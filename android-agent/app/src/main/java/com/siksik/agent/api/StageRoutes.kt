package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.staging.StageRecord
import com.siksik.agent.staging.StageRequest
import com.siksik.agent.staging.StagingManager
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

class StageRoutes(
    private val authenticator: SessionAuthenticator,
    private val staging: StagingManager,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val stageMatch = STAGE_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && stageMatch != null) {
            request.authenticate(stageMatch.groupValues[1])
            val idempotencyKey = request.requiredHeader("idempotency-key")
            val expected = setOf(
                "stage_id",
                "grant_id",
                "grant_version",
                "catalog_id",
                "source_kind",
                "source_id",
                "selection_fingerprint",
                "media_ids",
            )
            val body = request.jsonBody(expected)
            val items = body.getJSONArray("media_ids")
            val itemIds = (0 until items.length()).map(items::getString)
            val record = staging.start(
                authenticator.sessionId,
                StageRequest(
                    stageId = body.getString("stage_id"),
                    grantId = body.getString("grant_id"),
                    grantVersion = body.getInt("grant_version"),
                    catalogId = body.getString("catalog_id"),
                    sourceKind = body.getString("source_kind"),
                    sourceId = body.getString("source_id"),
                    selectionFingerprint = body.getString("selection_fingerprint"),
                    itemIds = itemIds,
                ),
                idempotencyKey,
            )
            return ApiResponse.json(202, stageJson(record))
        }
        val statusMatch = STATUS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && statusMatch != null) {
            request.authenticate(statusMatch.groupValues[1])
            request.validateQuery(setOf("stage_id"))
            return ApiResponse.json(
                200,
                stageJson(
                    staging.status(
                        authenticator.sessionId,
                        request.query("stage_id", required = true)!!,
                    ),
                ),
            )
        }
        val manifestMatch = MANIFEST_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && manifestMatch != null) {
            request.authenticate(manifestMatch.groupValues[1])
            request.validateQuery(setOf("stage_id"))
            val record = staging.status(
                authenticator.sessionId,
                request.query("stage_id", required = true)!!,
            )
            if (record.state != "completed" || record.manifestRelativePath == null) {
                throw ApiException("stage_not_ready", "Manifest stage belum selesai.", 409, true)
            }
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("stage_id", record.stageId)
                    .put("state", record.state)
                    .put("bundle_format", "manifest_files_v1")
                    .put("manifest_relative_path", record.manifestRelativePath)
                    .put("manifest_size_bytes", record.manifestSizeBytes)
                    .put("manifest_sha256", record.manifestSha256),
            )
        }
        val cleanupMatch = CLEANUP_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && cleanupMatch != null) {
            request.authenticate(cleanupMatch.groupValues[1])
            request.requiredHeader("idempotency-key")
            val body = request.jsonBody(setOf("stage_id"))
            val record = staging.cleanup(authenticator.sessionId, body.getString("stage_id"))
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("receipt_id", record.cleanupReceiptId)
                    .put("stage_id", record.stageId)
                    .put("deleted_files", record.cleanupDeletedFiles)
                    .put("already_absent", record.cleanupAlreadyAbsent)
                    .put("deleted_at_epoch_ms", record.cleanupEpochMs),
            )
        }
        return null
    }

    private fun stageJson(record: StageRecord): JSONObject = JSONObject()
        .put("stage_id", record.stageId)
        .put("state", record.state)
        .put("item_count", record.itemIds.size)
        .put("completed_items", record.completedItems)
        .put("total_bytes", record.totalBytes)
        .put("error_category", record.errorCategory ?: JSONObject.NULL)

    companion object {
        private val STAGE_PATH = Regex("^/v1/sessions/([^/]+)/stage$")
        private val STATUS_PATH = Regex("^/v1/sessions/([^/]+)/stage/status$")
        private val MANIFEST_PATH = Regex("^/v1/sessions/([^/]+)/manifest$")
        private val CLEANUP_PATH = Regex("^/v1/sessions/([^/]+)/cleanup$")
    }
}
