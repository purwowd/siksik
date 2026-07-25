package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.permission.GrantRecord
import com.siksik.agent.session.SessionAuthenticator
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

class GrantRoutes(
    private val authenticator: SessionAuthenticator,
    private val grants: GrantGateway,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val launchMatch = LAUNCH_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.POST && launchMatch != null) {
            request.authenticate(launchMatch.groupValues[1])
            val body = request.jsonBody(setOf("grant_id", "max_items"))
            val scope = when (launchMatch.groupValues[2]) {
                "open-photo-picker" -> "photo_picker"
                "open-directory-grant" -> "directory"
                "open-media-permission" -> "media_library"
                else -> throw ApiException("grant_unsupported", "Grant tidak didukung.", 422)
            }
            return ApiResponse.json(
                202,
                grantJson(
                    grants.launch(
                        authenticator.sessionId,
                        scope,
                        body.getString("grant_id"),
                        body.getInt("max_items"),
                    ),
                ),
            )
        }
        val statusMatch = STATUS_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && statusMatch != null) {
            request.authenticate(statusMatch.groupValues[1])
            return ApiResponse.json(
                200,
                grantJson(grants.get(authenticator.sessionId, statusMatch.groupValues[2])),
            )
        }
        return null
    }

    private fun grantJson(record: GrantRecord): JSONObject = JSONObject()
        .put("grant_id", record.grantId)
        .put("scope_type", record.scopeType)
        .put("effective_scope", record.effectiveScope ?: JSONObject.NULL)
        .put("state", record.state.wireName)
        .put("grant_ref", record.grantRef ?: JSONObject.NULL)
        .put("approved_item_count", record.approvedItemCount ?: JSONObject.NULL)
        .put("updated_at_epoch_ms", record.updatedAtEpochMs)
        .put("grant_version", record.grantVersion)

    companion object {
        private val LAUNCH_PATH = Regex(
            "^/v1/sessions/([^/]+)/(open-photo-picker|open-directory-grant|open-media-permission)$",
        )
        private val STATUS_PATH = Regex("^/v1/sessions/([^/]+)/grants/([^/]+)$")
    }
}
