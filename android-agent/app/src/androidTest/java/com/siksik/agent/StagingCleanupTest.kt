package com.siksik.agent

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.source.media.MediaCatalog
import com.siksik.agent.staging.StageRecord
import com.siksik.agent.staging.StageStateStore
import com.siksik.agent.staging.StagingManager
import java.io.File
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class StagingCleanupTest {
    @Test
    fun cleanupDeletesIndividualFilesAndIsIdempotent() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val suffix = UUID.randomUUID().toString()
        val sessionId = "session_$suffix"
        val stageId = "stage_$suffix"
        val stateRoot = File(context.filesDir, "siksik_stage_state")
        val stateStore = StageStateStore(stateRoot)
        val record = record(sessionId, stageId)
        stateStore.save(record)
        val external = context.getExternalFilesDir(null)
        assertNotNull(external)
        val stageRoot = File(external, "siksik_agent/$sessionId/$stageId")
        val stagedFile = File(stageRoot, "files/artifact_fixture__image.jpg")
        assertTrue(stagedFile.parentFile?.mkdirs() == true || stagedFile.parentFile?.isDirectory == true)
        stagedFile.writeBytes(byteArrayOf(1, 2, 3))
        val grants = GrantGateway(context)
        val manager = StagingManager(context, MediaCatalog(context, grants), grants)
        try {
            val first = manager.cleanup(sessionId, stageId)
            val repeated = manager.cleanup(sessionId, stageId)

            assertFalse(stageRoot.exists())
            assertEquals(1, first.cleanupDeletedFiles)
            assertEquals(false, first.cleanupAlreadyAbsent)
            assertEquals(first.cleanupReceiptId, repeated.cleanupReceiptId)
        } finally {
            manager.shutdown()
            File(stateRoot, "$stageId.json").delete()
        }
    }

    private fun record(sessionId: String, stageId: String) = StageRecord(
        stageId = stageId,
        sessionId = sessionId,
        grantId = "grant_fixture",
        grantVersion = 1,
        catalogId = "catalog_fixture",
        sourceKind = "manual_selection",
        sourceId = "selection_fixture",
        selectionFingerprint = "a".repeat(64),
        itemIds = listOf("media_fixture"),
        idempotencyKey = "key_fixture",
        requestFingerprint = "b".repeat(64),
        state = "completed",
        completedItems = 1,
        totalBytes = 3,
        manifestRelativePath = "$sessionId/$stageId/manifest.json",
        manifestSizeBytes = 128,
        manifestSha256 = "c".repeat(64),
        errorCategory = null,
        cleanupReceiptId = null,
        cleanupDeletedFiles = null,
        cleanupAlreadyAbsent = null,
        cleanupEpochMs = null,
    )
}
