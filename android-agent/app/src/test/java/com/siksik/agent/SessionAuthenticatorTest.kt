package com.siksik.agent

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SessionAuthenticatorTest {
    private val token = "token_abcdefghijklmnopqrstuvwxyz0123456789"

    @Test
    fun acceptsMatchingActiveSession() {
        SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }
            .authenticate("Bearer $token", "session_fixture")
    }

    @Test
    fun rejectsMissingToken() {
        assertCode("agent_auth_missing") {
            SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }
                .authenticate(null, "session_fixture")
        }
    }

    @Test
    fun rejectsInvalidToken() {
        assertCode("agent_auth_invalid") {
            SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }
                .authenticate("Bearer token_invalid_abcdefghijklmnopqrstuvwxyz", "session_fixture")
        }
    }

    @Test
    fun rejectsExpiredToken() {
        assertCode("agent_auth_expired") {
            SessionAuthenticator("session_fixture", token, 1_000) { 1_000 }
                .authenticate("Bearer $token", "session_fixture")
        }
    }

    @Test
    fun rejectsCrossSessionToken() {
        assertCode("agent_session_mismatch") {
            SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }
                .authenticate("Bearer $token", "session_different")
        }
    }

    @Test
    fun destroyedAuthenticatorRejectsOriginalToken() {
        val authenticator = SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }
        authenticator.destroy()

        assertCode("agent_auth_invalid") {
            authenticator.authenticate("Bearer $token", "session_fixture")
        }
    }

    @Test
    fun matchesOnlyTheSameBootstrapPayload() {
        val authenticator = SessionAuthenticator("session_fixture", token, 2_000) { 1_000 }

        assertEquals(true, authenticator.matches("session_fixture", token, 2_000))
        assertEquals(false, authenticator.matches("session_other", token, 2_000))
        assertEquals(false, authenticator.matches("session_fixture", "x".repeat(40), 2_000))
        assertEquals(false, authenticator.matches("session_fixture", token, 3_000))
    }

    private fun assertCode(expected: String, action: () -> Unit) {
        val exception = assertThrows(ApiException::class.java, action)
        assertEquals(expected, exception.code)
    }
}
