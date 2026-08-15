package com.siksik.agent.source.inventory

import android.content.Context
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.preprocessing.InventoryRecordSink
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.ContactInventorySource
import com.siksik.agent.source.communication.NotificationInventorySource
import com.siksik.agent.source.communication.SmsInventorySource
import com.siksik.agent.source.communication.VisibleUiInventorySource
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class InventoryController(
    context: Context,
    grants: GrantGateway,
    private val store: InventoryStateStore = InventoryStateStore(context),
    private val communicationStore: CommunicationCaptureStore =
        CommunicationCaptureStore(context),
    private val recordSink: InventoryRecordSink = InventoryRecordSink.NONE,
    sources: List<InventorySource> = defaultSources(
        context.applicationContext,
        grants,
        communicationStore,
    ),
    private val clock: () -> Long = System::currentTimeMillis,
) : AutoCloseable {
    private val sources = sources.associateBy(InventorySource::adapter)
    private val sourceLocks = ConcurrentHashMap<String, Any>()

    init {
        require(this.sources.keys == SourceAdapter.entries.toSet()) {
            "inventory source registry is incomplete"
        }
    }

    fun start(
        sessionId: String,
        mode: InventoryMode,
        documentGrantId: String?,
        targetPackages: Set<String> = emptySet(),
    ): InventoryRun {
        val validatedTargets = CommunicationPolicy.validateTargets(targetPackages)
        val latest = store.tryLatestForSession(sessionId)
        if (latest != null && latest.state in ACTIVE_RUN_STATES) {
            if (
                latest.mode == mode &&
                latest.documentGrantId == documentGrantId &&
                communicationStore.targetsForCrawl(latest.crawlId) == validatedTargets
            ) {
                return latest
            }
            throw ApiException("conflict", "Crawl inventory lain masih aktif.", 409)
        }
        val crawlId = "crawl_${UUID.randomUUID()}"
        communicationStore.beginSession(
            sessionId,
            crawlId,
            validatedTargets,
            clock(),
        )
        val availability = SourceAdapter.entries.map { adapter ->
            adapter to sources.getValue(adapter).availability(sessionId, documentGrantId)
        }
        val run = try {
            store.createRun(
                crawlId,
                sessionId,
                mode,
                documentGrantId,
                availability,
                clock(),
            )
        } catch (exception: RuntimeException) {
            communicationStore.clearCrawl(crawlId)
            throw exception
        }
        Log.i(
            LOG_TAG,
            "event=inventory_started crawl_id=$crawlId mode=${mode.wireName} " +
                "source_count=${availability.size}",
        )
        return run
    }

    fun latest(sessionId: String): InventoryRun = store.latestForSession(sessionId)

    fun status(sessionId: String, crawlId: String): InventoryRun =
        store.getRun(crawlId, sessionId)

    fun page(
        sessionId: String,
        crawlId: String,
        source: SourceAdapter,
        cursor: String?,
        requestedLimit: Int,
    ): InventoryPage {
        if (requestedLimit !in 1..BuildConfig.MAX_INVENTORY_PAGE_SIZE) {
            throw ApiException("validation_error", "Batas halaman inventory tidak valid.", 422)
        }
        val lock = sourceLocks.computeIfAbsent("$crawlId:${source.wireName}") { Any() }
        return synchronized(lock) {
            pageLocked(sessionId, crawlId, source, cursor, requestedLimit)
        }
    }

    fun cancel(sessionId: String, crawlId: String): InventoryRun {
        val now = clock()
        val run = store.requestCancel(crawlId, sessionId, now)
        communicationStore.finishSession(crawlId, now)
        return run
    }

    fun resume(sessionId: String, crawlId: String): InventoryRun {
        val now = clock()
        communicationStore.resumeSession(crawlId, now)
        return store.resume(crawlId, sessionId, now)
    }

    fun reportAutomationResult(
        sessionId: String,
        crawlId: String,
        result: com.siksik.agent.source.communication.AutomationTargetResult,
    ): InventoryRun {
        val run = store.getRun(crawlId, sessionId)
        if (run.state !in ACTIVE_RUN_STATES) {
            throw ApiException("conflict", "Crawl inventory tidak aktif.", 409)
        }
        if (result.targetPackage !in communicationStore.targetsForCrawl(crawlId)) {
            throw ApiException("validation_error", "Target automation tidak diizinkan.", 422)
        }
        communicationStore.recordAutomationResult(crawlId, result, clock())
        return store.getRun(crawlId, sessionId)
    }

    fun clearSession(sessionId: String) {
        store.clearSession(sessionId)
        communicationStore.clearSession(sessionId)
        sourceLocks.clear()
    }

    override fun close() {
        store.close()
        communicationStore.close()
    }

    private fun pageLocked(
        sessionId: String,
        crawlId: String,
        source: SourceAdapter,
        cursor: String?,
        requestedLimit: Int,
    ): InventoryPage {
        val run = store.getRun(crawlId, sessionId)
        val progress = run.sources.first { it.source == source }
        if (progress.state in UNAVAILABLE_SOURCE_STATES) {
            return emptyPage(crawlId, progress)
        }
        if (progress.state in FINISHED_SOURCE_STATES) {
            throw ApiException("conflict", "Sumber inventory sudah selesai.", 409)
        }
        if (run.state == InventoryRunState.CANCELLED || store.isCancellationRequested(crawlId)) {
            return emptyPage(
                crawlId,
                progress.copy(
                    state = InventorySourceState.CANCELLED,
                    reason = "crawl_cancelled",
                ),
            )
        }
        if (cursor != progress.resumeCursor) {
            throw ApiException("invalid_cursor", "Cursor inventory tidak sesuai status sumber.", 422)
        }
        val checkpoint = cursor?.let { store.resolveCursor(crawlId, source, it) }
        val remainingQuickRows = BuildConfig.QUICK_INVENTORY_ITEMS_PER_SOURCE -
            progress.scannedCount
        if (
            run.mode == InventoryMode.QUICK &&
            remainingQuickRows <= 0 &&
            progress.resumeCursor != null
        ) {
            store.finishPage(
                crawlId,
                source,
                InventorySourceState.COMPLETE,
                scannedDelta = 0,
                discoveredDelta = 0,
                duplicateDelta = 0,
                sampled = true,
                reason = QUICK_SAMPLE_REASON,
                resumeCursor = progress.resumeCursor,
                now = clock(),
            )
            return emptyPage(
                crawlId,
                store.sourceProgress(crawlId, source),
            )
        }
        val adapterLimit = if (run.mode == InventoryMode.QUICK) {
            minOf(requestedLimit, remainingQuickRows.coerceAtLeast(1))
        } else {
            requestedLimit
        }
        val timeScope = InventoryTimeScope.forRun(run.mode, run.startedAtEpochMs)
        store.startSource(crawlId, source, clock())
        val adapterPage = try {
            sources.getValue(source).page(
                sessionId,
                run.documentGrantId,
                checkpoint,
                adapterLimit,
                timeScope,
            ) { store.isCancellationRequested(crawlId) }
        } catch (exception: ApiException) {
            store.finishPage(
                crawlId,
                source,
                InventorySourceState.FAILED,
                scannedDelta = 0,
                discoveredDelta = 0,
                duplicateDelta = 0,
                sampled = false,
                reason = exception.code,
                resumeCursor = cursor,
                now = clock(),
            )
            throw exception
        } catch (exception: RuntimeException) {
            store.finishPage(
                crawlId,
                source,
                InventorySourceState.FAILED,
                scannedDelta = 0,
                discoveredDelta = 0,
                duplicateDelta = 0,
                sampled = false,
                reason = "source_adapter_failed",
                resumeCursor = cursor,
                now = clock(),
            )
            Log.e(
                LOG_TAG,
                "event=inventory_source_failed crawl_id=$crawlId source=${source.wireName} " +
                    "exception_type=${exception.javaClass.simpleName}",
            )
            throw ApiException(
                "source_adapter_failed",
                "Sumber inventory tidak dapat dibaca.",
                502,
                true,
            )
        }
        val accepted = ArrayList<InventoryRecord>(adapterPage.records.size)
        var duplicates = 0
        adapterPage.records.filter(timeScope::includes).forEach { record ->
            if (
                store.claimIdentity(
                    crawlId,
                    source,
                    record.dedupeHash,
                    record.recordId,
                )
            ) {
                accepted.add(record)
            } else {
                duplicates += 1
            }
        }
        val now = clock()
        try {
            recordSink.persist(sessionId, crawlId, accepted, now)
        } catch (exception: RuntimeException) {
            store.finishPage(
                crawlId,
                source,
                InventorySourceState.FAILED,
                adapterPage.scannedCount,
                0,
                duplicates,
                false,
                "preprocessing_ledger_failed",
                cursor,
                now,
            )
            throw ApiException(
                "preprocessing_ledger_failed",
                "Ledger preprocessing tidak dapat disimpan.",
                500,
                true,
            )
        }
        val nextCursor = adapterPage.nextCheckpoint?.let {
            store.registerCursor(crawlId, source, it, now)
        }
        val cancelled = store.isCancellationRequested(crawlId) ||
            adapterPage.terminalState == InventorySourceState.CANCELLED
        val quickLimitReached = run.mode == InventoryMode.QUICK &&
            nextCursor != null &&
            progress.scannedCount + adapterPage.scannedCount >=
            BuildConfig.QUICK_INVENTORY_ITEMS_PER_SOURCE
        val sourceState = when {
            cancelled -> InventorySourceState.CANCELLED
            adapterPage.terminalState != InventorySourceState.COMPLETE ->
                adapterPage.terminalState
            quickLimitReached -> InventorySourceState.COMPLETE
            nextCursor != null -> InventorySourceState.CRAWLING
            else -> InventorySourceState.COMPLETE
        }
        val reason = when {
            cancelled -> "crawl_cancelled"
            quickLimitReached -> QUICK_SAMPLE_REASON
            sourceState == InventorySourceState.CRAWLING -> null
            else -> adapterPage.terminalReason?.take(MAX_SOURCE_REASON_LENGTH)
        }
        val sampled = progress.sampled || quickLimitReached
        val resumeCursor = nextCursor ?: cursor.takeIf {
            sourceState in setOf(
                InventorySourceState.CANCELLED,
                InventorySourceState.PARTIAL,
                InventorySourceState.DENIED,
                InventorySourceState.RESTRICTED,
                InventorySourceState.FAILED,
            )
        }
        store.finishPage(
            crawlId,
            source,
            sourceState,
            adapterPage.scannedCount,
            accepted.size,
            duplicates,
            sampled,
            reason,
            resumeCursor,
            now,
        )
        val updated = store.getRun(crawlId, sessionId)
        if (updated.state !in ACTIVE_RUN_STATES) {
            communicationStore.finishSession(crawlId, now)
        }
        Log.i(
            LOG_TAG,
            "event=inventory_page_completed crawl_id=$crawlId source=${source.wireName} " +
                "state=${sourceState.wireName} scanned=${adapterPage.scannedCount} " +
                "discovered=${accepted.size} duplicates=$duplicates sampled=$sampled",
        )
        return InventoryPage(
            crawlId = crawlId,
            source = source,
            records = accepted,
            nextCursor = resumeCursor,
            sourceState = sourceState,
            sourceReason = reason,
            sampled = sampled,
            scannedCount = adapterPage.scannedCount,
            discoveredCount = accepted.size,
            duplicateCount = duplicates,
        )
    }

    private fun emptyPage(
        crawlId: String,
        progress: InventorySourceProgress,
    ) = InventoryPage(
        crawlId = crawlId,
        source = progress.source,
        records = emptyList(),
        nextCursor = progress.resumeCursor,
        sourceState = progress.state,
        sourceReason = progress.reason,
        sampled = progress.sampled,
        scannedCount = 0,
        discoveredCount = 0,
        duplicateCount = 0,
    )

    companion object {
        private const val LOG_TAG = "SIKSIKAgent"
        private const val QUICK_SAMPLE_REASON = "quick_sample_limit"
        private const val MAX_SOURCE_REASON_LENGTH = 128
        private val ACTIVE_RUN_STATES = setOf(
            InventoryRunState.READY,
            InventoryRunState.CRAWLING,
        )
        private val UNAVAILABLE_SOURCE_STATES = setOf(
            InventorySourceState.DENIED,
            InventorySourceState.RESTRICTED,
            InventorySourceState.UNSUPPORTED,
        )
        private val FINISHED_SOURCE_STATES = setOf(
            InventorySourceState.COMPLETE,
            InventorySourceState.PARTIAL,
            InventorySourceState.FAILED,
        )

        private fun defaultSources(
            context: Context,
            grants: GrantGateway,
            communicationStore: CommunicationCaptureStore,
        ): List<InventorySource> = listOf(
            MediaStoreInventorySource(context, SourceAdapter.PUBLIC_WHATSAPP),
            MediaStoreInventorySource(context, SourceAdapter.PUBLIC_TELEGRAM),
            MediaStoreInventorySource(context, SourceAdapter.MEDIA_IMAGE),
            MediaStoreInventorySource(context, SourceAdapter.MEDIA_VIDEO),
            MediaStoreInventorySource(context, SourceAdapter.MEDIA_AUDIO),
            MediaStoreInventorySource(context, SourceAdapter.DOCUMENT_SHARED),
            DocumentTreeInventorySource(context, grants),
            SmsInventorySource(context),
            ContactInventorySource(context),
            VisibleUiInventorySource(communicationStore),
            NotificationInventorySource(context, communicationStore),
        )
    }
}
