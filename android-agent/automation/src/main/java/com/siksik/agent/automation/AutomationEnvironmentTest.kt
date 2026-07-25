package com.siksik.agent.automation

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siksik.agent.source.communication.CommunicationCaptureStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AutomationEnvironmentTest {
    @Test
    fun shellInspectsTargetWithoutAdvertisingCrawlerSupport() {
        val target = InstrumentationRegistry.getInstrumentation().targetContext
        val environment = AutomationEnvironmentProbe(target).inspect()

        assertEquals(AutomationEnvironmentProbe.TARGET_PACKAGE, environment.targetPackage)
        assertEquals(setOf("environment_probe"), environment.supportedOperations)
        assertFalse(environment.supportedOperations.any { it.contains("crawl") })
    }

    @Test
    fun sharedCaptureStoreAcceptsAgentPackageContext() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val packageContext = instrumentation.targetContext.createPackageContext(
            AutomationEnvironmentProbe.TARGET_PACKAGE,
            0,
        )

        CommunicationCaptureStore(packageContext).use { store ->
            assertEquals(null, store.session("crawl_diagnostic_fixture"))
        }
    }
}
