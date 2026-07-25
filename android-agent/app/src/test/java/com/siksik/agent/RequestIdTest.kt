package com.siksik.agent

import com.siksik.agent.api.AgentServer
import com.siksik.agent.api.ApiRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RequestIdTest {
    @Test
    fun preservesSafeRequestId() {
        assertEquals("request_fixture", ApiRequest.normalizeRequestId("request_fixture"))
    }

    @Test
    fun replacesMissingOrUnsafeRequestId() {
        val missing = ApiRequest.normalizeRequestId(null)
        val unsafe = ApiRequest.normalizeRequestId("request id")

        assertTrue(missing.matches(Regex("^req_[0-9a-f-]{36}$")))
        assertTrue(unsafe.matches(Regex("^req_[0-9a-f-]{36}$")))
        assertNotEquals(missing, unsafe)
    }

    @Test
    fun serverHostIsLoopbackOnly() {
        assertEquals("127.0.0.1", AgentServer.HOST)
    }
}
