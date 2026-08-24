package com.siksik.agent.staging

import org.json.JSONArray
import org.json.JSONObject

object ManifestBuilder {
    fun build(record: StageRecord, artifacts: List<StagedArtifact>, createdAtEpochMs: Long): ByteArray {
        val entries = JSONArray()
        artifacts.forEach { artifact ->
            entries.put(
                JSONObject()
                    .put("artifact_id", artifact.artifactId)
                    .put("parent_artifact_id", JSONObject.NULL)
                    .put("kind", "media")
                    .put("media_id", artifact.mediaId)
                    .put("rank", artifact.rank)
                    .put("display_name", artifact.displayName)
                    .put("relative_path", artifact.relativePath)
                    .put("mime_type", artifact.mimeType)
                    .put("size_bytes", artifact.sizeBytes)
                    .put("sha256", artifact.sha256),
            )
        }
        return JSONObject()
            .put("manifest_version", 1)
            .put("bundle_format", "manifest_files_v1")
            .put("stage_id", record.stageId)
            .put("siksik_session_id", record.sessionId)
            .put("grant_id", record.grantId)
            .put("grant_version", record.grantVersion)
            .put("catalog_id", record.catalogId)
            .put("source_kind", record.sourceKind)
            .put("source_id", record.sourceId)
            .put("selection_fingerprint", record.selectionFingerprint)
            .put("created_at_epoch_ms", createdAtEpochMs)
            .put("artifacts", entries)
            .toString()
            .toByteArray(Charsets.UTF_8)
    }
}
