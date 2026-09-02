package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScopeFailurePolicyTest {
    @Test
    fun classifiesExtractionFailuresAsObservation() {
        assertEquals(
            ScopeFailureClass.OBSERVATION,
            ScopeFailurePolicy.classify("x_timeline_extraction_failed"),
        )
        assertEquals(
            ScopeFailureClass.OBSERVATION,
            ScopeFailurePolicy.classify("x_own_content_not_visible"),
        )
        assertEquals(
            ScopeFailureClass.OBSERVATION,
            ScopeFailurePolicy.classify("facebook_timeline_extraction_failed"),
        )
    }

    @Test
    fun classifiesVerificationFailuresAsPostcondition() {
        assertEquals(
            ScopeFailureClass.POSTCONDITION,
            ScopeFailurePolicy.classify("x_profile_not_verified"),
        )
        assertEquals(
            ScopeFailureClass.ACTION,
            ScopeFailurePolicy.classify("x_replies_control_missing"),
        )
    }

    @Test
    fun accountNotSignedInIsNotRetryable() {
        assertFalse(ScopeFailurePolicy.isRetryable("account_not_signed_in"))
        assertTrue(ScopeFailurePolicy.isRetryable("x_timeline_extraction_failed"))
    }

    @Test
    fun facebookNavigationFailuresAreRetryable() {
        assertTrue(ScopeFailurePolicy.isRetryable("facebook_navigation_deadline"))
        assertTrue(ScopeFailurePolicy.isRetryable("facebook_navigation_stalled"))
        assertTrue(ScopeFailurePolicy.isRetryable("facebook_all_posts_missing"))
        assertTrue(ScopeFailurePolicy.isRetryable("facebook_profile_not_verified"))
    }

    @Test
    fun observationFailuresPreferGentleRecoveryFirst() {
        assertEquals(
            ScopeFailurePolicy.RecoveryTier.IN_APP,
            ScopeFailurePolicy.recoveryTier("x_timeline_extraction_failed", 1),
        )
        assertEquals(
            ScopeFailurePolicy.RecoveryTier.RELAUNCH,
            ScopeFailurePolicy.recoveryTier("x_timeline_extraction_failed", 2),
        )
        assertEquals(
            ScopeFailurePolicy.RecoveryTier.FORCE_STOP,
            ScopeFailurePolicy.recoveryTier("x_timeline_extraction_failed", 3),
        )
    }
}
