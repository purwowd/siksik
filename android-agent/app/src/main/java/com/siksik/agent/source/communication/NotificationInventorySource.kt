package com.siksik.agent.source.communication

import android.content.Context
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

class NotificationInventorySource(
    context: Context,
    private val store: CommunicationCaptureStore,
    private val notificationEnabled: () -> Boolean = {
        CommunicationAccess.notificationListenerEnabled(context)
    },
) : InventorySource {
    override val adapter = SourceAdapter.NOTIFICATION

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability =
        if (notificationEnabled()) {
            SourceAvailability(InventorySourceState.PENDING)
        } else {
            SourceAvailability(InventorySourceState.DENIED, "notification_access_not_granted")
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
            throw ApiException("validation_error", "Batas halaman notifikasi tidak valid.", 422)
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
        if (!notificationEnabled()) {
            return AdapterPage(
                emptyList(),
                checkpoint,
                0,
                InventorySourceState.PARTIAL,
                "notification_access_revoked",
            )
        }
        val after = decodeCheckpoint(checkpoint)
        val stored = store.notificationPage(crawl.crawlId, after, limit)
        val records = stored.map { value ->
            val identityHash = CommunicationPolicy.identityHash(
                "notification",
                value.notificationIdentity,
            )
            val normalized = CommunicationPolicy.joinedText(
                listOf(value.title, value.text, value.subText, value.bigText) + value.textLines,
                BuildConfig.MAX_SMS_TEXT_LENGTH,
            )
            InventoryRecord(
                recordId = value.recordId,
                identityHash = identityHash,
                dedupeHash = identityHash,
                sourceKind = InventorySourceKind.NOTIFICATION,
                sourceAdapter = adapter,
                sourceApp = value.packageName,
                sourceLocator = CommunicationPolicy.sourceLocator(
                    "notification",
                    value.notificationIdentity,
                ),
                displayName = "Notification",
                mimeType = "application/vnd.siksik.notification+json",
                sizeBytes = normalized?.toByteArray(Charsets.UTF_8)?.size?.toLong(),
                width = null,
                height = null,
                durationMs = null,
                dateTakenEpochMs = null,
                dateAddedEpochMs = null,
                dateModifiedEpochMs = value.removedAt,
                captureTimeEpochMs = value.postTime,
                captureTimeSource = "source_timestamp",
                directoryHint = null,
                exif = null,
                warningCodes = emptyList(),
                thumbnailAvailable = false,
                observedAtEpochMs = value.observedAt,
                contentUri = null,
                normalizedText = normalized,
                contentSha256 = value.contentHash,
                notificationMetadata = NotificationRecordMetadata(
                    packageName = value.packageName,
                    notificationIdentity = value.notificationIdentity,
                    title = value.title,
                    text = value.text,
                    subText = value.subText,
                    bigText = value.bigText,
                    textLines = value.textLines,
                    category = value.category,
                    channelId = value.channelId,
                    postTimeEpochMs = value.postTime,
                    removedAtEpochMs = value.removedAt,
                    updateCount = value.updateCount,
                ),
            )
        }.filter(timeScope::includes)
        val hasMore = stored.size >= limit
        val next = stored.lastOrNull()?.rowId?.toString().takeIf { hasMore }
        val issue = store.session(crawl.crawlId)?.notificationReason
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
            ?: throw ApiException("invalid_cursor", "Checkpoint notifikasi tidak valid.", 422)
    }
}
