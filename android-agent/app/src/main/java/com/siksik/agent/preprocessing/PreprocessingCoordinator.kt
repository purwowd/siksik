package com.siksik.agent.preprocessing

import android.app.ActivityManager
import android.content.Context
import android.os.Build
import android.os.PowerManager
import android.os.StatFs
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.source.inventory.InventoryMode
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class PreprocessingCoordinator(
    context: Context,
    private val store: PreprocessingStore,
    private val clock: () -> Long = System::currentTimeMillis,
    private val processor: PreprocessingProcessor = defaultProcessor(context),
    private val resources: ResourcePolicy = DeviceResourcePolicy(context),
    private val itemTimeoutMs: Long = BuildConfig.PREPROCESS_ITEM_TIMEOUT_MS,
    private val documentItemTimeoutMs: Long = BuildConfig.PREPROCESS_DOCUMENT_TIMEOUT_MS,
    private val sessionDeadlineMs: Long = BuildConfig.PREPROCESS_SESSION_DEADLINE_MS,
    private val fullSessionDeadlineMs: Long = BuildConfig.PREPROCESS_FULL_SESSION_DEADLINE_MS,
    private val resourceRecheckMs: Long = DEFAULT_RESOURCE_RECHECK_MS,
) : AutoCloseable {
    private val controlExecutor = Executors.newSingleThreadExecutor(
        NamedThreadFactory("siksik-preprocess-control"),
    )
    private val workerCount = minOf(
        BuildConfig.MAX_PREPROCESS_CONCURRENCY,
        (Runtime.getRuntime().availableProcessors() / 2).coerceAtLeast(1),
    )
    private val workerExecutor: ExecutorService = Executors.newFixedThreadPool(
        workerCount,
        NamedThreadFactory("siksik-preprocess-worker"),
    )
    private val tasks = ConcurrentHashMap<String, Future<*>>()

    @Volatile
    private var closed = false

    init {
        require(itemTimeoutMs > 0)
        require(documentItemTimeoutMs >= itemTimeoutMs)
        require(sessionDeadlineMs > 0)
        require(fullSessionDeadlineMs >= sessionDeadlineMs)
        require(resourceRecheckMs > 0)
    }

    fun start(
        sessionId: String,
        crawlId: String,
        mode: InventoryMode = InventoryMode.QUICK,
    ): PreprocessingRun {
        check(!closed) { "preprocessing_coordinator_closed" }
        val now = clock()
        val deadlineBudget = when (mode) {
            InventoryMode.QUICK -> sessionDeadlineMs
            InventoryMode.FULL -> fullSessionDeadlineMs
        }
        val run = store.startOrResume(
            sessionId,
            crawlId,
            now,
            now + deadlineBudget,
        )
        if (run.state !in TERMINAL_STATES) {
            tasks.compute(crawlId) { _, existing ->
                if (existing == null || existing.isDone) {
                    controlExecutor.submit { execute(sessionId, crawlId, mode) }
                } else {
                    existing
                }
            }
        }
        return store.getRun(sessionId, crawlId)
    }

    fun status(sessionId: String, crawlId: String): PreprocessingRun =
        store.getRun(sessionId, crawlId)

    fun capabilities(): Map<String, EngineCapability> = processor.capabilities()

    fun cancel(sessionId: String, crawlId: String): PreprocessingRun {
        val run = store.requestCancel(sessionId, crawlId, clock())
        tasks[crawlId]?.cancel(true)
        return run
    }

    fun records(
        sessionId: String,
        crawlId: String,
        cursor: String?,
        limit: Int,
    ): PreprocessedRecordPage = store.recordPage(
        sessionId,
        crawlId,
        cursor,
        limit,
        clock(),
    )

    fun clearSession(sessionId: String) {
        store.clearSession(sessionId)
    }

    override fun close() {
        if (closed) return
        closed = true
        tasks.values.forEach { it.cancel(true) }
        controlExecutor.shutdownNow()
        workerExecutor.shutdownNow()
        controlExecutor.awaitTermination(CLOSE_WAIT_SECONDS, TimeUnit.SECONDS)
        workerExecutor.awaitTermination(CLOSE_WAIT_SECONDS, TimeUnit.SECONDS)
        processor.close()
    }

    private fun execute(sessionId: String, crawlId: String, mode: InventoryMode) {
        val partialReasons = mutableSetOf<String>()
        try {
            while (!closed) {
                val run = store.getRun(sessionId, crawlId)
                if (store.isCancelRequested(crawlId) || Thread.currentThread().isInterrupted) {
                    store.markRemaining(
                        sessionId,
                        crawlId,
                        PreprocessingRecordState.CANCELLED,
                        "cancelled",
                        clock(),
                    )
                    store.finishRun(
                        sessionId,
                        crawlId,
                        PreprocessingRunState.CANCELLED,
                        listOf("cancelled"),
                        clock(),
                    )
                    return
                }
                if (clock() >= run.deadlineAtEpochMs) {
                    partialReasons.add("preprocessing_deadline_exceeded")
                    store.markRemaining(
                        sessionId,
                        crawlId,
                        PreprocessingRecordState.SKIPPED,
                        "preprocessing_deadline_exceeded",
                        clock(),
                    )
                    break
                }
                val pauseReason = resources.pauseReason()
                if (pauseReason != null) {
                    TimeUnit.MILLISECONDS.sleep(resourceRecheckMs)
                    continue
                }
                val batch = store.claimPending(
                    crawlId,
                    workerCount,
                    clock(),
                )
                if (batch.isEmpty()) break
                processBatch(batch, run.deadlineAtEpochMs, mode)
            }
            applyClusters(crawlId, partialReasons)
            store.releaseSelectionInputs(crawlId, clock())
            val totals = store.getRun(sessionId, crawlId).totals
            if (totals.failed > 0) partialReasons.add("preprocessing_items_failed")
            if (totals.truncated > 0) partialReasons.add("preprocessing_items_truncated")
            if (totals.skipped > 0) partialReasons.add("preprocessing_items_skipped")
            val finalState = if (
                partialReasons.isNotEmpty() ||
                totals.failed > 0 ||
                totals.truncated > 0 ||
                totals.skipped > 0
            ) {
                PreprocessingRunState.PARTIAL
            } else {
                PreprocessingRunState.COMPLETE
            }
            store.finishRun(sessionId, crawlId, finalState, partialReasons.toList(), clock())
            Log.i(
                LOG_TAG,
                "event=preprocessing_completed crawl_id=$crawlId state=${finalState.wireName} " +
                    "total=${totals.total} completed=${totals.completed} " +
                    "truncated=${totals.truncated} failed=${totals.failed}",
            )
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            store.markRemaining(
                sessionId,
                crawlId,
                PreprocessingRecordState.CANCELLED,
                "cancelled",
                clock(),
            )
            store.finishRun(
                sessionId,
                crawlId,
                PreprocessingRunState.CANCELLED,
                listOf("cancelled"),
                clock(),
            )
        } catch (exception: RuntimeException) {
            Log.e(
                LOG_TAG,
                "event=preprocessing_failed crawl_id=$crawlId " +
                    "exception_type=${exception.javaClass.simpleName}",
            )
            store.markRemaining(
                sessionId,
                crawlId,
                PreprocessingRecordState.SKIPPED,
                "preprocessing_internal_failure",
                clock(),
            )
            store.finishRun(
                sessionId,
                crawlId,
                PreprocessingRunState.FAILED,
                listOf("preprocessing_internal_failure"),
                clock(),
            )
        } finally {
            tasks.remove(crawlId)
        }
    }

    private fun processBatch(
        batch: List<StoredPreprocessRecord>,
        deadlineAt: Long,
        mode: InventoryMode,
    ) {
        val submitted = batch.map { record ->
            val cancellation = CancellationToken {
                closed ||
                    Thread.currentThread().isInterrupted ||
                    store.isCancelRequested(record.crawlId) ||
                    clock() >= deadlineAt
            }
            SubmittedRecord(
                record,
                clock(),
                if (mode == InventoryMode.FULL && record.sourceKind == "document") {
                    documentItemTimeoutMs
                } else {
                    itemTimeoutMs
                },
                workerExecutor.submit<PreprocessingRecordUpdate> {
                    processor.process(record, cancellation)
                },
            )
        }
        try {
            submitted.forEach { item ->
                val elapsed = (clock() - item.submittedAt).coerceAtLeast(0L)
                val remaining = (item.timeoutMs - elapsed).coerceAtLeast(1L)
                try {
                    val update = item.future.get(remaining, TimeUnit.MILLISECONDS)
                    store.completeRecord(
                        item.record.crawlId,
                        item.record.recordId,
                        update,
                        clock(),
                    )
                } catch (_: TimeoutException) {
                    item.future.cancel(true)
                    store.markTimedOut(item.record.crawlId, item.record.recordId, clock())
                } catch (_: ExecutionException) {
                    store.completeRecord(
                        item.record.crawlId,
                        item.record.recordId,
                        failedUpdate("preprocessing_item_failed"),
                        clock(),
                    )
                }
            }
        } catch (exception: InterruptedException) {
            submitted.forEach { it.future.cancel(true) }
            throw exception
        }
    }

    private fun applyClusters(crawlId: String, partialReasons: MutableSet<String>) {
        val values = store.clusteringSignals(crawlId)
        val duplicateMembership = try {
            DuplicateClusterer().cluster(
                values.map { value ->
                    DuplicateSignal(
                        value.recordId,
                        value.exactSha256,
                        value.perceptualHash,
                        value.width?.toLong()?.times(value.height?.toLong() ?: 0L) ?: 0L,
                        value.sizeBytes ?: 0L,
                    )
                },
            ).associateBy(DuplicateMembership::recordId)
        } catch (_: IllegalArgumentException) {
            partialReasons.add("duplicate_signal_limit_exceeded")
            emptyMap()
        }
        val faceSignals = values.flatMap(::faceSignals)
        val faceMembership = try {
            AnonymousFaceClusterer().cluster(faceSignals)
                .associateBy(FaceClusterMembership::recordId)
        } catch (_: IllegalArgumentException) {
            partialReasons.add("face_signal_limit_exceeded")
            emptyMap()
        }
        store.applyMemberships(
            crawlId,
            duplicateMembership,
            faceMembership,
            clock(),
        )
    }

    private fun faceSignals(value: StoredClusterSignals): List<FaceSignal> {
        val encoded = value.faceVectorsJson ?: return emptyList()
        val array = org.json.JSONArray(encoded)
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.getJSONObject(index)
                val vectorJson = item.getJSONArray("vector")
                if (vectorJson.length() !in 1..4096) continue
                val vector = FloatArray(vectorJson.length()) { position ->
                    vectorJson.getDouble(position).toFloat()
                }
                if (vector.any { !it.isFinite() }) continue
                add(
                    FaceSignal(
                        value.recordId,
                        item.getInt("face_index"),
                        item.getDouble("confidence").toFloat(),
                        item.getLong("area"),
                        vector,
                    ),
                )
            }
        }
    }

    private data class SubmittedRecord(
        val record: StoredPreprocessRecord,
        val submittedAt: Long,
        val timeoutMs: Long,
        val future: Future<PreprocessingRecordUpdate>,
    )

    companion object {
        private const val LOG_TAG = "SIKSIKAgent"
        private const val DEFAULT_RESOURCE_RECHECK_MS = 500L
        private const val CLOSE_WAIT_SECONDS = 5L
        private val TERMINAL_STATES = setOf(
            PreprocessingRunState.COMPLETE,
            PreprocessingRunState.PARTIAL,
            PreprocessingRunState.CANCELLED,
            PreprocessingRunState.FAILED,
        )

        private fun defaultProcessor(context: Context): RecordPreprocessor {
            val registry = ModelAssetRegistry.from(context)
            val ocr = MlKitTextOcrPreprocessor()
            return RecordPreprocessor(
                AndroidPreprocessInputFactory(context),
                ocr,
                BoundedDocumentTextPreprocessor(ocr),
                StreamingExactHashPreprocessor(),
                DifferenceHashPreprocessor(),
                MediaPipeFaceEmbeddingPreprocessor(context, registry),
                MediaPipeObjectDetectionPreprocessor(context, registry),
            )
        }

        private fun failedUpdate(reason: String): PreprocessingRecordUpdate =
            PreprocessingRecordUpdate(
                PreprocessingRecordState.FAILED,
                org.json.JSONObject()
                    .put("schema_version", 1)
                    .put("status", "failed")
                    .put("warnings", org.json.JSONArray(listOf(reason)))
                    .toString(),
                null,
                null,
                null,
                null,
            )
    }
}

