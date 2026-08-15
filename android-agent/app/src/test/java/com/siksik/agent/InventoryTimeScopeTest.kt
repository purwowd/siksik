package com.siksik.agent

import com.siksik.agent.source.inventory.InventoryMode
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.InventoryTimeScope
import com.siksik.agent.source.inventory.SourceAdapter
import java.time.Instant
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InventoryTimeScopeTest {
    @Test
    fun quickAndFullUseThreeAndSixCalendarMonths() {
        val reference = Instant.parse("2026-08-31T12:30:00Z").toEpochMilli()

        val quick = InventoryTimeScope.forRun(InventoryMode.QUICK, reference)
        val full = InventoryTimeScope.forRun(InventoryMode.FULL, reference)

        assertTrue(
            quick.notBeforeEpochMs == Instant.parse("2026-05-31T12:30:00Z").toEpochMilli(),
        )
        assertTrue(
            full.notBeforeEpochMs == Instant.parse("2026-02-28T12:30:00Z").toEpochMilli(),
        )
    }

    @Test
    fun datedRecordsAreBoundedWhileUnknownAndContactsRemainAvailable() {
        val cutoff = InventoryTimeScope.forRun(
            InventoryMode.QUICK,
            Instant.parse("2026-08-14T10:00:00Z").toEpochMilli(),
        )

        assertFalse(cutoff.includes(record(InventorySourceKind.MEDIA_IMAGE, "2026-05-13T00:00:00Z")))
        assertTrue(cutoff.includes(record(InventorySourceKind.MEDIA_IMAGE, "2026-05-14T10:00:00Z")))
        assertTrue(cutoff.includes(record(InventorySourceKind.MEDIA_IMAGE, null)))
        assertTrue(cutoff.includes(record(InventorySourceKind.CONTACT, "2020-01-01T00:00:00Z")))
    }

    private fun record(kind: InventorySourceKind, timestamp: String?): InventoryRecord {
        val epochMs = timestamp?.let { Instant.parse(it).toEpochMilli() }
        return InventoryRecord(
            recordId = "record_fixture",
            identityHash = "identity_fixture",
            dedupeHash = "identity_fixture",
            sourceKind = kind,
            sourceAdapter = if (kind == InventorySourceKind.CONTACT) {
                SourceAdapter.CONTACT
            } else {
                SourceAdapter.MEDIA_IMAGE
            },
            sourceApp = null,
            sourceLocator = "fixture:record",
            displayName = "fixture",
            mimeType = "application/octet-stream",
            sizeBytes = null,
            width = null,
            height = null,
            durationMs = null,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = null,
            captureTimeEpochMs = epochMs,
            captureTimeSource = if (epochMs == null) "unknown" else "source_timestamp",
            directoryHint = null,
            exif = null,
            warningCodes = emptyList(),
            thumbnailAvailable = false,
            observedAtEpochMs = 1,
            contentUri = null,
        )
    }
}
