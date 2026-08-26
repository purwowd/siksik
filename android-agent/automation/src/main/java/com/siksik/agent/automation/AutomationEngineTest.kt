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

    @Test
    fun retryableScopeFailureRecoversAndCompletes() {
        val driver = FakeDriver(
            navigateFailures = mutableMapOf(SocialScope.OWN_PROFILE to 1),
            recoverReturns = true,
        )
        val progress = mutableListOf<AutomationScopeProgress>()
        val result = AutomationEngine(sequenceClock()).execute(
            strategy,
            driver,
            limits,
            onProgress = { progress += it },
        ) { true }

        assertEquals("complete", result.state)
        assertEquals(2, driver.navigateAttempts[SocialScope.OWN_PROFILE])
        assertEquals(1, driver.recoverCalls)
        assertTrue(progress.any { it.state == "retrying" && it.scope == SocialScope.OWN_PROFILE })
        assertTrue(
            progress.any {
                it.state == "complete" &&
                    it.scope == SocialScope.OWN_PROFILE &&
                    it.stage == "checkpoint_saved"
            },
        )
    }

    @Test
    fun completedCheckpointSkipsNavigationForThatScope() {
        val driver = FakeDriver(
            completedCheckpoints = setOf(SocialScope.OWN_PROFILE, SocialScope.OWN_POSTS),
        )
        val progress = mutableListOf<AutomationScopeProgress>()
        val result = AutomationEngine(sequenceClock()).execute(
            strategy,
            driver,
            limits,
            onProgress = { progress += it },
        ) { true }

        assertEquals("complete", result.state)
        assertEquals(
            listOf(SocialScope.OWN_STORY_ARCHIVE, SocialScope.OWN_COMMENTS),
            driver.openedScopes,
        )
        assertTrue(
            progress.any {
                it.scope == SocialScope.OWN_PROFILE &&
                    it.stage == "checkpoint_restored" &&
                    it.state == "complete"
            },
        )
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
        private val navigateFailures: MutableMap<SocialScope, Int> = mutableMapOf(),
        private val recoverReturns: Boolean = false,
        private val completedCheckpoints: Set<SocialScope> = emptySet(),
    ) : AutomationDriver {
        var scrollCalls = 0
        var captureCalls = 0
        var returnCalled = false
        var recoverCalls = 0
        val openedScopes = mutableListOf<SocialScope>()
        val navigateAttempts = mutableMapOf<SocialScope, Int>()
        private var lastFailure: String? = null

        override fun targetExists(targetPackage: String) = exists
        override fun launch(targetPackage: String) = true
        override fun waitVisible(targetPackage: String, timeoutMs: Long) = visible
        override fun waitStable(timeoutMs: Long) = Unit
        override fun isForeground(targetPackage: String) = foreground
        override fun navigateToScope(targetPackage: String, scope: SocialScope): Boolean {
            val attempt = (navigateAttempts[scope] ?: 0) + 1
            navigateAttempts[scope] = attempt
            val remaining = navigateFailures[scope] ?: 0
            if (remaining > 0) {
                navigateFailures[scope] = remaining - 1
                lastFailure = "scope_navigation_failed"
                return false
            }
            openedScopes.add(scope)
            lastFailure = null
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
        override fun lastFailureReason(): String? = lastFailure
        override fun recoverScope(
            targetPackage: String,
            scope: SocialScope,
            failedAttempt: Int,
            reason: String,
        ): Boolean {
            recoverCalls += 1
            return recoverReturns
        }
        override fun completedScopeCheckpoints(targetPackage: String): Set<SocialScope> =
            completedCheckpoints
        override fun returnToAgent() {
            returnCalled = true
        }
    }
}
