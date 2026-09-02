package com.siksik.agent.automation

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FacebookProfileProofPolicyTest {
    @Test
    fun classicProofRequiresEditProfileAndMetric() {
        assertTrue(
            FacebookProfileProofPolicy.hasClassicProfileProof(
                labels = listOf("Edit profile", "2 friends · 5 posts"),
                editProfileLabels = listOf("Edit profile"),
                hasMetricSignal = true,
                hasProfileChrome = false,
                hasMediaTabs = false,
            ),
        )
        assertFalse(
            FacebookProfileProofPolicy.hasClassicProfileProof(
                labels = listOf("Home"),
                editProfileLabels = listOf("Edit profile"),
                hasMetricSignal = true,
                hasProfileChrome = false,
                hasMediaTabs = false,
            ),
        )
    }

    @Test
    fun earlyWallProofAcceptsMarkerWithTabs() {
        assertTrue(
            FacebookProfileProofPolicy.hasEarlyProfileWallProof(
                labels = listOf("Saipul Tes", "Posts", "Photos"),
                marker = "Saipul Tes",
                hasProfileChrome = false,
                hasMediaTabs = true,
                hasMetricLine = false,
            ),
        )
    }

    @Test
    fun captureRequiresDisplayNameAndFriends() {
        val texts = listOf("Saipul Tes", "2 friends")
        assertTrue(
            FacebookProfileProofPolicy.captureHasDisplayName(
                texts = texts,
                marker = "Saipul Tes",
                hasProfileDisplayNameViewId = true,
            ),
        )
        assertTrue(FacebookProfileProofPolicy.captureHasFriendsMetric(texts))
    }
}
