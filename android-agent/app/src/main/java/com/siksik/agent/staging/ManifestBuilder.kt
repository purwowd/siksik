package com.siksik.agent.staging

import org.json.JSONArray
import org.json.JSONObject

object ManifestBuilder {
    fun build(
        record: StageRecord,
        artifacts: List<StagedArtifact>,
        createdAtEpochMs: Long,
    ): ByteArray {
        val payload = JSONObject()
            .put("schema_version", 1)
            .put("bundle_format", "manifest_files_v1")
            .put("stage_id", record.stageId)
            .put("siksik_session_id", record.sessionId)
            .put("grant_id", record.grantId)
            .put("catalog_id", record.catalogId)
            .put("source_kind", record.sourceKind)
            .put("source_id", record.sourceId)
            .put("selection_fingerprint", record.selectionFingerprint)
            .put("created_at_epoch_ms", createdAtEpochMs)
            .put("artifact_count", artifacts.size)
            .put("total_bytes", artifacts.sumOf(StagedArtifact::sizeBytes))
            .put(
                "artifacts",
                JSONArray().apply {
                    artifacts.forEach { artifact ->
                        put(
                            JSONObject()
                                .put("artifact_id", artifact.artifactId)
                                .put("media_id", artifact.mediaId)
                                .put("rank", artifact.rank)
                                .put("display_name", artifact.displayName)
                                .put("relative_path", artifact.relativePath)
                                .put("mime_type", artifact.mimeType)
                                .put("size_bytes", artifact.sizeBytes)
                                .put("sha256", artifact.sha256),
                        )
                    }
                },
            )
        return payload.toString().toByteArray(Charsets.UTF_8)
    }

    fun buildDirect(
        record: CrawlTransferRecord,
        artifacts: List<DirectStagedArtifact>,
        createdAtEpochMs: Long,
    ): ByteArray {
        val payload = JSONObject()
            .put("schema_version", 1)
            .put("bundle_format", "direct_manifest_files_v1")
            .put("stage_id", record.stageId)
            .put("siksik_session_id", record.sessionId)
            .put("crawl_id", record.crawlId)
            .put("selection_revision", record.selectionRevision)
            .put("selection_fingerprint", record.selectionFingerprint)
            .put("policy_fingerprint", record.policyFingerprint)
            .put("record_count", record.totalRecords)
            .put("artifact_count", artifacts.size)
            .put("total_bytes", artifacts.sumOf(DirectStagedArtifact::sizeBytes))
            .put("created_at_epoch_ms", createdAtEpochMs)
            .put(
                "artifacts",
                JSONArray().apply {
                    artifacts.forEach { artifact ->
                        put(
                            JSONObject()
                                .put("artifact_id", artifact.artifactId)
                                .put("record_id", artifact.recordId)
                                .put("source_kind", artifact.sourceKind)
                                .put("role", artifact.role)
                                .put("attachment_id", artifact.attachmentId ?: JSONObject.NULL)
                                .put("relative_path", artifact.relativePath)
                                .put("mime_type", artifact.mimeType)
                                .put("size_bytes", artifact.sizeBytes)
                                .put("sha256", artifact.sha256),
                        )
                    }
                },
            )
        return payload.toString().toByteArray(Charsets.UTF_8)
    }
}
