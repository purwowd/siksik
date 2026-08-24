package com.siksik.agent.staging

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import java.io.File
import java.io.FileOutputStream
import org.json.JSONObject

class CrawlTransferStateStore(private val stateRoot: File) {
    init {
        if (!stateRoot.mkdirs() && !stateRoot.isDirectory) {
            throw ApiException("storage_unavailable", "Status transfer tidak tersedia.", 507)
        }
    }

    @Synchronized
    fun save(record: CrawlTransferRecord) {
        val target = stateFile(record.stageId)
        val partial = StagePathPolicy.controlledChild(stateRoot, "${target.name}.partial")
        try {
            FileOutputStream(partial).use { output ->
                output.write(encode(record).toString().toByteArray(Charsets.UTF_8))
                output.fd.sync()
            }
            if (target.exists() && !target.delete()) {
                throw ApiException("storage_unavailable", "Status transfer tidak dapat diperbarui.", 507)
            }
            if (!partial.renameTo(target)) {
                throw ApiException("storage_unavailable", "Status transfer tidak dapat difinalisasi.", 507)
            }
        } finally {
            if (partial.exists()) partial.delete()
        }
    }

    @Synchronized
    fun load(stageId: String): CrawlTransferRecord? {
        val target = stateFile(stageId)
        if (!target.isFile) return null
        return try {
            decode(JSONObject(target.readText(Charsets.UTF_8)))
        } catch (exception: ApiException) {
            throw exception
        } catch (_: Exception) {
            throw ApiException("stage_failed", "Status transfer tersimpan tidak valid.", 500)
        }
    }

    private fun stateFile(stageId: String): File {
        if (!SessionAuthenticator.SAFE_ID.matches(stageId)) {
            throw ApiException("validation_error", "ID stage tidak valid.", 422)
        }
        return StagePathPolicy.controlledChild(stateRoot, "$stageId.json")
    }

    private fun encode(value: CrawlTransferRecord): JSONObject = JSONObject()
        .put("stage_id", value.stageId)
        .put("session_id", value.sessionId)
        .put("crawl_id", value.crawlId)
        .put("selection_revision", value.selectionRevision)
        .put("selection_fingerprint", value.selectionFingerprint)
        .put("idempotency_key", value.idempotencyKey)
        .put("request_fingerprint", value.requestFingerprint)
        .put("state", value.state)
        .put("completed_records", value.completedRecords)
        .put("total_records", value.totalRecords)
        .put("artifact_count", value.artifactCount)
        .put("total_bytes", value.totalBytes)
        .putNullable("manifest_relative_path", value.manifestRelativePath)
        .putNullable("manifest_size_bytes", value.manifestSizeBytes)
        .putNullable("manifest_sha256", value.manifestSha256)
        .putNullable("error_category", value.errorCategory)
        .putNullable("cleanup_receipt_id", value.cleanupReceiptId)
        .putNullable("cleanup_deleted_files", value.cleanupDeletedFiles)
        .putNullable("cleanup_already_absent", value.cleanupAlreadyAbsent)
        .putNullable("cleanup_epoch_ms", value.cleanupEpochMs)

    private fun decode(value: JSONObject): CrawlTransferRecord = CrawlTransferRecord(
        stageId = value.getString("stage_id"),
        sessionId = value.getString("session_id"),
        crawlId = value.getString("crawl_id"),
        selectionRevision = value.getInt("selection_revision"),
        selectionFingerprint = value.getString("selection_fingerprint"),
        idempotencyKey = value.getString("idempotency_key"),
        requestFingerprint = value.getString("request_fingerprint"),
        state = value.getString("state"),
        completedRecords = value.getInt("completed_records"),
        totalRecords = value.getInt("total_records"),
        artifactCount = value.getInt("artifact_count"),
        totalBytes = value.getLong("total_bytes"),
        manifestRelativePath = value.nullableString("manifest_relative_path"),
        manifestSizeBytes = value.nullableLong("manifest_size_bytes"),
        manifestSha256 = value.nullableString("manifest_sha256"),
        errorCategory = value.nullableString("error_category"),
        cleanupReceiptId = value.nullableString("cleanup_receipt_id"),
        cleanupDeletedFiles = value.nullableInt("cleanup_deleted_files"),
        cleanupAlreadyAbsent = value.nullableBoolean("cleanup_already_absent"),
        cleanupEpochMs = value.nullableLong("cleanup_epoch_ms"),
    )

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
