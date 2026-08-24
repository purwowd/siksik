package com.siksik.agent.staging

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

class StageStateStore(private val stateRoot: File) {
    init {
        if (!stateRoot.mkdirs() && !stateRoot.isDirectory) {
            throw ApiException("storage_unavailable", "Penyimpanan status staging tidak tersedia.", 507)
        }
    }

    @Synchronized
    fun save(record: StageRecord) {
        val target = stateFile(record.stageId)
        val partial = StagePathPolicy.controlledChild(stateRoot, "${target.name}.partial")
        val bytes = encode(record).toString().toByteArray(Charsets.UTF_8)
        try {
            FileOutputStream(partial).use { output ->
                output.write(bytes)
                output.fd.sync()
            }
            if (target.exists() && !target.delete()) {
                throw ApiException("storage_unavailable", "Status staging tidak dapat diperbarui.", 507)
            }
            if (!partial.renameTo(target)) {
                throw ApiException("storage_unavailable", "Status staging tidak dapat difinalisasi.", 507)
            }
        } finally {
            if (partial.exists() && partial != target) {
                partial.delete()
            }
        }
    }

    @Synchronized
    fun load(stageId: String): StageRecord? {
        val file = stateFile(stageId)
        if (!file.isFile) return null
        return try {
            decode(JSONObject(file.readText(Charsets.UTF_8)))
        } catch (exception: ApiException) {
            throw exception
        } catch (_: Exception) {
            throw ApiException("stage_failed", "Status staging tersimpan tidak valid.", 500)
        }
    }

    private fun stateFile(stageId: String): File {
        validateId(stageId)
        return StagePathPolicy.controlledChild(stateRoot, "$stageId.json")
    }

    private fun validateId(value: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(value)) {
            throw ApiException("validation_error", "ID stage tidak valid.", 422)
        }
    }

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
        .putNullable("manifest_relative_path", record.manifestRelativePath)
        .putNullable("manifest_size_bytes", record.manifestSizeBytes)
        .putNullable("manifest_sha256", record.manifestSha256)
        .putNullable("error_category", record.errorCategory)
        .putNullable("cleanup_receipt_id", record.cleanupReceiptId)
        .putNullable("cleanup_deleted_files", record.cleanupDeletedFiles)
        .putNullable("cleanup_already_absent", record.cleanupAlreadyAbsent)
        .putNullable("cleanup_epoch_ms", record.cleanupEpochMs)

    private fun decode(payload: JSONObject): StageRecord {
        val itemIds = payload.getJSONArray("item_ids")
        return StageRecord(
            stageId = payload.getString("stage_id"),
            sessionId = payload.getString("session_id"),
            grantId = payload.getString("grant_id"),
            grantVersion = payload.getInt("grant_version"),
            catalogId = payload.getString("catalog_id"),
            sourceKind = payload.getString("source_kind"),
            sourceId = payload.getString("source_id"),
            selectionFingerprint = payload.getString("selection_fingerprint"),
            itemIds = (0 until itemIds.length()).map(itemIds::getString),
            idempotencyKey = payload.getString("idempotency_key"),
            requestFingerprint = payload.getString("request_fingerprint"),
            state = payload.getString("state"),
            completedItems = payload.getInt("completed_items"),
            totalBytes = payload.getLong("total_bytes"),
            manifestRelativePath = payload.nullableString("manifest_relative_path"),
            manifestSizeBytes = payload.nullableLong("manifest_size_bytes"),
            manifestSha256 = payload.nullableString("manifest_sha256"),
            errorCategory = payload.nullableString("error_category"),
            cleanupReceiptId = payload.nullableString("cleanup_receipt_id"),
            cleanupDeletedFiles = payload.nullableInt("cleanup_deleted_files"),
            cleanupAlreadyAbsent = payload.nullableBoolean("cleanup_already_absent"),
            cleanupEpochMs = payload.nullableLong("cleanup_epoch_ms"),
        )
    }

    private fun JSONObject.putNullable(key: String, value: Any?): JSONObject =
        put(key, value ?: JSONObject.NULL)

    private fun JSONObject.nullableString(key: String): String? =
        if (isNull(key)) null else getString(key)

    private fun JSONObject.nullableLong(key: String): Long? =
        if (isNull(key)) null else getLong(key)

    private fun JSONObject.nullableInt(key: String): Int? =
        if (isNull(key)) null else getInt(key)

    private fun JSONObject.nullableBoolean(key: String): Boolean? =
        if (isNull(key)) null else getBoolean(key)
}
