package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.staging.CrawlTransferManager
import com.siksik.agent.staging.CrawlTransferRecord
import com.siksik.agent.staging.CrawlTransferRequest
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

class TransferRoutes(
    private val authenticator: SessionAuthenticator,
    private val transfer: CrawlTransferManager,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val startMatch = TRANSFER_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && startMatch != null) {
            val sessionId = startMatch.groupValues[1]
            val crawlId = startMatch.groupValues[2]
            request.authenticate(sessionId)
            val idempotencyKey = request.requiredHeader("idempotency-key")
            val body = request.jsonBody(
                setOf("stage_id", "selection_revision", "selection_fingerprint"),
            )
            val value = transfer.start(
                authenticator.sessionId,
                CrawlTransferRequest(
                    stageId = body.getString("stage_id"),
                    crawlId = crawlId,
                    selectionRevision = body.getInt("selection_revision"),
                    selectionFingerprint = body.getString("selection_fingerprint"),
                ),
                idempotencyKey,
            )
            return ApiResponse.json(202, statusJson(value))
        }
        val statusMatch = TRANSFER_STATUS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && statusMatch != null) {
            val sessionId = statusMatch.groupValues[1]
            val crawlId = statusMatch.groupValues[2]
            request.authenticate(sessionId)
            request.validateQuery(setOf("stage_id"))
            return ApiResponse.json(
                200,
                statusJson(
                    transfer.status(
                        authenticator.sessionId,
                        crawlId,
                        request.query("stage_id", required = true)!!,
                    ),
                ),
            )
        }
        val manifestMatch = TRANSFER_MANIFEST_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && manifestMatch != null) {
            val sessionId = manifestMatch.groupValues[1]
            val crawlId = manifestMatch.groupValues[2]
            request.authenticate(sessionId)
            request.validateQuery(setOf("stage_id"))
            val value = transfer.status(
                authenticator.sessionId,
                crawlId,
                request.query("stage_id", required = true)!!,
            )
            if (value.state != "completed" || value.manifestRelativePath == null) {
                throw ApiException("stage_not_ready", "Manifest transfer belum selesai.", 409, true)
            }
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("stage_id", value.stageId)
                    .put("crawl_id", value.crawlId)
                    .put("selection_revision", value.selectionRevision)
                    .put("selection_fingerprint", value.selectionFingerprint)
                    .put("bundle_format", "direct_manifest_files_v1")
                    .put("manifest_relative_path", value.manifestRelativePath)
                    .put("manifest_size_bytes", value.manifestSizeBytes)
                    .put("manifest_sha256", value.manifestSha256),
            )
        }
        val cleanupMatch = TRANSFER_CLEANUP_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && cleanupMatch != null) {
            val sessionId = cleanupMatch.groupValues[1]
            val crawlId = cleanupMatch.groupValues[2]
            request.authenticate(sessionId)
            request.requiredHeader("idempotency-key")
            val body = request.jsonBody(setOf("stage_id"))
            val value = transfer.cleanup(
                authenticator.sessionId,
                crawlId,
                body.getString("stage_id"),
            )
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("receipt_id", value.cleanupReceiptId)
                    .put("stage_id", value.stageId)
                    .put("crawl_id", value.crawlId)
                    .put("deleted_files", value.cleanupDeletedFiles)
                    .put("already_absent", value.cleanupAlreadyAbsent)
                    .put("deleted_at_epoch_ms", value.cleanupEpochMs),
            )
        }
        return null
    }

    private fun statusJson(value: CrawlTransferRecord): JSONObject = JSONObject()
        .put("schema_version", 1)
        .put("stage_id", value.stageId)
        .put("crawl_id", value.crawlId)
        .put("state", value.state)
        .put("selection_revision", value.selectionRevision)
        .put("selection_fingerprint", value.selectionFingerprint)
        .put("total_records", value.totalRecords)
        .put("completed_records", value.completedRecords)
        .put("artifact_count", value.artifactCount)
        .put("total_bytes", value.totalBytes)
        .put("error_category", value.errorCategory ?: JSONObject.NULL)

    companion object {
        private val TRANSFER_PATH = Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)/transfer$")
        private val TRANSFER_STATUS_PATH =
            Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)/transfer/status$")
        private val TRANSFER_MANIFEST_PATH =
            Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)/transfer/manifest$")
        private val TRANSFER_CLEANUP_PATH =
            Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)/transfer/cleanup$")
    }
}
