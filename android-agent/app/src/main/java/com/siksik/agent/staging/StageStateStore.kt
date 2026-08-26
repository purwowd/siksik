package com.siksik.agent.staging

import java.io.File
import org.json.JSONArray
import org.json.JSONObject

class StageStateStore(private val root: File) {
    fun save(record: StageRecord) {
        require(SAFE_FILE.matches(record.stageId))
        AtomicJsonStore.write(File(root, "${record.stageId}.json"), encode(record))
    }

    fun load(stageId: String): StageRecord? {
        require(SAFE_FILE.matches(stageId))
        val payload = AtomicJsonStore.read(File(root, "$stageId.json")) ?: return null
        return decode(payload)
    }

    companion object {
        private val SAFE_FILE = Regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")

        private fun encode(record: StageRecord): JSONObject = JSONObject()
            .put("stage_id", record.stageId)
            .put("session_id", record.sessionId)
            .put("grant_id", record.grantId)
            .put("grant_version", record.grantVersion)
            .put("catalog_id", record.catalogId)
            .put("source_kind", record.sourceKind)
            .put("source_id", record.sourceId)
            .put("selection_fingerprint", record.selectionFingerprint)
            .put("item_ids", JSONArray(record.itemIds))
            .put("idempotency_key", record.idempotencyKey)
            .put("request_fingerprint", record.requestFingerprint)
            .put("state", record.state)
            .put("completed_items", record.completedItems)
            .put("total_bytes", record.totalBytes)
            .put("manifest_relative_path", record.manifestRelativePath ?: JSONObject.NULL)
            .put("manifest_size_bytes", record.manifestSizeBytes ?: JSONObject.NULL)
            .put("manifest_sha256", record.manifestSha256 ?: JSONObject.NULL)
            .put("error_category", record.errorCategory ?: JSONObject.NULL)
            .put("cleanup_receipt_id", record.cleanupReceiptId ?: JSONObject.NULL)
            .put("cleanup_deleted_files", record.cleanupDeletedFiles ?: JSONObject.NULL)
            .put("cleanup_already_absent", record.cleanupAlreadyAbsent ?: JSONObject.NULL)
            .put("cleanup_epoch_ms", record.cleanupEpochMs ?: JSONObject.NULL)

        private fun decode(payload: JSONObject): StageRecord {
            val items = payload.getJSONArray("item_ids")
            return StageRecord(
                stageId = payload.getString("stage_id"),
                sessionId = payload.getString("session_id"),
                grantId = payload.getString("grant_id"),
                grantVersion = payload.getInt("grant_version"),
                catalogId = payload.getString("catalog_id"),
                sourceKind = payload.getString("source_kind"),
                sourceId = payload.getString("source_id"),
                selectionFingerprint = payload.getString("selection_fingerprint"),
                itemIds = (0 until items.length()).map(items::getString),
                idempotencyKey = payload.getString("idempotency_key"),
                requestFingerprint = payload.getString("request_fingerprint"),
                state = payload.getString("state"),
                completedItems = payload.getInt("completed_items"),
                totalBytes = payload.getLong("total_bytes"),
                manifestRelativePath = payload.optionalString("manifest_relative_path"),
                manifestSizeBytes = payload.optionalLong("manifest_size_bytes"),
                manifestSha256 = payload.optionalString("manifest_sha256"),
                errorCategory = payload.optionalString("error_category"),
                cleanupReceiptId = payload.optionalString("cleanup_receipt_id"),
                cleanupDeletedFiles = payload.optionalInt("cleanup_deleted_files"),
                cleanupAlreadyAbsent = payload.optionalBoolean("cleanup_already_absent"),
                cleanupEpochMs = payload.optionalLong("cleanup_epoch_ms"),
            )
        }

        private fun JSONObject.optionalString(key: String): String? =
            if (isNull(key)) null else optString(key).takeIf(String::isNotBlank)

        private fun JSONObject.optionalLong(key: String): Long? =
            if (isNull(key) || !has(key)) null else getLong(key)

        private fun JSONObject.optionalInt(key: String): Int? =
            if (isNull(key) || !has(key)) null else getInt(key)

        private fun JSONObject.optionalBoolean(key: String): Boolean? =
            if (isNull(key) || !has(key)) null else getBoolean(key)
    }
}
