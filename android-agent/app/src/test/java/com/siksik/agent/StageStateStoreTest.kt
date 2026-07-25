package com.siksik.agent

import com.siksik.agent.staging.StageRecord
import com.siksik.agent.staging.StageStateStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class StageStateStoreTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun atomicallyPersistsAndLoadsNullableState() {
        val store = StageStateStore(temporaryFolder.newFolder("state"))
        val record = StageRecord(
            stageId = "stage_fixture",
            sessionId = "session_fixture",
            grantId = "grant_fixture",
            grantVersion = 1,
            catalogId = "catalog_fixture",
            sourceKind = "manual_selection",
            sourceId = "selection_fixture",
            selectionFingerprint = "a".repeat(64),
            itemIds = listOf("media_fixture"),
            idempotencyKey = "key_fixture",
            requestFingerprint = "b".repeat(64),
            state = "queued",
            completedItems = 0,
            totalBytes = 0,
            manifestRelativePath = null,
            manifestSizeBytes = null,
            manifestSha256 = null,
            errorCategory = null,
            cleanupReceiptId = null,
            cleanupDeletedFiles = null,
            cleanupAlreadyAbsent = null,
            cleanupEpochMs = null,
        )

        store.save(record)
        val loaded = store.load(record.stageId)!!

        assertEquals(record, loaded)
        assertNull(loaded.manifestRelativePath)
    }
}
