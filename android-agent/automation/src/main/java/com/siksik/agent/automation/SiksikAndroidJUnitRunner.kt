package com.siksik.agent.automation

import android.app.UiAutomation
import androidx.test.runner.AndroidJUnitRunner

/**
 * Default UiAutomation suppresses other accessibility services. The TEXT_ONLY
 * white cover lives in CaptureAccessibilityService, so crawl instrumentation
 * must leave that service connected.
 */
class SiksikAndroidJUnitRunner : AndroidJUnitRunner() {
    override fun getUiAutomation(): UiAutomation =
        getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)

    override fun getUiAutomation(flags: Int): UiAutomation =
        super.getUiAutomation(flags or UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
}
