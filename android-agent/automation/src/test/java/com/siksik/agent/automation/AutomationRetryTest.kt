package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Test

class AutomationRetryTest {
    private val limits = AutomationLimits(1, 0, 1_000, 250)

    @Test
    fun fourthAttemptCompletesAfterThreeRetryableFailures() {
        val driver = RetryDriver(failuresBeforeSuccess = 3, recoverySucceeds = true)

        val result = AutomationEngine(sequenceClock()).execute(
            XOwnAccountStrategy(),
            driver,
            limits,
        ) { true }

        assertEquals("complete", result.state)
        assertEquals(4, driver.profileAttempts)
        assertEquals(3, driver.recoveryCalls)
    }

    @Test
    fun recoveryFailureStillUsesAllFourBoundedAttempts() {
        val driver = RetryDriver(failuresBeforeSuccess = 4, recoverySucceeds = false)

        AutomationEngine(sequenceClock()).execute(
            XOwnAccountStrategy(),
            driver,
            limits,
        ) { true }

        assertEquals(4, driver.profileAttempts)
        assertEquals(3, driver.recoveryCalls)
    }

    private fun sequenceClock(): () -> Long {
        var value = 1_000L
        return { value.also { value += 10L } }
    }

    private class RetryDriver(
        private val failuresBeforeSuccess: Int,
        private val recoverySucceeds: Boolean,
    ) : AutomationDriver {
        var profileAttempts = 0
        var recoveryCalls = 0
        private var remainingFailures = failuresBeforeSuccess
        private var failureReason: String? = null

        override fun targetExists(targetPackage: String) = true
        override fun launch(targetPackage: String) = true
        override fun waitVisible(targetPackage: String, timeoutMs: Long) = true
        override fun waitStable(timeoutMs: Long) = Unit
        override fun isForeground(targetPackage: String) = true

        override fun navigateToScope(targetPackage: String, scope: SocialScope): Boolean {
            if (scope == SocialScope.OWN_PROFILE) {
                profileAttempts += 1
                if (remainingFailures > 0) {
                    remainingFailures -= 1
                    failureReason = "scope_navigation_failed"
                    return false
                }
            }
            failureReason = null
            return true
        }

        override fun scrollForward() = false

        override fun captureScope(scope: SocialScope, takeScreenshot: Boolean) =
            ScopeCapture(stored = true, screenshotId = null, exhausted = true)

        override fun lastFailureReason(): String? = failureReason

        override fun recoverScope(
            targetPackage: String,
            scope: SocialScope,
            failedAttempt: Int,
            reason: String,
        ): Boolean {
            recoveryCalls += 1
            return recoverySucceeds
        }

        override fun returnToAgent() = Unit
    }
}