fun interface ResourcePolicy {
    fun pauseReason(): String?
}

class DeviceResourcePolicy(context: Context) : ResourcePolicy {
    private val applicationContext = context.applicationContext
    private val activityManager = applicationContext.getSystemService(ActivityManager::class.java)
    private val powerManager = applicationContext.getSystemService(PowerManager::class.java)

    override fun pauseReason(): String? {
        val memory = ActivityManager.MemoryInfo()
        activityManager.getMemoryInfo(memory)
        if (memory.lowMemory) return "preprocessing_low_memory_pause"
        if (
            Build.VERSION.SDK_INT >= 29 &&
            powerManager.currentThermalStatus >= PowerManager.THERMAL_STATUS_SEVERE
        ) {
            return "preprocessing_thermal_pause"
        }
        val storage = StatFs(applicationContext.filesDir.absolutePath).availableBytes
        if (storage < MIN_AVAILABLE_STORAGE_BYTES) return "preprocessing_low_storage_pause"
        return null
    }

    companion object {
        private const val MIN_AVAILABLE_STORAGE_BYTES = 64L * 1024L * 1024L
    }
}

private class NamedThreadFactory(private val prefix: String) : java.util.concurrent.ThreadFactory {
    private val sequence = java.util.concurrent.atomic.AtomicInteger()

    override fun newThread(task: Runnable): Thread = Thread(
        task,
        "$prefix-${sequence.incrementAndGet()}",
    ).apply {
        isDaemon = true
        priority = Thread.NORM_PRIORITY - 1
    }
}
