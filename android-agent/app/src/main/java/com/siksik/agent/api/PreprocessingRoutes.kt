package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.PreprocessingCoordinator
import com.siksik.agent.preprocessing.PreprocessingRun
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.inventory.InventoryController
import com.siksik.agent.source.inventory.InventoryRunState
import fi.iki.elonen.NanoHTTPD
import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject

class PreprocessingRoutes(
    private val authenticator: SessionAuthenticator,
    private val inventory: InventoryController,
    private val preprocessing: PreprocessingCoordinator,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val recordsMatch = RECORDS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && recordsMatch != null) {
            request.authenticate(recordsMatch.groupValues[1])
            request.validateQuery(emptySet(), setOf("cursor", "limit"))
            val limitValue = request.query("limit")
            val limit = limitValue?.toIntOrNull() ?: if (limitValue == null) {
                DEFAULT_PAGE_SIZE
            } else {
                throw ApiException("validation_error", "Batas record preprocessing tidak valid.", 422)
            }
            if (limit !in 1..MAX_PAGE_SIZE) {
                throw ApiException("validation_error", "Batas record preprocessing tidak valid.", 422)
            }
            val crawlId = recordsMatch.groupValues[2]
            val page = preprocessing.records(
                authenticator.sessionId,
                crawlId,
                request.query("cursor"),
                limit,
            )
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("crawl_id", crawlId)
                    .put("siksik_session_id", authenticator.sessionId)
                    .put("records", JSONArray(page.records))
                    .put("next_cursor", page.nextCursor ?: JSONObject.NULL),
            )
        }

        val cancelMatch = CANCEL_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && cancelMatch != null) {
            request.authenticate(cancelMatch.groupValues[1])
            request.jsonBody(emptySet())
            return ApiResponse.json(
                200,
                runJson(
                    preprocessing.cancel(
                        authenticator.sessionId,
                        cancelMatch.groupValues[2],
                    ),
                ),
            )
        }

        val runMatch = RUN_PATH.matchEntire(request.uri) ?: return null
        request.authenticate(runMatch.groupValues[1])
        val crawlId = runMatch.groupValues[2]
        return when (request.method) {
            NanoHTTPD.Method.POST -> {
                request.jsonBody(emptySet())
                val crawl = inventory.status(authenticator.sessionId, crawlId)
                if (crawl.state !in setOf(InventoryRunState.COMPLETE, InventoryRunState.PARTIAL)) {
                    throw ApiException(
                        "preprocessing_not_ready",
                        "Inventory crawl belum siap diproses.",
                        409,
                        true,
                    )
                }
                ApiResponse.json(
                    202,
                    runJson(
                        preprocessing.start(
                            authenticator.sessionId,
                            crawlId,
                            crawl.mode,
                        ),
                    ),
                )
            }
            NanoHTTPD.Method.GET -> {
                request.validateQuery(emptySet())
                ApiResponse.json(200, runJson(preprocessing.status(authenticator.sessionId, crawlId)))
            }
            else -> null
        }
    }

    private fun runJson(run: PreprocessingRun): JSONObject = JSONObject()
        .put("schema_version", 1)
        .put("crawl_id", run.crawlId)
        .put("siksik_session_id", run.sessionId)
        .put("state", run.state.wireName)
        .put("started_at", timestamp(run.startedAtEpochMs))
        .put("updated_at", timestamp(run.updatedAtEpochMs))
        .put("completed_at", run.completedAtEpochMs?.let(::timestamp) ?: JSONObject.NULL)
        .put("deadline_at", timestamp(run.deadlineAtEpochMs))
        .put(
            "totals",
            JSONObject()
                .put("total", run.totals.total)
                .put("pending", run.totals.pending)
                .put("processing", run.totals.processing)
                .put("completed", run.totals.completed)
                .put("skipped", run.totals.skipped)
                .put("truncated", run.totals.truncated)
                .put("failed", run.totals.failed)
                .put("cancelled", run.totals.cancelled),
        )
        .put(
            "preprocessor_totals",
            JSONObject().apply {
                run.preprocessorTotals.forEach { (name, totals) ->
                    put(
                        name,
                        JSONObject()
                            .put("attempted", totals.attempted)
                            .put("processed", totals.processed)
                            .put("skipped", totals.skipped)
                            .put("truncated", totals.truncated)
                            .put("failed", totals.failed)
                            .put("cancelled", totals.cancelled),
                    )
                }
            },
        )
        .put("partial_reasons", JSONArray(run.partialReasons))

    private fun timestamp(epochMs: Long): String = Instant.ofEpochMilli(epochMs).toString()

    companion object {
        private const val DEFAULT_PAGE_SIZE = 10
        private const val MAX_PAGE_SIZE = 20
        private val RUN_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/preprocessing$",
        )
        private val RECORDS_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/preprocessing/records$",
        )
        private val CANCEL_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/preprocessing/cancel$",
        )
    }
}
