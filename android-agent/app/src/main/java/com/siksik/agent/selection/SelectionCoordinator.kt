package com.siksik.agent.selection

import android.util.Log
import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.PreprocessingStore
import com.siksik.agent.preprocessing.PreprocessingRunState
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit

class SelectionCoordinator(
    private val preprocessingStore: PreprocessingStore,
    private val selectionStore: SelectionStore,
    private val clock: () -> Long = System::currentTimeMillis,
) : AutoCloseable {
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "siksik-selection").apply { isDaemon = true }
    }
    private val tasks = ConcurrentHashMap<String, Future<*>>()

    @Volatile
    private var configured: ConfiguredPolicy? = null

    @Volatile
    private var closed = false

    @Synchronized
    fun configure(policy: SelectionPolicy, reviewCandidates: Boolean) {
        check(!closed) { "selection_coordinator_closed" }
        val current = configured
        if (
            current != null &&
            (
                current.policy.policyFingerprint != policy.policyFingerprint ||
                    current.reviewCandidates != reviewCandidates
                )
        ) {
            throw ApiException(
                "selection_policy_mismatch",
                "Policy selection sesi aktif tidak dapat diganti.",
                409,
            )
        }
        configured = ConfiguredPolicy(policy, reviewCandidates)
    }

    fun start(
        sessionId: String,
        crawlId: String,
        policyFingerprint: String,
        reviewCandidates: Boolean,
    ): SelectionRun {
        check(!closed) { "selection_coordinator_closed" }
        val config = requireConfigured(policyFingerprint, reviewCandidates)
        val preprocessing = preprocessingStore.getRun(sessionId, crawlId)
        preprocessingStore.resetClaimedSelectionInputs(sessionId, crawlId, clock())
        val run = selectionStore.start(
            sessionId,
            crawlId,
            config.policy,
            reviewCandidates,
            preprocessing.totals.total,
            clock(),
        )
        if (run.state == SelectionRunState.RUNNING) {
            tasks.compute(crawlId) { _, existing ->
                if (existing == null || existing.isDone) {
                    executor.submit { execute(sessionId, crawlId, config.policy) }
                } else {
                    existing
                }
            }
        }
        return selectionStore.getRun(sessionId, crawlId)
    }

    fun status(sessionId: String, crawlId: String): SelectionRun =
        selectionStore.getRun(sessionId, crawlId)

    fun candidates(
        sessionId: String,
        crawlId: String,
        afterRecordId: String?,
        limit: Int,
    ): SelectionCandidatePage = selectionStore.page(
        sessionId,
        crawlId,
        afterRecordId,
        limit,
    )

    fun liveSelectedRecords(
        sessionId: String,
        crawlId: String,
        afterSequence: Long,
        limit: Int,
    ): LiveSelectedRecordPage {
        val page = selectionStore.liveSelectedPage(
            sessionId,
            crawlId,
            afterSequence,
            limit,
        )
        return LiveSelectedRecordPage(
            page.records.map { value ->
                LiveSelectedRecord(
                    value.sequence,
                    value.candidate,
                    preprocessingStore.transferRecord(
                        sessionId,
                        crawlId,
                        value.candidate.recordId,
                    ).payload,
                )
            },
            page.nextSequence,
        )
    }

    fun mutate(
        sessionId: String,
        crawlId: String,
        recordId: String,
        expectedRevision: Int,
        override: HumanOverride,
        operatorId: String,
    ): SelectionMutation = selectionStore.mutate(
        sessionId,
        crawlId,
        recordId,
        expectedRevision,
        override,
        operatorId,
        clock(),
    )

    fun confirm(
        sessionId: String,
        crawlId: String,
        expectedRevision: Int,
    ): SelectionRun = selectionStore.confirm(
        sessionId,
        crawlId,
        expectedRevision,
        clock(),
    )

    fun cancel(sessionId: String, crawlId: String): SelectionRun {
        val run = selectionStore.cancel(sessionId, crawlId, clock())
        tasks[crawlId]?.cancel(true)
        return run
    }

    fun clearSession(sessionId: String) {
        selectionStore.clearSession(sessionId)
    }

    override fun close() {
        if (closed) return
        closed = true
        tasks.values.forEach { it.cancel(true) }
        executor.shutdownNow()
        executor.awaitTermination(3, TimeUnit.SECONDS)
        selectionStore.close()
    }

    private fun execute(sessionId: String, crawlId: String, policy: SelectionPolicy) {
        val scorer = SelectionScorer(policy, clock)
        try {
            while (!closed) {
                if (
                    Thread.currentThread().isInterrupted ||
                    selectionStore.isCancelRequested(crawlId)
                ) {
                    selectionStore.cancel(sessionId, crawlId, clock())
                    return
                }
                val records = preprocessingStore.claimSelectionInputs(
                    sessionId,
                    crawlId,
                    INPUT_PAGE_SIZE,
                    clock(),
                )
                if (records.isEmpty()) {
                    when (preprocessingStore.getRun(sessionId, crawlId).state) {
                        PreprocessingRunState.RUNNING -> {
                            TimeUnit.MILLISECONDS.sleep(INPUT_POLL_MS)
                            continue
                        }
                        PreprocessingRunState.COMPLETE,
                        PreprocessingRunState.PARTIAL,
                        -> {
                            preprocessingStore.releaseSelectionInputs(crawlId, clock())
                            if (preprocessingStore.selectionInputsRemaining(sessionId, crawlId) > 0) {
                                TimeUnit.MILLISECONDS.sleep(INPUT_POLL_MS)
                                continue
                            }
                            break
                        }
                        PreprocessingRunState.CANCELLED -> {
                            selectionStore.cancel(sessionId, crawlId, clock())
                            return
                        }
                        PreprocessingRunState.FAILED -> {
                            selectionStore.fail(
                                sessionId,
                                crawlId,
                                "preprocessing_failed",
                                clock(),
                            )
                            return
                        }
                    }
                }
                val evaluations = records.map(scorer::evaluate)
                selectionStore.appendEvaluations(
                    sessionId,
                    crawlId,
                    evaluations,
                    clock(),
                )
                preprocessingStore.markSelectionInputsEvaluated(
                    sessionId,
                    crawlId,
                    records.map { it.recordId },
                    clock(),
                )
            }
            if (!closed && !selectionStore.isCancelRequested(crawlId)) {
                val completed = selectionStore.freeze(sessionId, crawlId, policy, clock())
                Log.i(
                    LOG_TAG,
                    "event=selection_completed crawl_id=$crawlId state=${completed.state.wireName} " +
                        "evaluated=${completed.totals.evaluated} selected=${completed.totals.selected} " +
                        "revision=${completed.revision}",
                )
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            selectionStore.cancel(sessionId, crawlId, clock())
        } catch (exception: RuntimeException) {
            Log.e(
                LOG_TAG,
                "event=selection_failed crawl_id=$crawlId " +
                    "exception_type=${exception.javaClass.simpleName}",
            )
            selectionStore.fail(sessionId, crawlId, "selection_processing_failed", clock())
        } finally {
            tasks.remove(crawlId)
        }
    }

    private fun requireConfigured(
        policyFingerprint: String,
        reviewCandidates: Boolean,
    ): ConfiguredPolicy {
        val config = configured ?: throw ApiException(
            "selection_policy_missing",
            "Policy selection belum dikonfigurasi.",
            409,
        )
        if (
            config.policy.policyFingerprint != policyFingerprint ||
            config.reviewCandidates != reviewCandidates
        ) {
            throw ApiException(
                "selection_policy_mismatch",
                "Policy selection tidak sesuai sesi aktif.",
                409,
            )
        }
        return config
    }

    private data class ConfiguredPolicy(
        val policy: SelectionPolicy,
        val reviewCandidates: Boolean,
    )

    companion object {
        private const val INPUT_PAGE_SIZE = 100
        private const val INPUT_POLL_MS = 100L
        private const val LOG_TAG = "SIKSIKAgent"
    }
}

data class LiveSelectedRecord(
    val sequence: Long,
    val candidate: SelectionCandidate,
    val payload: org.json.JSONObject,
)

data class LiveSelectedRecordPage(
    val records: List<LiveSelectedRecord>,
    val nextSequence: Long?,
)
