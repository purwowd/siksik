package com.siksik.agent.source.communication

import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.source.inventory.AdapterPage
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventorySource
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.InventorySourceState
import com.siksik.agent.source.inventory.InventoryTimeScope
import com.siksik.agent.source.inventory.SourceAdapter
import com.siksik.agent.source.inventory.SourceAvailability

class VisibleUiInventorySource(
    private val store: CommunicationCaptureStore,
) : InventorySource {
    override val adapter = SourceAdapter.VISIBLE_UI

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability {
        val active = store.activeSession()
        return when {
            active == null || active.sessionId != sessionId ->
                SourceAvailability(InventorySourceState.FAILED, "capture_session_unavailable")
            active.targetPackages.isEmpty() ->
                SourceAvailability(InventorySourceState.RESTRICTED, "social_target_allowlist_empty")
            else -> SourceAvailability(InventorySourceState.PENDING)
        }
    }

    override fun page(
        sessionId: String,
        documentGrantId: String?,
        checkpoint: String?,
        limit: Int,
        timeScope: InventoryTimeScope,
        isCancelled: () -> Boolean,
    ): AdapterPage {
        if (limit !in 1..BuildConfig.MAX_INVENTORY_PAGE_SIZE) {
            throw ApiException("validation_error", "Batas halaman visible UI tidak valid.", 422)
        }
        val crawl = store.activeSession() ?: store.sessionForSession(sessionId)
            ?: return AdapterPage(
                emptyList(),
                checkpoint,
                0,
                InventorySourceState.FAILED,
                "capture_session_unavailable",
            )
        if (isCancelled()) {
            return AdapterPage(
                emptyList(),
                checkpoint,
                0,
                InventorySourceState.CANCELLED,
                "crawl_cancelled",
            )
        }
        val after = decodeCheckpoint(checkpoint)
        val stored = store.visiblePage(crawl.crawlId, after, limit)
        val records = stored.map { value ->
            val identityHash = CommunicationPolicy.identityHash("visible_ui", value.recordId)
            InventoryRecord(
                recordId = value.recordId,
                identityHash = identityHash,
                dedupeHash = identityHash,
                sourceKind = InventorySourceKind.VISIBLE_UI,
                sourceAdapter = adapter,
                sourceApp = value.packageName,
                sourceLocator = CommunicationPolicy.sourceLocator("visible_ui", value.recordId),
                displayName = "Visible UI",
                mimeType = "application/vnd.siksik.visible-ui+json",
                sizeBytes = value.normalizedText?.toByteArray(Charsets.UTF_8)?.size?.toLong(),
                width = null,
                height = null,
                durationMs = null,
                dateTakenEpochMs = null,
                dateAddedEpochMs = null,
                dateModifiedEpochMs = null,
                captureTimeEpochMs = value.eventTime.takeIf { it > 0 },
                captureTimeSource = if (value.eventTime > 0) "source_timestamp" else "unknown",
                directoryHint = null,
                exif = null,
                warningCodes = emptyList(),
                thumbnailAvailable = value.screenshotIds.isNotEmpty(),
                observedAtEpochMs = value.observedAt,
                contentUri = null,
                normalizedText = value.normalizedText,
                contentSha256 = value.contentHash,
                visibleUiMetadata = VisibleUiRecordMetadata(
                    packageName = value.packageName,
                    socialScope = value.socialScope,
                    windowId = value.windowId,
                    activityContext = value.activityContext,
                    eventType = value.eventType,
                    screenSequence = value.screenSequence,
                    nodes = value.nodes,
                    screenshotIds = value.screenshotIds,
                    profileLinks = value.profileLinks,
                ),
                attachmentIds = value.screenshotIds,
            )
        }.filter(timeScope::includes)
        val hasMore = stored.size >= limit
        val next = stored.lastOrNull()?.rowId?.toString().takeIf { hasMore }
        val session = store.session(crawl.crawlId)
        val automationResultCount = store.automationResultCount(crawl.crawlId)
        val targetResultsMissing = automationResultCount < crawl.targetPackages.size
        val issue = store.automationIssue(crawl.crawlId)
            ?: "automation_result_missing".takeIf { targetResultsMissing }
            ?: session?.accessibilityReason?.takeIf { automationResultCount == 0 }
        return AdapterPage(
            records,
            next,
            stored.size,
            terminalState = if (!hasMore && issue != null) {
                InventorySourceState.PARTIAL
            } else {
                InventorySourceState.COMPLETE
            },
            terminalReason = issue.takeIf { !hasMore },
        )
    }

    private fun decodeCheckpoint(value: String?): Long {
        if (value == null) return 0
        return value.toLongOrNull()?.takeIf { it >= 0 }
            ?: throw ApiException("invalid_cursor", "Checkpoint visible UI tidak valid.", 422)
    }
}
