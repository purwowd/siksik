package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.communication.AutomationTargetResult
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.inventory.InventoryController
import com.siksik.agent.source.inventory.InventoryMode
import com.siksik.agent.source.inventory.InventoryPage
import com.siksik.agent.source.inventory.InventoryRecordJson
import com.siksik.agent.source.inventory.InventoryRun
import com.siksik.agent.source.inventory.InventorySourceProgress
import com.siksik.agent.source.inventory.InventorySourceState
import com.siksik.agent.source.inventory.SourceAdapter
import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

class InventoryRoutes(
    private val authenticator: SessionAuthenticator,
    private val inventory: InventoryController,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val automationMatch = AUTOMATION_RESULT_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && automationMatch != null) {
            request.authenticate(automationMatch.groupValues[1])
            val body = request.jsonBody(
                setOf(
                    "target_package",
                    "state",
                    "reason",
                    "scroll_count",
                    "screenshot_ids",
                    "duration_ms",
                ),
            )
            val target = body.getString("target_package")
            if (target !in CommunicationPolicy.supportedSocialTargets) {
                throw ApiException("validation_error", "Target automation tidak valid.", 422)
            }
            val state = body.getString("state")
            if (state !in AUTOMATION_STATES) {
                throw ApiException("validation_error", "Status automation tidak valid.", 422)
            }
            val reason = if (body.isNull("reason")) null else body.getString("reason").also {
                if (!SAFE_REASON.matches(it)) {
                    throw ApiException("validation_error", "Alasan automation tidak valid.", 422)
                }
            }
            val scrollCount = body.getInt("scroll_count")
            val durationMs = body.getLong("duration_ms")
            if (scrollCount !in 0..100 || durationMs !in 0..3_600_000) {
                throw ApiException("validation_error", "Batas automation tidak valid.", 422)
            }
            val screenshotValues = body.getJSONArray("screenshot_ids")
            if (screenshotValues.length() > 48) {
                throw ApiException("validation_error", "Jumlah screenshot tidak valid.", 422)
            }
            val screenshotIds = buildList {
                for (index in 0 until screenshotValues.length()) {
                    val id = screenshotValues.getString(index)
                    validateIdentifier(id)
                    add(id)
                }
            }
            val run = inventory.reportAutomationResult(
                authenticator.sessionId,
                automationMatch.groupValues[2],
                AutomationTargetResult(
                    target,
                    state,
                    reason,
                    scrollCount,
                    screenshotIds,
                    durationMs,
                ),
            )
            return ApiResponse.json(200, runJson(run))
        }

        val recordsMatch = RECORDS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && recordsMatch != null) {
            request.authenticate(recordsMatch.groupValues[1])
            request.validateQuery(setOf("source"), setOf("cursor", "limit"))
            val source = parseSource(request.query("source", required = true)!!)
            val rawLimit = request.query("limit")
            val limit = rawLimit?.toIntOrNull() ?: if (rawLimit == null) {
                DEFAULT_PAGE_SIZE
            } else {
                throw ApiException("validation_error", "Batas halaman inventory tidak valid.", 422)
            }
            val page = inventory.page(
                authenticator.sessionId,
                recordsMatch.groupValues[2],
                source,
                request.query("cursor"),
                limit,
            )
            return ApiResponse.json(200, pageJson(authenticator.sessionId, page))
        }

        val actionMatch = ACTION_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && actionMatch != null) {
            request.authenticate(actionMatch.groupValues[1])
            request.jsonBody(emptySet())
            val run = when (actionMatch.groupValues[3]) {
                "cancel" -> inventory.cancel(
                    authenticator.sessionId,
                    actionMatch.groupValues[2],
                )
                "resume" -> inventory.resume(
                    authenticator.sessionId,
                    actionMatch.groupValues[2],
                )
                else -> throw ApiException("not_found", "Aksi crawl tidak ditemukan.", 404)
            }
            return ApiResponse.json(200, runJson(run))
        }

        val statusMatch = STATUS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && statusMatch != null) {
            request.authenticate(statusMatch.groupValues[1])
            request.validateQuery(emptySet())
            return ApiResponse.json(
                200,
                runJson(
                    inventory.status(
                        authenticator.sessionId,
                        statusMatch.groupValues[2],
                    ),
                ),
            )
        }

        val crawlMatch = CRAWL_PATH.matchEntire(request.uri)
        if (crawlMatch != null) {
            request.authenticate(crawlMatch.groupValues[1])
            if (request.method == NanoHTTPD.Method.POST) {
                val body = request.jsonBody(
                    setOf(
                        "mode",
                        "document_grant_id",
                        "target_packages",
                        "source_adapters",
                    ),
                )
                val modeValue = body.getString("mode")
                val mode = InventoryMode.entries.firstOrNull { it.wireName == modeValue }
                    ?: throw ApiException(
                        "validation_error",
                        "Mode inventory tidak valid.",
                        422,
                    )
                val documentGrantId = if (body.isNull("document_grant_id")) {
                    null
                } else {
                    body.getString("document_grant_id").also(::validateIdentifier)
                }
                val targetPackages = if (!body.has("target_packages") || body.isNull("target_packages")) {
                    emptySet()
                } else {
                    val values = body.getJSONArray("target_packages")
                    buildSet {
                        for (index in 0 until values.length()) {
                            add(values.getString(index))
                        }
                    }.let {
                        try {
                            CommunicationPolicy.validateTargets(it)
                        } catch (_: IllegalArgumentException) {
                            throw ApiException(
                                "validation_error",
                                "Target social tidak valid.",
                                422,
                            )
                        }
                    }
                }
                val sourceAdapters = if (
                    !body.has("source_adapters") || body.isNull("source_adapters")
                ) {
                    SourceAdapter.entries.toSet()
                } else {
                    val values = body.getJSONArray("source_adapters")
                    buildSet {
                        for (index in 0 until values.length()) {
                            val wireName = values.getString(index)
                            val adapter = SourceAdapter.entries.firstOrNull {
                                it.wireName == wireName
                            } ?: throw ApiException(
                                "validation_error",
                                "Sumber inventory tidak valid.",
                                422,
                            )
                            if (!add(adapter)) {
                                throw ApiException(
                                    "validation_error",
                                    "Sumber inventory duplikat.",
                                    422,
                                )
                            }
                        }
                    }
                }
                return ApiResponse.json(
                    201,
                    runJson(
                        inventory.start(
                            authenticator.sessionId,
                            mode,
                            documentGrantId,
                            targetPackages,
                            sourceAdapters,
                        ),
                    ),
                )
            }
            if (request.method == NanoHTTPD.Method.GET) {
                request.validateQuery(emptySet())
                return ApiResponse.json(
                    200,
                    runJson(inventory.latest(authenticator.sessionId)),
                )
            }
        }
        return null
    }

    private fun runJson(run: InventoryRun): JSONObject {
        val progress = JSONObject()
        val partialReasons = JSONArray()
        val resumeCursors = JSONObject()
        run.sources.forEach { source ->
            progress.put(source.source.wireName, progressJson(source))
            if (
                source.reason != null &&
                source.state in ISSUE_SOURCE_STATES
            ) {
                partialReasons.put(
                    JSONObject()
                        .put("source", source.source.wireName)
                        .put("state", source.state.wireName)
                        .put("reason", source.reason),
                )
            }
            if (source.resumeCursor != null) {
                resumeCursors.put(source.source.wireName, source.resumeCursor)
            }
        }
        return JSONObject()
            .put("schema_version", 1)
            .put("crawl_id", run.crawlId)
            .put("siksik_session_id", run.sessionId)
            .put("mode", run.mode.wireName)
            .put("state", run.state.wireName)
            .put("started_at", timestamp(run.startedAtEpochMs))
            .put("updated_at", timestamp(run.updatedAtEpochMs))
            .put("completed_at", nullableTimestamp(run.completedAtEpochMs))
            .put("source_progress", progress)
            .put(
                "totals",
                JSONObject()
                    .put("scanned", run.sources.sumOf(InventorySourceProgress::scannedCount))
                    .put(
                        "discovered",
                        run.sources.sumOf(InventorySourceProgress::discoveredCount),
                    )
                    .put(
                        "duplicates",
                        run.sources.sumOf(InventorySourceProgress::duplicateCount),
                    ),
            )
            .put("partial_reasons", partialReasons)
            .put("resume_cursors", resumeCursors)
    }

    private fun progressJson(progress: InventorySourceProgress): JSONObject = JSONObject()
        .put("state", progress.state.wireName)
        .put("scanned_count", progress.scannedCount)
        .put("discovered_count", progress.discoveredCount)
        .put("duplicate_count", progress.duplicateCount)
        .put("sampled", progress.sampled)
        .put("reason", progress.reason ?: JSONObject.NULL)
        .put("resume_cursor", progress.resumeCursor ?: JSONObject.NULL)

    private fun pageJson(sessionId: String, page: InventoryPage): JSONObject {
        val records = JSONArray()
        page.records.forEach {
            records.put(InventoryRecordJson.encode(sessionId, page.crawlId, it))
        }
        return JSONObject()
            .put("schema_version", 1)
            .put("crawl_id", page.crawlId)
            .put("siksik_session_id", sessionId)
            .put("source_adapter", page.source.wireName)
            .put("source_state", page.sourceState.wireName)
            .put("source_reason", page.sourceReason ?: JSONObject.NULL)
            .put("sampled", page.sampled)
            .put("scanned_count", page.scannedCount)
            .put("discovered_count", page.discoveredCount)
            .put("duplicate_count", page.duplicateCount)
            .put("records", records)
            .put("next_cursor", page.nextCursor ?: JSONObject.NULL)
    }

    private fun parseSource(value: String): SourceAdapter =
        SourceAdapter.entries.firstOrNull { it.wireName == value }
            ?: throw ApiException("validation_error", "Sumber inventory tidak valid.", 422)

    private fun validateIdentifier(value: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(value)) {
            throw ApiException("validation_error", "ID grant tidak valid.", 422)
        }
    }

    private fun timestamp(epochMs: Long): String = Instant.ofEpochMilli(epochMs).toString()

    private fun nullableTimestamp(epochMs: Long?): Any =
        epochMs?.let(::timestamp) ?: JSONObject.NULL

    companion object {
        private const val DEFAULT_PAGE_SIZE = 50
        private val ISSUE_SOURCE_STATES = setOf(
            InventorySourceState.PARTIAL,
            InventorySourceState.DENIED,
            InventorySourceState.RESTRICTED,
            InventorySourceState.UNSUPPORTED,
            InventorySourceState.CANCELLED,
            InventorySourceState.FAILED,
        )
        private val CRAWL_PATH = Regex("^/v1/sessions/([^/]+)/crawl$")
        private val STATUS_PATH = Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)$")
        private val RECORDS_PATH = Regex("^/v1/sessions/([^/]+)/crawl/([^/]+)/records$")
        private val ACTION_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/(cancel|resume)$",
        )
        private val AUTOMATION_RESULT_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/automation-results$",
        )
        private val SAFE_REASON = Regex("^[a-z0-9_]{1,128}$")
        private val AUTOMATION_STATES = setOf(
            "complete",
            "partial",
            "cancelled",
            "failed",
            "target_missing",
            "timeout",
        )
    }
}
