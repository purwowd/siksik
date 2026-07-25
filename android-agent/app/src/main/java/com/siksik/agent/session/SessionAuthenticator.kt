package com.siksik.agent.session

import com.siksik.agent.model.ApiException
import java.security.MessageDigest
import java.util.Arrays

class SessionAuthenticator(
    val sessionId: String,
    token: String,
    val expiresAtEpochMs: Long,
    private val clock: () -> Long = { System.currentTimeMillis() },
) {
    private val expectedToken = token.toByteArray(Charsets.UTF_8)

    init {
        require(SAFE_ID.matches(sessionId)) { "invalid session id" }
        require(token.length in MIN_TOKEN_LENGTH..MAX_TOKEN_LENGTH) { "invalid session token" }
        require(expiresAtEpochMs > 0) { "invalid token expiry" }
    }

    @Synchronized
    fun authenticate(authorization: String?, pathSessionId: String? = null) {
        if (authorization.isNullOrBlank() || !authorization.startsWith(BEARER_PREFIX)) {
            throw ApiException("agent_auth_missing", "Token otorisasi diperlukan.", 401)
        }
        if (clock() >= expiresAtEpochMs) {
            throw ApiException("agent_auth_expired", "Token sesi sudah kedaluwarsa.", 401)
        }
        val supplied = authorization.removePrefix(BEARER_PREFIX).toByteArray(Charsets.UTF_8)
        if (!MessageDigest.isEqual(expectedToken, supplied)) {
            throw ApiException("agent_auth_invalid", "Token sesi tidak valid.", 401)
        }
        if (pathSessionId != null && pathSessionId != sessionId) {
            throw ApiException(
                "agent_session_mismatch",
                "ID sesi tidak sesuai dengan sesi aktif.",
                409,
            )
        }
    }

    @Synchronized
    fun matches(sessionId: String, token: String, expiresAtEpochMs: Long): Boolean {
        if (this.sessionId != sessionId || this.expiresAtEpochMs != expiresAtEpochMs) {
            return false
        }
        return MessageDigest.isEqual(
            expectedToken,
            token.toByteArray(Charsets.UTF_8),
        )
    }

    @Synchronized
    fun destroy() {
        Arrays.fill(expectedToken, 0)
    }

    companion object {
        const val MIN_TOKEN_LENGTH = 32
        const val MAX_TOKEN_LENGTH = 512
        private const val BEARER_PREFIX = "Bearer "
        val SAFE_ID = Regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
    }
}
