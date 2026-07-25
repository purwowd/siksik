package com.siksik.agent

import com.siksik.agent.staging.ManifestBuilder
import com.siksik.agent.staging.StageRecord
import com.siksik.agent.staging.StagedArtifact
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class ManifestBuilderTest {
    @Test
    fun finalizesNonArchiveManifestWithVerifiedIndividualArtifact() {
        val bytes = ManifestBuilder.build(
            stageRecord(),
            listOf(
                StagedArtifact(
                    artifactId = "artifact_fixture",
                    mediaId = "media_fixture",
                    rank = 0,
                    displayName = "image.jpg",
                    relativePath = "session_fixture/stage_fixture/files/image.jpg",
                    mimeType = "image/jpeg",
                    sizeBytes = 128,
                    sha256 = "b".repeat(64),
                ),
            ),
            2_000,
        )
        val payload = JSONObject(bytes.toString(Charsets.UTF_8))
        val artifact = payload.getJSONArray("artifacts").getJSONObject(0)

        assertEquals("manifest_files_v1", payload.getString("bundle_format"))
        assertEquals("session_fixture", payload.getString("siksik_session_id"))
        assertEquals("b".repeat(64), artifact.getString("sha256"))
        assertEquals(128L, artifact.getLong("size_bytes"))
        assertFalse(payload.toString().contains(".zip", ignoreCase = true))
    }

    private fun stageRecord() = StageRecord(
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
        requestFingerprint = "c".repeat(64),
        state = "finalizing",
        completedItems = 1,
        totalBytes = 128,
        manifestRelativePath = null,
        manifestSizeBytes = null,
        manifestSha256 = null,
        errorCategory = null,
        cleanupReceiptId = null,
        cleanupDeletedFiles = null,
        cleanupAlreadyAbsent = null,
        cleanupEpochMs = null,
    )
}
