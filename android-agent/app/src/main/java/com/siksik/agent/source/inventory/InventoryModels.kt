package com.siksik.agent.source.inventory

import android.net.Uri
import com.siksik.agent.source.communication.ContactRecordMetadata
import com.siksik.agent.source.communication.NotificationRecordMetadata
import com.siksik.agent.source.communication.SmsRecordMetadata
import com.siksik.agent.source.communication.VisibleUiRecordMetadata

enum class InventoryMode(val wireName: String) {
    QUICK("quick"),
    FULL("full"),
}

enum class InventoryRunState(val wireName: String) {
    READY("ready"),
    CRAWLING("crawling"),
    COMPLETE("complete"),
    PARTIAL("partial"),
    CANCELLED("cancelled"),
    FAILED("failed"),
}

enum class InventorySourceState(val wireName: String) {
    PENDING("pending"),
    CRAWLING("crawling"),
    COMPLETE("complete"),
    PARTIAL("partial"),
    DENIED("denied"),
    RESTRICTED("restricted"),
    UNSUPPORTED("unsupported"),
    CANCELLED("cancelled"),
    FAILED("failed"),
}

enum class SourceAdapter(val wireName: String) {
    PUBLIC_WHATSAPP("public_whatsapp"),
    PUBLIC_TELEGRAM("public_telegram"),
    MEDIA_IMAGE("media_store_image"),
    MEDIA_VIDEO("media_store_video"),
    MEDIA_AUDIO("media_store_audio"),
    DOCUMENT_SHARED("shared_storage_document"),
    DOCUMENT_TREE("document_tree"),
    SMS("sms_content_provider"),
    CONTACT("contacts_content_provider"),
    VISIBLE_UI("accessibility_visible_ui"),
    NOTIFICATION("notification_listener"),
}

enum class InventorySourceKind(val wireName: String) {
    MEDIA_IMAGE("media_image"),
    MEDIA_VIDEO("media_video"),
    MEDIA_AUDIO("media_audio"),
    DOCUMENT("document"),
    SMS("sms"),
    CONTACT("contact"),
    VISIBLE_UI("visible_ui"),
    NOTIFICATION("notification"),
}

data class ExifMetadata(
    val state: String,
    val orientation: Int?,
    val cameraMake: String?,
    val cameraModel: String?,
    val lensModel: String?,
    val exposureTime: String?,
    val aperture: Double?,
    val focalLength: Double?,
    val iso: Int?,
    val latitude: Double?,
    val longitude: Double?,
    val altitude: Double?,
    val capturedAtEpochMs: Long?,
    val warningCodes: List<String>,
)

data class InventoryRecord(
    val recordId: String,
    val identityHash: String,
    val dedupeHash: String,
    val sourceKind: InventorySourceKind,
    val sourceAdapter: SourceAdapter,
    val sourceApp: String?,
    val sourceLocator: String,
    val displayName: String,
    val mimeType: String,
    val sizeBytes: Long?,
    val width: Int?,
    val height: Int?,
    val durationMs: Long?,
    val dateTakenEpochMs: Long?,
    val dateAddedEpochMs: Long?,
    val dateModifiedEpochMs: Long?,
    val captureTimeEpochMs: Long?,
    val captureTimeSource: String,
    val directoryHint: String?,
    val exif: ExifMetadata?,
    val warningCodes: List<String>,
    val thumbnailAvailable: Boolean,
    val observedAtEpochMs: Long,
    val contentUri: Uri?,
    val normalizedText: String? = null,
    val contentSha256: String? = null,
    val smsMetadata: SmsRecordMetadata? = null,
    val contactMetadata: ContactRecordMetadata? = null,
    val visibleUiMetadata: VisibleUiRecordMetadata? = null,
    val notificationMetadata: NotificationRecordMetadata? = null,
    val attachmentIds: List<String> = emptyList(),
)

data class SourceAvailability(
    val state: InventorySourceState,
    val reason: String? = null,
)

data class AdapterPage(
    val records: List<InventoryRecord>,
    val nextCheckpoint: String?,
    val scannedCount: Int,
    val terminalState: InventorySourceState = InventorySourceState.COMPLETE,
    val terminalReason: String? = null,
)

data class InventoryPage(
    val crawlId: String,
    val source: SourceAdapter,
    val records: List<InventoryRecord>,
    val nextCursor: String?,
    val sourceState: InventorySourceState,
    val sourceReason: String?,
    val sampled: Boolean,
    val scannedCount: Int,
    val discoveredCount: Int,
    val duplicateCount: Int,
)

data class InventorySourceProgress(
    val source: SourceAdapter,
    val state: InventorySourceState,
    val scannedCount: Int,
    val discoveredCount: Int,
    val duplicateCount: Int,
    val sampled: Boolean,
    val reason: String?,
    val resumeCursor: String?,
)

data class InventoryRun(
    val crawlId: String,
    val sessionId: String,
    val mode: InventoryMode,
    val state: InventoryRunState,
    val documentGrantId: String?,
    val startedAtEpochMs: Long,
    val updatedAtEpochMs: Long,
    val completedAtEpochMs: Long?,
    val sources: List<InventorySourceProgress>,
)

interface InventorySource {
    val adapter: SourceAdapter

    fun availability(sessionId: String, documentGrantId: String?): SourceAvailability

    fun page(
        sessionId: String,
        documentGrantId: String?,
        checkpoint: String?,
        limit: Int,
        timeScope: InventoryTimeScope = InventoryTimeScope.UNBOUNDED,
        isCancelled: () -> Boolean,
    ): AdapterPage
}
