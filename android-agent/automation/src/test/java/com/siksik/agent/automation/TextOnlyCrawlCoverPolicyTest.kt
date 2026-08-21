package com.siksik.agent.automation

import com.siksik.agent.source.communication.CommunicationPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TextOnlyCrawlCoverPolicyTest {
    @Test
    fun facebookAndXAreTextOnlyCoverTargetsAndInstagramIsNot() {
        assertEquals(SocialCaptureMode.TEXT_ONLY, XOwnAccountStrategy().captureMode)
        assertEquals(SocialCaptureMode.TEXT_ONLY, FacebookOwnAccountStrategy().captureMode)
        assertEquals(SocialCaptureMode.VISUAL, InstagramOwnAccountStrategy().captureMode)
        assertTrue(CommunicationPolicy.usesTextOnlyCrawlCover(XOwnAccountStrategy().targetPackage))
        assertTrue(
            CommunicationPolicy.usesTextOnlyCrawlCover(FacebookOwnAccountStrategy().targetPackage),
        )
        assertFalse(
            CommunicationPolicy.usesTextOnlyCrawlCover(InstagramOwnAccountStrategy().targetPackage),
        )
    }
}
