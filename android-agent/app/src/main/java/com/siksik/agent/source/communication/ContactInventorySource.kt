package com.siksik.agent.source.communication

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.ContactsContract
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

class ContactInventorySource(
    context: Context,
    private val provider: ContactProviderGateway = AndroidContactProviderGateway(
        context.contentResolver,
    ),
    private val clock: () -> Long = System::currentTimeMillis,
    private val permissionGranted: () -> Boolean = {
        context.checkSelfPermission(Manifest.permission.READ_CONTACTS) ==
            PackageManager.PERMISSION_GRANTED
    },
) : InventorySource {
    override val adapter = SourceAdapter.CONTACT

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability =
        if (permissionGranted()) {
            SourceAvailability(InventorySourceState.PENDING)
        } else {
            SourceAvailability(InventorySourceState.DENIED, "read_contacts_not_granted")
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
            throw ApiException("validation_error", "Batas halaman kontak tidak valid.", 422)
        }
        val available = availability(sessionId, documentGrantId)
        if (available.state != InventorySourceState.PENDING) {
            return AdapterPage(emptyList(), checkpoint, 0, available.state, available.reason)
        }
        val lastId = decodeCheckpoint(checkpoint)
        val contactRows = mutableListOf<ContactRow>()
        var scanned = 0
        var lastScannedId: Long? = null
        try {
            provider.queryContacts(lastId, limit)?.use { cursor ->
                while (scanned < limit && cursor.moveToNext()) {
                    if (isCancelled()) {
                        return AdapterPage(
                            emptyList(),
                            lastScannedId?.toString() ?: checkpoint,
                            scanned,
                            InventorySourceState.CANCELLED,
                            "crawl_cancelled",
                        )
                    }
                    scanned += 1
                    val id = cursor.long(ContactsContract.Contacts._ID) ?: continue
                    lastScannedId = id
                    contactRows.add(
                        ContactRow(
                            id,
                            CommunicationPolicy.boundedText(
                                cursor.text(ContactsContract.Contacts.LOOKUP_KEY),
                                1024,
                            ),
                            CommunicationPolicy.boundedText(
                                cursor.text(ContactsContract.Contacts.DISPLAY_NAME_PRIMARY),
                                2048,
                            ),
                            cursor.long(ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP)
                                ?.takeIf { it > 0 },
                        ),
                    )
                }
            } ?: return AdapterPage(
                emptyList(),
                checkpoint,
                0,
                InventorySourceState.PARTIAL,
                "contacts_provider_unavailable",
            )
        } catch (_: SecurityException) {
            return AdapterPage(
                emptyList(),
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.DENIED,
                "read_contacts_revoked",
            )
        } catch (_: IllegalArgumentException) {
            return AdapterPage(
                emptyList(),
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.PARTIAL,
                "contacts_provider_query_rejected",
            )
        }
        val detailPage = try {
            queryDetails(contactRows.map(ContactRow::id), isCancelled)
        } catch (_: SecurityException) {
            return AdapterPage(
                emptyList(),
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.DENIED,
                "read_contacts_revoked",
            )
        } catch (_: IllegalArgumentException) {
            return AdapterPage(
                contactRows.map { mapRecord(it, ContactDetails()) }.filter(timeScope::includes),
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.PARTIAL,
                "contact_details_query_rejected",
            )
        }
        if (detailPage.cancelled) {
            return AdapterPage(
                emptyList(),
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.CANCELLED,
                "crawl_cancelled",
            )
        }
        val records = contactRows.map { row ->
            mapRecord(row, detailPage.details[row.id] ?: ContactDetails())
        }.filter(timeScope::includes)
        if (!detailPage.providerAvailable) {
            return AdapterPage(
                records,
                lastScannedId?.toString() ?: checkpoint,
                scanned,
                InventorySourceState.PARTIAL,
                "contact_details_provider_unavailable",
            )
        }
        val hasMore = scanned >= limit
        return AdapterPage(records, lastScannedId?.toString().takeIf { hasMore }, scanned)
    }

    private fun queryDetails(
        contactIds: List<Long>,
        isCancelled: () -> Boolean,
    ): ContactDetailPage {
        if (contactIds.isEmpty()) return ContactDetailPage(emptyMap())
        val details = contactIds.associateWith { ContactDetails() }.toMutableMap()
        val cursor = provider.queryDetails(contactIds)
            ?: return ContactDetailPage(details, providerAvailable = false)
        cursor.use {
            while (cursor.moveToNext()) {
                if (isCancelled()) return ContactDetailPage(details, cancelled = true)
                val contactId = cursor.long(ContactsContract.Data.CONTACT_ID) ?: continue
                val target = details[contactId] ?: continue
                when (cursor.text(ContactsContract.Data.MIMETYPE)) {
                    ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE ->
                        if (target.phones.size < MAX_DETAILS_PER_KIND) {
                            val value = CommunicationPolicy.boundedText(
                                cursor.text(ContactsContract.Data.DATA1),
                                512,
                            ) ?: continue
                            target.phones.add(
                                ContactIdentity(
                                    value,
                                    CommunicationPolicy.normalizedPhone(
                                        cursor.text(ContactsContract.Data.DATA4) ?: value,
                                    ),
                                    detailLabel(cursor),
                                ),
                            )
                        }
                    ContactsContract.CommonDataKinds.Email.CONTENT_ITEM_TYPE ->
                        if (target.emails.size < MAX_DETAILS_PER_KIND) {
                            val value = CommunicationPolicy.boundedText(
                                cursor.text(ContactsContract.Data.DATA1),
                                1024,
                            ) ?: continue
                            target.emails.add(
                                ContactIdentity(
                                    value,
                                    CommunicationPolicy.normalizedEmail(value),
                                    detailLabel(cursor),
                                ),
                            )
                        }
                    ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE ->
                        if (target.organizations.size < MAX_DETAILS_PER_KIND) {
                            target.organizations.add(
                                ContactOrganization(
                                    company = CommunicationPolicy.boundedText(
                                        cursor.text(ContactsContract.Data.DATA1),
                                        2048,
                                    ),
                                    title = CommunicationPolicy.boundedText(
                                        cursor.text(ContactsContract.Data.DATA4),
                                        2048,
                                    ),
                                    department = CommunicationPolicy.boundedText(
                                        cursor.text(ContactsContract.Data.DATA5),
                                        2048,
                                    ),
                                ),
                            )
                        }
                }
            }
        }
        return ContactDetailPage(details)
    }

    private fun mapRecord(row: ContactRow, details: ContactDetails): InventoryRecord {
        val identity = row.lookupKey ?: row.id.toString()
        val identityHash = CommunicationPolicy.identityHash("contact", identity)
        val text = CommunicationPolicy.contactText(
            buildList {
                add(row.displayName)
                addAll(details.phones.map(ContactIdentity::value))
                addAll(details.emails.map(ContactIdentity::value))
                details.organizations.forEach { organization ->
                    add(organization.company)
                    add(organization.title)
                    add(organization.department)
                }
            },
        )
        val contentHash = CommunicationPolicy.contentHash(
            row.displayName,
            details.phones.joinToString("|") { it.value },
            details.emails.joinToString("|") { it.value },
            details.organizations.joinToString("|") {
                listOf(it.company, it.title, it.department).joinToString(":")
            },
            row.updatedAt?.toString(),
        )
        return InventoryRecord(
            recordId = CommunicationPolicy.recordId("contact", identity),
            identityHash = identityHash,
            dedupeHash = identityHash,
            sourceKind = InventorySourceKind.CONTACT,
            sourceAdapter = adapter,
            sourceApp = null,
            sourceLocator = CommunicationPolicy.sourceLocator("contact", identity),
            displayName = "Contact",
            mimeType = "application/vnd.siksik.contact+json",
            sizeBytes = text?.toByteArray(Charsets.UTF_8)?.size?.toLong(),
            width = null,
            height = null,
            durationMs = null,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = row.updatedAt,
            captureTimeEpochMs = row.updatedAt,
            captureTimeSource = if (row.updatedAt == null) "unknown" else "source_timestamp",
            directoryHint = null,
            exif = null,
            warningCodes = emptyList(),
            thumbnailAvailable = false,
            observedAtEpochMs = clock(),
            contentUri = null,
            normalizedText = text,
            contentSha256 = contentHash,
            contactMetadata = ContactRecordMetadata(
                displayName = row.displayName,
                lookupIdentity = identityHash,
                phones = details.phones,
                emails = details.emails,
                organizations = details.organizations,
                updatedAtEpochMs = row.updatedAt,
            ),
        )
    }

    private fun detailLabel(cursor: Cursor): String? =
        CommunicationPolicy.boundedText(cursor.text(ContactsContract.Data.DATA3), 256)
            ?: cursor.int(ContactsContract.Data.DATA2)?.let { "type_$it" }

    private fun decodeCheckpoint(value: String?): Long? {
        if (value == null) return null
        return value.toLongOrNull()?.takeIf { it > 0 }
            ?: throw ApiException("invalid_cursor", "Checkpoint kontak tidak valid.", 422)
    }

    private fun Cursor.index(column: String): Int = getColumnIndex(column)

    private fun Cursor.text(column: String): String? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getString(it) }

    private fun Cursor.long(column: String): Long? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getLong(it) }

    private fun Cursor.int(column: String): Int? = index(column).takeIf { it >= 0 }
        ?.let { if (isNull(it)) null else getInt(it) }

    private data class ContactRow(
        val id: Long,
        val lookupKey: String?,
        val displayName: String?,
        val updatedAt: Long?,
    )

    private data class ContactDetails(
        val phones: MutableList<ContactIdentity> = mutableListOf(),
        val emails: MutableList<ContactIdentity> = mutableListOf(),
        val organizations: MutableList<ContactOrganization> = mutableListOf(),
    )

    private data class ContactDetailPage(
        val details: Map<Long, ContactDetails>,
        val cancelled: Boolean = false,
        val providerAvailable: Boolean = true,
    )

    companion object {
        private const val MAX_DETAILS_PER_KIND = 32
    }
}
