package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

class ApiRequest(
    val session: NanoHTTPD.IHTTPSession,
    val requestId: String,
    private val authenticator: SessionAuthenticator,
) {
    val method: NanoHTTPD.Method get() = session.method
    val uri: String get() = session.uri

    fun authenticate(pathSessionId: String? = null) {
        authenticator.authenticate(session.headers["authorization"], pathSessionId)
    }

    fun jsonBody(
        requiredKeys: Set<String>,
        optionalKeys: Set<String> = emptySet(),
    ): JSONObject {
        val contentType = session.headers["content-type"].orEmpty().substringBefore(';').trim()
        if (contentType != JSON_MEDIA_TYPE) {
            throw ApiException("validation_error", "Content-Type harus application/json.", 422)
        }
        val contentLength = session.headers["content-length"]?.toLongOrNull()
            ?: throw ApiException("validation_error", "Content-Length wajib tersedia.", 422)
        if (contentLength !in 0..MAX_BODY_BYTES) {
            throw ApiException("validation_error", "Ukuran body request tidak valid.", 422)
        }
        val files = mutableMapOf<String, String>()
        try {
            session.parseBody(files)
        } catch (_: IOException) {
            throw ApiException("validation_error", "Body request tidak dapat dibaca.", 422)
        } catch (_: NanoHTTPD.ResponseException) {
            throw ApiException("validation_error", "Body request tidak dapat diproses.", 422)
        }
        val payload = JSONObject(files["postData"] ?: "{}")
        val seen = mutableSetOf<String>()
        val keys = payload.keys()
        while (keys.hasNext()) seen.add(keys.next())
        if (!seen.containsAll(requiredKeys) || seen.any { it !in requiredKeys && it !in optionalKeys }) {
            throw ApiException("validation_error", "Field request tidak valid.", 422)
        }
        return payload
    }

    fun requiredHeader(name: String): String {
        val value = session.headers[name.lowercase()]
        if (value.isNullOrBlank() || value.length > MAX_HEADER_VALUE_LENGTH) {
            throw ApiException("validation_error", "Header $name wajib tersedia.", 422)
        }
        return value
    }

    fun validateQuery(required: Set<String>, optional: Set<String> = emptySet()) {
        val keys = session.parameters.keys
        if (!keys.containsAll(required) || keys.any { it !in required && it !in optional }) {
            throw ApiException("validation_error", "Parameter query tidak valid.", 422)
        }
    }

    fun query(name: String, required: Boolean = false): String? {
        val values = session.parameters[name]
        if (values.isNullOrEmpty()) {
            if (required) {
                throw ApiException("validation_error", "Parameter query $name wajib tersedia.", 422)
            }
            return null
        }
        if (values.size != 1 || values[0].isBlank() || values[0].length > MAX_QUERY_VALUE_LENGTH) {
            throw ApiException("validation_error", "Parameter query $name tidak valid.", 422)
        }
        return values[0]
    }

    companion object {
        private const val JSON_MEDIA_TYPE = "application/json"
        private const val MAX_BODY_BYTES = 512L * 1024L
        private const val MAX_QUERY_VALUE_LENGTH = 256
        private const val MAX_HEADER_VALUE_LENGTH = 256
        private val SAFE_REQUEST_ID = Regex("^[A-Za-z0-9_.:-]{1,128}$")

        fun normalizeRequestId(value: String?): String =
            value?.takeIf(SAFE_REQUEST_ID::matches) ?: "req_${UUID.randomUUID()}"
    }
}
