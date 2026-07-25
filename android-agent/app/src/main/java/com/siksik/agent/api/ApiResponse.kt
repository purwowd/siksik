package com.siksik.agent.api

import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.ByteArrayInputStream

object ApiResponse {
    private const val JSON_MIME = "application/json; charset=utf-8"

    fun json(statusCode: Int, payload: JSONObject): NanoHTTPD.Response =
        NanoHTTPD.newFixedLengthResponse(status(statusCode), JSON_MIME, payload.toString()).also {
            it.addHeader("Cache-Control", "no-store")
            it.addHeader("X-Content-Type-Options", "nosniff")
        }

    fun bytes(statusCode: Int, mimeType: String, payload: ByteArray): NanoHTTPD.Response =
        NanoHTTPD.newFixedLengthResponse(
            status(statusCode),
            mimeType,
            ByteArrayInputStream(payload),
            payload.size.toLong(),
        ).also {
            it.addHeader("Cache-Control", "no-store")
            it.addHeader("X-Content-Type-Options", "nosniff")
        }

    fun error(
        statusCode: Int,
        code: String,
        message: String,
        retryable: Boolean,
        requestId: String,
    ): NanoHTTPD.Response = json(
        statusCode,
        JSONObject().put(
            "error",
            JSONObject()
                .put("code", code)
                .put("message", message)
                .put("retryable", retryable)
                .put("request_id", requestId),
        ),
    )

    private fun status(code: Int): NanoHTTPD.Response.IStatus {
        val known = NanoHTTPD.Response.Status.lookup(code)
        return known ?: FixedStatus(code, descriptions[code] ?: "$code Error")
    }

    private data class FixedStatus(
        private val code: Int,
        private val text: String,
    ) : NanoHTTPD.Response.IStatus {
        override fun getRequestStatus(): Int = code
        override fun getDescription(): String = text
    }

    private val descriptions = mapOf(
        422 to "422 Unprocessable Entity",
        507 to "507 Insufficient Storage",
    )
}
