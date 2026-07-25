package com.siksik.agent.automation

import android.os.Bundle
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.CommunicationIdentifiers
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SocialCrawlInstrumentation {
    @Test
    fun crawlConfiguredTarget() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val arguments = InstrumentationRegistry.getArguments()
        val sessionId = requiredId(arguments, "session_id")
        val crawlId = requiredId(arguments, "crawl_id")
        val targetPackage = arguments.getString("target_package").orEmpty()
        val strategy = TargetStrategyRegistry.resolve(targetPackage)
            ?: throw IllegalArgumentException("target package is not allowed")
        val limits = AutomationLimits(
            maxScrolls = boundedInt(arguments, "max_scrolls", 0, 40),
            maxScreenshots = boundedInt(arguments, "max_screenshots", 0, 48),
            launchTimeoutMs = boundedLong(arguments, "launch_timeout_ms", 1_000, 60_000),
            stableWaitMs = boundedLong(arguments, "stable_wait_ms", 250, 10_000),
        )
        val navigationDeadlineMs = boundedLong(
            arguments,
            "navigation_deadline_ms",
            15_000,
            175_000,
        )
        val debugSnapshots = arguments.getString("debug_snapshots") == "true"
        val testContext = instrumentation.targetContext
        val context = testContext.createPackageContext("com.siksik.agent", 0)
        val sessionStore = CommunicationCaptureStore(context)
        val driver = UiAutomatorDriver(
            context,
            UiDevice.getInstance(instrumentation),
            instrumentation.uiAutomation,
            sessionId,
            crawlId,
            debugSnapshotsEnabled = debugSnapshots,
            navigationDeadlineAtMs = System.currentTimeMillis() + navigationDeadlineMs,
        )
        val outcome = try {
            AutomationEngine().execute(strategy, driver, limits) {
                sessionStore.session(crawlId)?.active == true
            }
        } finally {
            driver.close()
            sessionStore.close()
        }
        instrumentation.sendStatus(
            RESULT_STATUS_CODE,
            Bundle().apply { putString(RESULT_KEY, outcomeJson(outcome).toString()) },
        )
    }

    private fun requiredId(arguments: Bundle, key: String): String {
        val value = arguments.getString(key).orEmpty()
        require(CommunicationIdentifiers.SAFE_ID.matches(value)) { "$key is invalid" }
        return value
    }

    private fun boundedInt(arguments: Bundle, key: String, minimum: Int, maximum: Int): Int {
        val value = arguments.getString(key)?.toIntOrNull()
            ?: throw IllegalArgumentException("$key is invalid")
        require(value in minimum..maximum) { "$key is invalid" }
        return value
    }

    private fun boundedLong(arguments: Bundle, key: String, minimum: Long, maximum: Long): Long {
        val value = arguments.getString(key)?.toLongOrNull()
            ?: throw IllegalArgumentException("$key is invalid")
        require(value in minimum..maximum) { "$key is invalid" }
        return value
    }

    private fun outcomeJson(value: AutomationOutcome): JSONObject = JSONObject()
        .put("schema_version", 1)
        .put("target_package", value.targetPackage)
        .put("state", value.state)
        .put("reason", value.reason ?: JSONObject.NULL)
        .put("scroll_count", value.scrollCount)
        .put("screenshot_ids", JSONArray(value.screenshotIds))
        .put("duration_ms", value.durationMs)

    companion object {
        const val RESULT_KEY = "siksik_result"
        private const val RESULT_STATUS_CODE = 2
    }
}
