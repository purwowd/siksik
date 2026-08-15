package com.siksik.agent.source.communication

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.Telephony
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

class SmsInventorySource(
    context: Context,
    private val provider: SmsProviderGateway = AndroidSmsProviderGateway(context.contentResolver),
    private val clock: () -> Long = System::currentTimeMillis,
    private val permissionGranted: () -> Boolean = {
        context.checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED
    },
    private val telephonySupported: () -> Boolean = {
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)
    },
) : InventorySource {
    override val adapter = SourceAdapter.SMS

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability =
        when {
            !telephonySupported() ->
                SourceAvailability(InventorySourceState.UNSUPPORTED, "telephony_not_supported")
            permissionGranted() -> SourceAvailability(InventorySourceState.PENDING)
            else -> SourceAvailability(InventorySourceState.DENIED, "read_sms_not_granted")
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
            throw ApiException("validation_error", "Batas halaman SMS tidak valid.", 422)
        }
        val available = availability(sessionId, documentGrantId)
        if (available.state != InventorySourceState.PENDING) {
            return AdapterPage(emptyList(), checkpoint, 0, available.state, available.reason)
        }
        val lastId = decodeCheckpoint(checkpoint)
        val records = mutableListOf<InventoryRecord>()
        var scanned = 0
        var lastScannedId: Long? = null
        try {
            provider.query(lastId, timeScope.notBeforeEpochMs, limit)?.use { cursor ->
                while (scanned < limit && cursor.moveToNext()) {
                    if (isCancelled()) {
                        return AdapterPage(
                            records,
                            lastScannedId?.toString() ?: checkpoint,
                            scanned,
                            InventorySourceState.CANCELLED,
                            "crawl_cancelled",
                        )
                    }
                    scanned += 1
                    val id = cursor.long(Telephony.Sms._ID) ?: continue
                    lastScannedId = id
                    records.add(mapRecord(cursor, id))
                }
            } ?: return AdapterPage(
                emptyList(),
                checkpoint,
                0,
                InventorySourceState.PARTIAL,
                "sms_provider_unavailable",
            )
        } catch (_: SecurityException) {
            return AdapterPage(
                records,
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.DENIED,
                "read_sms_revoked",
            )
        } catch (_: IllegalArgumentException) {
            return AdapterPage(
                records,
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.PARTIAL,
                "sms_provider_query_rejected",
            )
        }
        val hasMore = scanned >= limit
        return AdapterPage(
            records,
            lastScannedId?.toString().takeIf { hasMore },
            scanned,
        )
    }

    private fun mapRecord(cursor: Cursor, id: Long): InventoryRecord {
        val rawBody = cursor.text(Telephony.Sms.BODY)
        val body = CommunicationPolicy.smsText(rawBody)
        val address = CommunicationPolicy.boundedText(cursor.text(Telephony.Sms.ADDRESS), 512)
        val normalizedAddress = CommunicationPolicy.normalizedAddress(address)
        val thread = cursor.long(Telephony.Sms.THREAD_ID)?.takeIf { it >= 0 }
        val messageType = cursor.int(Telephony.Sms.TYPE) ?: 0
        val timestamp = cursor.long(Telephony.Sms.DATE)?.takeIf { it > 0 }
        val sentAt = cursor.long(Telephony.Sms.DATE_SENT)?.takeIf { it > 0 }
        val identity = "sms:$id"
        val identityHash = CommunicationPolicy.identityHash("sms", identity)
        val warningCodes = buildList {
            if (timestamp == null) add("sms_timestamp_missing")
            if (rawBody != null && rawBody.length > BuildConfig.MAX_SMS_TEXT_LENGTH) {
                add("sms_body_truncated")
            }
        }
        val contentHash = CommunicationPolicy.contentHash(
            id.toString(),
            address,
            body,
            timestamp?.toString(),
            messageType.toString(),
        )
        return InventoryRecord(
            recordId = CommunicationPolicy.recordId("sms", identity),
            identityHash = identityHash,
            dedupeHash = identityHash,
            sourceKind = InventorySourceKind.SMS,
            sourceAdapter = adapter,
            sourceApp = null,
            sourceLocator = CommunicationPolicy.sourceLocator("sms", identity),
            displayName = "SMS",
            mimeType = "application/vnd.siksik.sms+json",
            sizeBytes = body?.toByteArray(Charsets.UTF_8)?.size?.toLong(),
            width = null,
            height = null,
            durationMs = null,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = null,
            captureTimeEpochMs = timestamp,
            captureTimeSource = if (timestamp == null) "unknown" else "source_timestamp",
            directoryHint = null,
            exif = null,
            warningCodes = warningCodes,
            thumbnailAvailable = false,
            observedAtEpochMs = clock(),
            contentUri = null,
            normalizedText = body,
            contentSha256 = contentHash,
            smsMetadata = SmsRecordMetadata(
                direction = direction(messageType),
                address = address,
                addressIdentity = normalizedAddress?.let {
                    CommunicationPolicy.identityHash("sms_address", it)
                },
                threadIdentity = thread?.let {
                    CommunicationPolicy.identityHash("sms_thread", it.toString())
                },
                messageType = messageType,
                status = cursor.int(Telephony.Sms.STATUS),
                subscriptionId = cursor.int(SMS_SUBSCRIPTION_ID),
                isRead = cursor.boolean(Telephony.Sms.READ),
                isSeen = cursor.boolean(Telephony.Sms.SEEN),
                sentAtEpochMs = sentAt,
            ),
        )
    }

    private fun decodeCheckpoint(value: String?): Long? {
        if (value == null) return null
        return value.toLongOrNull()?.takeIf { it > 0 }
            ?: throw ApiException("invalid_cursor", "Checkpoint SMS tidak valid.", 422)
    }

    private fun direction(type: Int): String = when (type) {
        Telephony.Sms.MESSAGE_TYPE_INBOX -> "received"
        Telephony.Sms.MESSAGE_TYPE_SENT -> "sent"
        Telephony.Sms.MESSAGE_TYPE_DRAFT -> "draft"
        Telephony.Sms.MESSAGE_TYPE_OUTBOX -> "outbox"
        Telephony.Sms.MESSAGE_TYPE_FAILED -> "failed"
        Telephony.Sms.MESSAGE_TYPE_QUEUED -> "queued"
        else -> "unknown"
    }

    private fun Cursor.index(column: String): Int = getColumnIndex(column)

    private fun Cursor.text(column: String): String? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getString(it) }

    private fun Cursor.long(column: String): Long? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getLong(it) }

    private fun Cursor.int(column: String): Int? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getInt(it) }

    private fun Cursor.boolean(column: String): Boolean? = int(column)?.let { it != 0 }

}
