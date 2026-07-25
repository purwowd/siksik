package com.siksik.agent.session

data class BootstrapPayload(
    val sessionId: String,
    val token: String,
    val expiresAtEpochMs: Long,
)

object BootstrapValidator {
    private const val MAX_TOKEN_TTL_MS = 60L * 60L * 1000L
    private const val MAX_CLOCK_SKEW_MS = 5L * 60L * 1000L

    fun validate(
        sessionId: String?,
        token: String?,
        expiresAtEpochMs: Long,
        nowEpochMs: Long = System.currentTimeMillis(),
    ): BootstrapPayload? {
        if (
            sessionId == null ||
            token == null ||
            !SessionAuthenticator.SAFE_ID.matches(sessionId) ||
            token.length !in SessionAuthenticator.MIN_TOKEN_LENGTH..SessionAuthenticator.MAX_TOKEN_LENGTH ||
            token.any(Char::isISOControl) ||
            expiresAtEpochMs <= nowEpochMs ||
            expiresAtEpochMs - nowEpochMs > MAX_TOKEN_TTL_MS + MAX_CLOCK_SKEW_MS
        ) {
            return null
        }
        return BootstrapPayload(sessionId, token, expiresAtEpochMs)
    }
}
