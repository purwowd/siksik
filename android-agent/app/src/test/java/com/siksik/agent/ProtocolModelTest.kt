package com.siksik.agent

import com.siksik.agent.model.CapabilityState
import com.siksik.agent.permission.GrantState
import org.junit.Assert.assertEquals
import org.junit.Test

class ProtocolModelTest {
    @Test
    fun grantWireStatesRemainStable() {
        assertEquals(
            listOf("requested", "awaiting_user", "approved", "denied", "cancelled", "revoked"),
            GrantState.entries.map(GrantState::wireName),
        )
    }

    @Test
    fun capabilityWireStatesRemainStable() {
        assertEquals(
            listOf(
                "unavailable",
                "not_granted",
                "awaiting_user",
                "granted",
                "denied",
                "restricted",
                "error",
            ),
            CapabilityState.entries.map(CapabilityState::wireName),
        )
    }
}
