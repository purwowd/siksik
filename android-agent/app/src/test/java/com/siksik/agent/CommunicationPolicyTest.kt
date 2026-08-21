package com.siksik.agent

import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.CaptureEventGate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CommunicationPolicyTest {
    @Test
    fun textOnlyCrawlCoverIsFacebookAndXNeverInstagram() {
        assertTrue(CommunicationPolicy.usesTextOnlyCrawlCover("com.twitter.android"))
        assertTrue(CommunicationPolicy.usesTextOnlyCrawlCover("com.facebook.katana"))
        assertFalse(CommunicationPolicy.usesTextOnlyCrawlCover("com.instagram.android"))
        assertFalse(CommunicationPolicy.usesTextOnlyCrawlCover("com.whatsapp"))
    }

    @Test
    fun socialTargetAllowlistIsExactAndRejectsArbitraryPackages() {
        assertEquals(
            setOf("com.twitter.android", "com.facebook.katana", "com.instagram.android"),
            CommunicationPolicy.supportedSocialTargets,
        )
        assertEquals(
            setOf("com.instagram.android"),
            CommunicationPolicy.validateTargets(listOf("com.instagram.android")),
        )
        val rejected = runCatching {
            CommunicationPolicy.validateTargets(listOf("com.example.arbitrary"))
        }
        assertTrue(rejected.isFailure)
    }

    @Test
    fun sensitiveTextAndIdentifiersAreBoundedWithoutRawLocatorLeakage() {
        val sensitive = "\u0000fixture\u0007 value " + "x".repeat(100)
        val bounded = requireNotNull(CommunicationPolicy.boundedText(sensitive, 24))
        val identity = CommunicationPolicy.identityHash("sms_address", sensitive)
        val locator = CommunicationPolicy.sourceLocator("sms", sensitive)

        assertTrue(bounded.length <= 24)
        assertFalse(bounded.contains('\u0000'))
        assertEquals(64, identity.length)
        assertFalse(locator.contains("fixture"))
    }

    @Test
    fun phoneEmailAndCanonicalContentNormalizationAreDeterministic() {
        assertEquals("+628123456", CommunicationPolicy.normalizedPhone("+62 (812) 34-56"))
        assertEquals("fixture@example.test", CommunicationPolicy.normalizedEmail(" FIXTURE@Example.Test "))
        assertEquals(
            CommunicationPolicy.contentHash("one", "two"),
            CommunicationPolicy.contentHash("one", "two"),
        )
    }

    @Test
    fun notificationRecordIdsAreDeterministicAndScopedPerCrawl() {
        val first = CommunicationPolicy.scopedRecordId(
            "notification",
            "crawl_00000001",
            "notification_identity",
        )
        val repeated = CommunicationPolicy.scopedRecordId(
            "notification",
            "crawl_00000001",
            "notification_identity",
        )
        val second = CommunicationPolicy.scopedRecordId(
            "notification",
            "crawl_00000002",
            "notification_identity",
        )

        assertEquals(first, repeated)
        assertFalse(first == second)
        assertFalse(first.contains("crawl_00000001"))
    }

    @Test
    fun accessibilityEventGateEnforcesAllowlistAndRateLimit() {
        var now = 1_000L
        val gate = CaptureEventGate(500) { now }
        val allowlist = setOf("com.instagram.android")

        assertFalse(gate.allow("com.example.other", allowlist))
        assertTrue(gate.allow("com.instagram.android", allowlist))
        now += 499
        assertFalse(gate.allow("com.instagram.android", allowlist))
        now += 1
        assertTrue(gate.allow("com.instagram.android", allowlist))
        gate.clear()
        assertTrue(gate.allow("com.instagram.android", allowlist))
    }
}
