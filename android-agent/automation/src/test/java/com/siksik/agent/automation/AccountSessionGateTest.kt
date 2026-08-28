package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AccountSessionGateTest {
    private val limits = AutomationLimits(3, 2, 1_000, 250)

    @Test
    fun unsignedSessionFailsWithAccountNotSignedIn() {
        val driver = object : AutomationDriver {
            var returned = false
            override fun targetExists(targetPackage: String) = true
            override fun launch(targetPackage: String) = true
            override fun waitVisible(targetPackage: String, timeoutMs: Long) = true
            override fun waitStable(timeoutMs: Long) = Unit
            override fun isForeground(targetPackage: String) = true
            override fun navigateToScope(targetPackage: String, scope: SocialScope) = false
            override fun scrollForward() = false
            override fun captureScope(scope: SocialScope, takeScreenshot: Boolean) =
                ScopeCapture(false, null)
            override fun lastFailureReason() = "account_not_signed_in"
            override fun requireSignedInSession(targetPackage: String) = false
            override fun returnToAgent() {
                returned = true
            }
        }
        val outcome = AutomationEngine().execute(XOwnAccountStrategy(), driver, limits) { true }
        assertEquals("failed", outcome.state)
        assertEquals("account_not_signed_in", outcome.reason)
        assertEquals(0, outcome.scrollCount)
        assertTrue(driver.returned)
    }

    @Test
    fun launchTimeoutMapsAccountNotSignedInFromDriver() {
        val driver = object : AutomationDriver {
            override fun targetExists(targetPackage: String) = true
            override fun launch(targetPackage: String) = true
            override fun waitVisible(targetPackage: String, timeoutMs: Long) = false
            override fun waitStable(timeoutMs: Long) = Unit
            override fun isForeground(targetPackage: String) = false
            override fun navigateToScope(targetPackage: String, scope: SocialScope) = false
            override fun scrollForward() = false
            override fun captureScope(scope: SocialScope, takeScreenshot: Boolean) =
                ScopeCapture(false, null)
            override fun lastFailureReason() = "account_not_signed_in"
            override fun returnToAgent() = Unit
        }
        val outcome = AutomationEngine().execute(XOwnAccountStrategy(), driver, limits) { true }
        assertEquals("failed", outcome.state)
        assertEquals("account_not_signed_in", outcome.reason)
    }

    @Test
    fun instagramUnsignedSessionFailsWithAccountNotSignedIn() {
        val driver = object : AutomationDriver {
            var returned = false
            override fun targetExists(targetPackage: String) = true
            override fun launch(targetPackage: String) = true
            override fun waitVisible(targetPackage: String, timeoutMs: Long) = true
            override fun waitStable(timeoutMs: Long) = Unit
            override fun isForeground(targetPackage: String) = true
            override fun navigateToScope(targetPackage: String, scope: SocialScope) = false
            override fun scrollForward() = false
            override fun captureScope(scope: SocialScope, takeScreenshot: Boolean) =
                ScopeCapture(false, null)
            override fun lastFailureReason() = "account_not_signed_in"
            override fun requireSignedInSession(targetPackage: String) = false
            override fun returnToAgent() {
                returned = true
            }
        }
        val outcome = AutomationEngine().execute(
            InstagramOwnAccountStrategy(),
            driver,
            limits,
        ) { true }
        assertEquals("failed", outcome.state)
        assertEquals("account_not_signed_in", outcome.reason)
        assertEquals(0, outcome.scrollCount)
        assertTrue(driver.returned)
    }

    @Test
    fun facebookUnsignedSessionFailsWithAccountNotSignedIn() {
        val driver = object : AutomationDriver {
            var returned = false
            override fun targetExists(targetPackage: String) = true
            override fun launch(targetPackage: String) = true
            override fun waitVisible(targetPackage: String, timeoutMs: Long) = true
            override fun waitStable(timeoutMs: Long) = Unit
            override fun isForeground(targetPackage: String) = true
            override fun navigateToScope(targetPackage: String, scope: SocialScope) = false
            override fun scrollForward() = false
            override fun captureScope(scope: SocialScope, takeScreenshot: Boolean) =
                ScopeCapture(false, null)
            override fun lastFailureReason() = "account_not_signed_in"
            override fun requireSignedInSession(targetPackage: String) = false
            override fun returnToAgent() {
                returned = true
            }
        }
        val outcome = AutomationEngine().execute(
            FacebookOwnAccountStrategy(),
            driver,
            limits,
        ) { true }
        assertEquals("failed", outcome.state)
        assertEquals("account_not_signed_in", outcome.reason)
        assertEquals(0, outcome.scrollCount)
        assertTrue(driver.returned)
    }
}
