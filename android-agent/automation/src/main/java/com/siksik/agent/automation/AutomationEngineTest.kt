package com.siksik.agent.automation

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AutomationEngineTest {
    private val limits = AutomationLimits(3, 2, 1_000, 250)
    private val strategy = InstagramOwnAccountStrategy()

    @Test
    fun launchWaitScrollScreenshotAndReturnAreBounded() {
        val driver = FakeDriver()
        val result = AutomationEngine(sequenceClock()).execute(strategy, driver, limits) { true }

        assertEquals("complete", result.state)
        assertEquals(6, result.scrollCount)
        assertEquals(2, result.screenshotIds.size)
        assertTrue(driver.returnCalled)
        assertEquals(6, driver.scrollCalls)
        assertEquals(strategy.scopes, driver.openedScopes)
    }

    @Test
    fun targetMissingChangedUiCancellationAndTimeoutAreExplicit() {
        val missingDriver = FakeDriver(exists = false)
        val changedDriver = FakeDriver(foreground = false)
        val cancelledDriver = FakeDriver()
        val timeoutDriver = FakeDriver(visible = false)
        val missing = AutomationEngine().execute(
            strategy,
            missingDriver,
            limits,
        ) { true }
        val changed = AutomationEngine().execute(
            strategy,
            changedDriver,
            limits,
        ) { true }
        val cancelled = AutomationEngine().execute(strategy, cancelledDriver, limits) { false }
        val timeout = AutomationEngine().execute(
            strategy,
            timeoutDriver,
            limits,
        ) { true }

        assertEquals("target_missing", missing.state)
        assertEquals("failed", changed.state)
        assertEquals("cancelled", cancelled.state)
        assertEquals("timeout", timeout.state)
        assertTrue(missingDriver.returnCalled)
        assertTrue(changedDriver.returnCalled)
        assertTrue(cancelledDriver.returnCalled)
        assertTrue(timeoutDriver.returnCalled)
    }

    @Test
    fun registryContainsOnlyConfiguredReadOnlyTargets() {
        assertEquals(
            setOf("com.twitter.android", "com.facebook.katana", "com.instagram.android"),
            TargetStrategyRegistry.supportedPackages,
        )
    }

    @Test
    fun exhaustedInitialCaptureSkipsExtraScrollsWithoutFailing() {
        val driver = FakeDriver(exhausted = true)
        val result = AutomationEngine(sequenceClock()).execute(strategy, driver, limits) { true }

        assertEquals("complete", result.state)
        assertEquals(0, result.scrollCount)
        assertEquals(0, driver.scrollCalls)
        assertEquals(strategy.scopes, driver.openedScopes)
    }

    private fun sequenceClock(): () -> Long {
        var value = 1_000L
        return { value.also { value += 10 } }
    }

    private class FakeDriver(
        private val exists: Boolean = true,
        private val visible: Boolean = true,
        private val foreground: Boolean = true,
        private val exhausted: Boolean = false,
    ) : AutomationDriver {
        var scrollCalls = 0
        var captureCalls = 0
        var returnCalled = false
        val openedScopes = mutableListOf<SocialScope>()

        override fun targetExists(targetPackage: String) = exists
        override fun launch(targetPackage: String) = true
        override fun waitVisible(targetPackage: String, timeoutMs: Long) = visible
        override fun waitStable(timeoutMs: Long) = Unit
        override fun isForeground(targetPackage: String) = foreground
        override fun navigateToScope(targetPackage: String, scope: SocialScope): Boolean {
            openedScopes.add(scope)
            return true
        }
        override fun scrollForward(): Boolean {
            scrollCalls += 1
            return true
        }
        override fun captureScope(scope: SocialScope, takeScreenshot: Boolean): ScopeCapture {
            captureCalls += 1
            return ScopeCapture(
                stored = true,
                screenshotId = "shot_$captureCalls".takeIf { takeScreenshot },
                exhausted = exhausted,
            )
        }
        override fun returnToAgent() {
            returnCalled = true
        }
    }
}
