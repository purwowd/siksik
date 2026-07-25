package com.siksik.agent.api

import android.content.Context
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.CapabilityProbe
import com.siksik.agent.preprocessing.EngineCapability
import com.siksik.agent.session.SessionAuthenticator
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

class SessionRoutes(
    context: Context,
    private val authenticator: SessionAuthenticator,
    preprocessingCapabilities: () -> Map<String, EngineCapability> = { emptyMap() },
    private val configureSelection: (JSONObject, Boolean) -> Unit = { _, _ -> },
    private val onStopRequested: () -> Unit,
) : AgentRoute {
    private val capabilityProbe = CapabilityProbe(
        context.applicationContext,
        preprocessingCapabilities,
    )

    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        if (request.method == NanoHTTPD.Method.GET && request.uri == HEALTH_PATH) {
            request.authenticate()
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("schema_version", 1)
                    .put("session_id", authenticator.sessionId)
                    .put("state", "active")
                    .put("agent_version", BuildConfig.AGENT_VERSION)
                    .put("agent_build_sha256", BuildConfig.AGENT_BUILD_SHA256)
                    .put("api_version", BuildConfig.API_VERSION)
                    .put("api_port", BuildConfig.API_PORT),
            )
        }
        if (request.method == NanoHTTPD.Method.GET && request.uri == CAPABILITIES_PATH) {
            request.authenticate()
            return ApiResponse.json(200, capabilityProbe.snapshot(authenticator.sessionId).toJson())
        }
        if (request.method == NanoHTTPD.Method.POST && request.uri == SESSIONS_PATH) {
            request.authenticate()
            val body = request.jsonBody(
                setOf("session_id", "api_version"),
                setOf("selection_policy", "review_candidates"),
            )
            request.authenticate(body.getString("session_id"))
            if (body.getString("api_version") != BuildConfig.API_VERSION) {
                throw ApiException(
                    "agent_api_mismatch",
                    "Versi API Android agent tidak sesuai.",
                    409,
                )
            }
            val hasPolicy = body.has("selection_policy")
            val hasReview = body.has("review_candidates")
            if (hasPolicy != hasReview) {
                throw ApiException(
                    "validation_error",
                    "Konfigurasi selection tidak lengkap.",
                    422,
                )
            }
            if (hasPolicy) {
                configureSelection(
                    body.getJSONObject("selection_policy"),
                    body.getBoolean("review_candidates"),
                )
            }
            return ApiResponse.json(201, sessionJson("active"))
        }
        val statusMatch = SESSION_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && statusMatch != null) {
            request.authenticate(statusMatch.groupValues[1])
            return ApiResponse.json(200, sessionJson("active"))
        }
        val stopMatch = STOP_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && stopMatch != null) {
            request.authenticate(stopMatch.groupValues[1])
            request.jsonBody(emptySet())
            onStopRequested()
            return ApiResponse.json(200, sessionJson("closed"))
        }
        return null
    }

    private fun sessionJson(state: String): JSONObject = JSONObject()
        .put("session_id", authenticator.sessionId)
        .put("api_version", BuildConfig.API_VERSION)
        .put("state", state)

    companion object {
        private const val HEALTH_PATH = "/v1/health"
        private const val CAPABILITIES_PATH = "/v1/capabilities"
        private const val SESSIONS_PATH = "/v1/sessions"
        private val SESSION_PATH = Regex("^/v1/sessions/([^/]+)$")
        private val STOP_PATH = Regex("^/v1/sessions/([^/]+)/stop$")
    }
}
