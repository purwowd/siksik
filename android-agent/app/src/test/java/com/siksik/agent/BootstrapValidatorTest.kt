package com.siksik.agent

import com.siksik.agent.session.BootstrapValidator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BootstrapValidatorTest {
    private val token = "token_abcdefghijklmnopqrstuvwxyz0123456789"

    @Test
    fun acceptsBoundedFutureBootstrap() {
        val payload = BootstrapValidator.validate("session_fixture", token, 61_000, 1_000)

        assertEquals("session_fixture", payload?.sessionId)
    }

    @Test
    fun rejectsMalformedOrExpiredBootstrap() {
        assertNull(BootstrapValidator.validate("bad", token, 61_000, 1_000))
        assertNull(BootstrapValidator.validate("session_fixture", "short", 61_000, 1_000))
        assertNull(BootstrapValidator.validate("session_fixture", token, 1_000, 1_000))
    }

    @Test
    fun rejectsControlCharactersAndExcessiveTtl() {
        assertNull(
            BootstrapValidator.validate(
                "session_fixture",
                "token_abcdefghijklmnopqrstuvwxyz01234\u0000",
                61_000,
                1_000,
            ),
        )
        assertEquals(
            "session_fixture",
            BootstrapValidator.validate("session_fixture", token, 3_601_001, 1_000)?.sessionId,
        )
        assertNull(BootstrapValidator.validate("session_fixture", token, 3_901_001, 1_000))
    }
}
