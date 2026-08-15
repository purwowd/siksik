package com.siksik.agent

import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.provider.ContactsContract
import android.provider.Telephony
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.source.communication.CONTACT_DETAIL_PROJECTION
import com.siksik.agent.source.communication.CONTACT_PROJECTION
import com.siksik.agent.source.communication.ContactInventorySource
import com.siksik.agent.source.communication.ContactProviderGateway
import com.siksik.agent.source.communication.SMS_PROJECTION
import com.siksik.agent.source.communication.SmsInventorySource
import com.siksik.agent.source.communication.SmsProviderGateway
import com.siksik.agent.source.inventory.InventoryMode
import com.siksik.agent.source.inventory.InventorySourceState
import com.siksik.agent.source.inventory.InventoryTimeScope
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CommunicationProviderSourceTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun smsProjectionPaginationNormalizationDenialAndMalformedRowsAreExplicit() {
        val provider = SmsFixtureGateway()
        val source = SmsInventorySource(
            context,
            provider,
            clock = { 1_700_000_000_000L },
            permissionGranted = { true },
            telephonySupported = { true },
        )
        val first = source.page("session_fixture", null, null, 1) { false }
        val second = source.page(
            "session_fixture",
            null,
            requireNotNull(first.nextCheckpoint),
            2,
        ) { false }
        val denied = SmsInventorySource(
            context,
            provider,
            permissionGranted = { false },
            telephonySupported = { true },
        ).availability("session_fixture", null)
        val unsupported = SmsInventorySource(
            context,
            provider,
            permissionGranted = { true },
            telephonySupported = { false },
        ).availability("session_fixture", null)

        assertEquals(1, first.records.size)
        assertEquals("received", first.records.single().smsMetadata?.direction)
        assertEquals("fixture body", first.records.single().normalizedText)
        assertNotNull(first.nextCheckpoint)
        assertEquals(2, second.scannedCount)
        assertEquals(1, second.records.size)
        assertEquals(InventorySourceState.DENIED, denied.state)
        assertEquals(InventorySourceState.UNSUPPORTED, unsupported.state)
        assertEquals(SmsFixtureGateway.requiredProjection, SMS_PROJECTION.toSet())
        assertFalse(first.records.single().sourceLocator.contains("+620000000"))

        val bounded = source.page(
            "session_fixture",
            null,
            null,
            10,
            InventoryTimeScope.forRun(
                InventoryMode.QUICK,
                Instant.parse("2024-02-14T20:00:00Z").toEpochMilli(),
            ),
        ) { false }
        assertEquals(1, bounded.records.size)
        assertEquals("fixture body", bounded.records.single().normalizedText)
    }

    @Test
    fun contactDetailsUseOneBoundedBatchQueryAndProviderStatesAreExplicit() {
        val provider = ContactFixtureGateway()
        val source = ContactInventorySource(
            context,
            provider,
            clock = { 1_700_000_000_000L },
            permissionGranted = { true },
        )
        val page = source.page("session_fixture", null, null, 2) { false }

        assertEquals(2, page.records.size)
        assertEquals(1, provider.detailQueries)
        assertEquals(1, page.records.first().contactMetadata?.phones?.size)
        assertTrue(page.records.any { it.normalizedText?.contains("Fixture One") == true })
        assertTrue(CONTACT_PROJECTION.contains(ContactsContract.Contacts.LOOKUP_KEY))
        assertTrue(CONTACT_DETAIL_PROJECTION.contains(ContactsContract.Data.MIMETYPE))

        provider.detailsAvailable = false
        val unavailableDetails = source.page("session_fixture", null, null, 2) { false }
        assertEquals(InventorySourceState.PARTIAL, unavailableDetails.terminalState)
        assertEquals(
            "contact_details_provider_unavailable",
            unavailableDetails.terminalReason,
        )
        assertEquals(2, unavailableDetails.records.size)

        provider.detailsAvailable = true
        var cancellationChecks = 0
        val cancelled = source.page("session_fixture", null, null, 2) {
            cancellationChecks += 1
            cancellationChecks > 2
        }
        assertEquals(InventorySourceState.CANCELLED, cancelled.terminalState)

        provider.contacts.clear()
        val empty = source.page("session_fixture", null, null, 2) { false }
        assertEquals(InventorySourceState.COMPLETE, empty.terminalState)
        assertTrue(empty.records.isEmpty())
    }

    private class SmsFixtureGateway : SmsProviderGateway {
        private val rows = listOf(
            mapOf<String, Any?>(
                Telephony.Sms._ID to 3L,
                Telephony.Sms.THREAD_ID to 7L,
                Telephony.Sms.ADDRESS to "+620000000",
                Telephony.Sms.BODY to "fixture body",
                Telephony.Sms.DATE to 1_700_000_000_000L,
                Telephony.Sms.DATE_SENT to 1_700_000_000_000L,
                Telephony.Sms.TYPE to Telephony.Sms.MESSAGE_TYPE_INBOX,
                Telephony.Sms.STATUS to 0,
                Telephony.Sms.READ to 1,
                Telephony.Sms.SEEN to 1,
                "sub_id" to 1,
            ),
            mapOf<String, Any?>(
                Telephony.Sms._ID to 2L,
                Telephony.Sms.THREAD_ID to 8L,
                Telephony.Sms.ADDRESS to "+621111111",
                Telephony.Sms.BODY to "second body",
                Telephony.Sms.DATE to 1_699_000_000_000L,
                Telephony.Sms.DATE_SENT to 1_699_000_000_000L,
                Telephony.Sms.TYPE to Telephony.Sms.MESSAGE_TYPE_SENT,
                Telephony.Sms.STATUS to 0,
                Telephony.Sms.READ to 1,
                Telephony.Sms.SEEN to 1,
                "sub_id" to 1,
            ),
            mapOf<String, Any?>(Telephony.Sms._ID to null),
        )

        override fun query(lastId: Long?, notBeforeEpochMs: Long, limit: Int): Cursor = matrix(
            SMS_PROJECTION,
            rows.filter { row ->
                val id = row[Telephony.Sms._ID] as? Long
                val timestamp = row[Telephony.Sms.DATE] as? Long
                (timestamp == null || timestamp <= 0 || timestamp >= notBeforeEpochMs) &&
                    (lastId == null || id == null || id < lastId)
            }.take(limit),
        )

        companion object {
            val requiredProjection = setOf(
                Telephony.Sms._ID,
                Telephony.Sms.THREAD_ID,
                Telephony.Sms.ADDRESS,
                Telephony.Sms.BODY,
                Telephony.Sms.DATE,
                Telephony.Sms.DATE_SENT,
                Telephony.Sms.TYPE,
                Telephony.Sms.STATUS,
                Telephony.Sms.READ,
                Telephony.Sms.SEEN,
                "sub_id",
            )
        }
    }

    private class ContactFixtureGateway : ContactProviderGateway {
        val contacts = mutableListOf(
            mapOf<String, Any?>(
                ContactsContract.Contacts._ID to 2L,
                ContactsContract.Contacts.LOOKUP_KEY to "lookup-two",
                ContactsContract.Contacts.DISPLAY_NAME_PRIMARY to "Fixture Two",
                ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP to 1_700_000_000_000L,
            ),
            mapOf<String, Any?>(
                ContactsContract.Contacts._ID to 1L,
                ContactsContract.Contacts.LOOKUP_KEY to "lookup-one",
                ContactsContract.Contacts.DISPLAY_NAME_PRIMARY to "Fixture One",
                ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP to 1_699_000_000_000L,
            ),
        )
        var detailQueries = 0
        var detailsAvailable = true

        override fun queryContacts(lastId: Long?, limit: Int): Cursor = matrix(
            CONTACT_PROJECTION,
            contacts.filter { row ->
                lastId == null || (row[ContactsContract.Contacts._ID] as Long) < lastId
            }.take(limit),
        )

        override fun queryDetails(contactIds: List<Long>): Cursor? {
            detailQueries += 1
            if (!detailsAvailable) return null
            return matrix(
                CONTACT_DETAIL_PROJECTION,
                contactIds.map { id ->
                    mapOf<String, Any?>(
                        ContactsContract.Data._ID to id * 10,
                        ContactsContract.Data.CONTACT_ID to id,
                        ContactsContract.Data.MIMETYPE to (
                            ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE
                        ),
                        ContactsContract.Data.DATA1 to "+62000000$id",
                        ContactsContract.Data.DATA2 to 2,
                        ContactsContract.Data.DATA3 to "mobile",
                        ContactsContract.Data.DATA4 to "+62000000$id",
                        ContactsContract.Data.DATA5 to null,
                    )
                },
            )
        }
    }

    companion object {
        private fun matrix(
            columns: Array<out String>,
            rows: List<Map<String, Any?>>,
        ): MatrixCursor = MatrixCursor(columns).apply {
            rows.forEach { row -> addRow(columns.map { column -> row[column] }.toTypedArray()) }
        }
    }
}
