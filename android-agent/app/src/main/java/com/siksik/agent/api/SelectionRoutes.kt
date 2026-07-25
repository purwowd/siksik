package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.PreprocessingCoordinator
import com.siksik.agent.preprocessing.PreprocessingRunState
import com.siksik.agent.selection.HumanOverride
import com.siksik.agent.selection.SelectionCandidate
import com.siksik.agent.selection.SelectionCoordinator
import com.siksik.agent.selection.SelectionModelSignal
import com.siksik.agent.selection.SelectionRun
import com.siksik.agent.session.SessionAuthenticator
import fi.iki.elonen.NanoHTTPD
import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject

class SelectionRoutes(
    private val authenticator: SessionAuthenticator,
    private val preprocessing: PreprocessingCoordinator,
    private val selection: SelectionCoordinator,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val liveSelectedMatch = LIVE_SELECTED_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && liveSelectedMatch != null) {
            request.authenticate(liveSelectedMatch.groupValues[1])
            request.validateQuery(emptySet(), setOf("cursor", "limit"))
            val cursorValue = request.query("cursor")
            val cursor = cursorValue?.toLongOrNull() ?: if (cursorValue == null) {
                0L
            } else {
                throw ApiException("validation_error", "Cursor live selection tidak valid.", 422)
            }
            val limitValue = request.query("limit")
            val limit = limitValue?.toIntOrNull() ?: if (limitValue == null) {
                DEFAULT_PAGE_SIZE
            } else {
                throw ApiException("validation_error", "Batas live selection tidak valid.", 422)
            }
            if (cursor < 0 || limit !in 1..MAX_PAGE_SIZE) {
                throw ApiException("validation_error", "Halaman live selection tidak valid.", 422)
            }
            val crawlId = liveSelectedMatch.groupValues[2]
            val run = selection.status(authenticator.sessionId, crawlId)
            val page = selection.liveSelectedRecords(
                authenticator.sessionId,
                crawlId,
                cursor,
                limit,
            )
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("crawl_id", crawlId)
                    .put("siksik_session_id", authenticator.sessionId)
                    .put("selection_state", run.state.wireName)
                    .put("review_candidates", run.reviewCandidates)
                    .put(
                        "records",
                        JSONArray().apply {
                            page.records.forEach { value ->
                                put(
                                    JSONObject()
                                        .put("sequence", value.sequence)
                                        .put("candidate", candidateJson(value.candidate, run.revision))
                                        .put("record", value.payload),
                                )
                            }
                        },
                    )
                    .put("next_cursor", page.nextSequence?.toString() ?: JSONObject.NULL),
            )
        }

        val candidateMatch = CANDIDATE_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.PATCH && candidateMatch != null) {
            request.authenticate(candidateMatch.groupValues[1])
            val body = request.jsonBody(setOf("expected_revision", "override", "operator_id"))
            val revision = body.getInt("expected_revision")
            if (revision < 1) invalidRevision()
            val mutation = selection.mutate(
                authenticator.sessionId,
                candidateMatch.groupValues[2],
                candidateMatch.groupValues[3],
                revision,
                HumanOverride.fromWireName(body.getString("override")),
                body.getString("operator_id"),
            )
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("run", runJson(mutation.run))
                    .put("candidate", candidateJson(mutation.candidate, mutation.run.revision)),
            )
        }

        val candidatesMatch = CANDIDATES_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && candidatesMatch != null) {
            request.authenticate(candidatesMatch.groupValues[1])
            request.validateQuery(emptySet(), setOf("cursor", "limit"))
            val rawLimit = request.query("limit")
            val limit = rawLimit?.toIntOrNull() ?: if (rawLimit == null) {
                DEFAULT_PAGE_SIZE
            } else {
                throw ApiException("validation_error", "Batas candidate tidak valid.", 422)
            }
            if (limit !in 1..MAX_PAGE_SIZE) {
                throw ApiException("validation_error", "Batas candidate tidak valid.", 422)
            }
            val crawlId = candidatesMatch.groupValues[2]
            val run = selection.status(authenticator.sessionId, crawlId)
            val page = selection.candidates(
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
                    .put("revision", run.revision)
                    .put(
                        "selection_fingerprint",
                        run.selectionFingerprint ?: JSONObject.NULL,
                    )
                    .put(
                        "records",
                        JSONArray().apply {
                            page.candidates.forEach { put(candidateJson(it, run.revision)) }
                        },
                    )
                    .put("next_cursor", page.nextRecordId ?: JSONObject.NULL),
            )
        }

        val confirmMatch = CONFIRM_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && confirmMatch != null) {
            request.authenticate(confirmMatch.groupValues[1])
            val body = request.jsonBody(setOf("expected_revision"))
            val revision = body.getInt("expected_revision")
            if (revision < 1) invalidRevision()
            return ApiResponse.json(
                200,
                runJson(
                    selection.confirm(
                        authenticator.sessionId,
                        confirmMatch.groupValues[2],
                        revision,
                    ),
                ),
            )
        }

        val cancelMatch = CANCEL_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && cancelMatch != null) {
            request.authenticate(cancelMatch.groupValues[1])
            request.jsonBody(emptySet())
            return ApiResponse.json(
                200,
                runJson(
                    selection.cancel(
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
                val body = request.jsonBody(setOf("policy_fingerprint", "review_candidates"))
                val preprocessingRun = preprocessing.status(authenticator.sessionId, crawlId)
                if (preprocessingRun.state !in setOf(
                        PreprocessingRunState.RUNNING,
                        PreprocessingRunState.COMPLETE,
                        PreprocessingRunState.PARTIAL,
                    )
                ) {
                    throw ApiException(
                        "selection_not_ready",
                        "Preprocessing belum siap untuk selection.",
                        409,
                        true,
                    )
                }
                ApiResponse.json(
                    202,
                    runJson(
                        selection.start(
                            authenticator.sessionId,
                            crawlId,
                            body.getString("policy_fingerprint"),
                            body.getBoolean("review_candidates"),
                        ),
                    ),
                )
            }
            NanoHTTPD.Method.GET -> {
                request.validateQuery(emptySet())
                ApiResponse.json(200, runJson(selection.status(authenticator.sessionId, crawlId)))
            }
            else -> null
        }
    }

    private fun runJson(run: SelectionRun): JSONObject = JSONObject()
        .put("schema_version", 1)
        .put("crawl_id", run.crawlId)
        .put("siksik_session_id", run.sessionId)
        .put("state", run.state.wireName)
        .put("policy_version", run.policyVersion)
        .put("policy_fingerprint", run.policyFingerprint)
        .put("revision", run.revision)
        .put("selection_fingerprint", run.selectionFingerprint ?: JSONObject.NULL)
        .put("review_candidates", run.reviewCandidates)
        .put(
            "totals",
            JSONObject()
                .put("total", run.totals.total)
                .put("evaluated", run.totals.evaluated)
                .put("candidates", run.totals.candidates)
                .put("auto_selected", run.totals.autoSelected)
                .put("selected", run.totals.selected)
                .put("below_threshold", run.totals.belowThreshold)
                .put("selected_bytes", run.totals.selectedBytes),
        )
        .put("started_at", timestamp(run.startedAtEpochMs))
        .put("updated_at", timestamp(run.updatedAtEpochMs))
        .put("frozen_at", nullableTimestamp(run.frozenAtEpochMs))
        .put("confirmed_at", nullableTimestamp(run.confirmedAtEpochMs))
        .put("failure_reason", run.failureReason ?: JSONObject.NULL)

    private fun candidateJson(candidate: SelectionCandidate, revision: Int): JSONObject =
        JSONObject()
            .put("record_id", candidate.recordId)
            .put("source_kind", candidate.sourceKind)
            .put("source_app", candidate.sourceApp ?: JSONObject.NULL)
            .put("evidence_text", candidate.evidenceText ?: JSONObject.NULL)
            .put("score", candidate.scoreBasisPoints / 10_000.0)
            .put("threshold", candidate.thresholdBasisPoints / 10_000.0)
            .put("auto_selected", candidate.autoSelected)
            .put("selected", candidate.selected)
            .put("matched_keywords", JSONArray(candidate.matchedKeywords))
            .put("matched_rules", JSONArray(candidate.matchedRules))
            .put(
                "model_signals",
                JSONArray().apply {
                    candidate.modelSignals.forEach { put(modelSignalJson(it)) }
                },
            )
            .put("reasons", JSONArray(candidate.reasons))
            .put("human_override", candidate.humanOverride.wireName)
            .put("operator_id", candidate.operatorId ?: JSONObject.NULL)
            .put("revision", revision)
            .put("decided_at", timestamp(candidate.decidedAtEpochMs))
            .put("duplicate_group_id", candidate.duplicateGroupId ?: JSONObject.NULL)
            .put(
                "representative_record_id",
                candidate.representativeRecordId ?: JSONObject.NULL,
            )
            .put("size_bytes", candidate.sizeBytes ?: JSONObject.NULL)
            .put("thumbnail_available", candidate.thumbnailAvailable)

    private fun modelSignalJson(signal: SelectionModelSignal): JSONObject = JSONObject()
        .put("signal", signal.signal)
        .put("value", signal.value)
        .put("weight_basis_points", signal.weightBasisPoints)

    private fun invalidRevision(): Nothing = throw ApiException(
        "validation_error",
        "Revision selection tidak valid.",
        422,
    )

    private fun timestamp(value: Long): String = Instant.ofEpochMilli(value).toString()

    private fun nullableTimestamp(value: Long?): Any = value?.let(::timestamp) ?: JSONObject.NULL

    companion object {
        private const val DEFAULT_PAGE_SIZE = 50
        private const val MAX_PAGE_SIZE = 100
        private val RUN_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection$",
        )
        private val CANDIDATES_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection/candidates$",
        )
        private val LIVE_SELECTED_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection/live-selected-records$",
        )
        private val CANDIDATE_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection/candidates/([^/]+)$",
        )
        private val CONFIRM_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection/confirm$",
        )
        private val CANCEL_PATH = Regex(
            "^/v1/sessions/([^/]+)/crawl/([^/]+)/selection/cancel$",
        )
    }
}
