package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Test

class InstagramCapturePolicyTest {
    @Test
    fun archivePrecedesCommentsAndArchiveUsesThreeScrolls() {
        val strategy = InstagramOwnAccountStrategy()

        assertEquals(
            listOf(
                SocialScope.OWN_PROFILE,
                SocialScope.OWN_POSTS,
                SocialScope.OWN_STORY_ARCHIVE,
                SocialScope.OWN_COMMENTS,
            ),
            strategy.scopes,
        )
        assertEquals(
            INSTAGRAM_ARCHIVE_SCROLL_LIMIT,
            strategy.additionalCaptureCount(SocialScope.OWN_STORY_ARCHIVE),
        )
        assertEquals(4, strategy.screenshotLimit(SocialScope.OWN_STORY_ARCHIVE, 24))
        assertEquals(
            INSTAGRAM_COMMENTS_EXHAUST_SCROLL_BUDGET,
            strategy.additionalCaptureCount(SocialScope.OWN_COMMENTS),
        )
        assertEquals(
            INSTAGRAM_COMMENTS_SCREENSHOT_BUDGET,
            strategy.screenshotLimit(SocialScope.OWN_COMMENTS, 48),
        )
        assertEquals(
            SOCIAL_FEED_EXHAUST_SCROLL_BUDGET,
            strategy.additionalCaptureCount(SocialScope.OWN_POSTS),
        )
    }

    @Test
    fun xAndFacebookExhaustFeedScrollsAndStayTextOnly() {
        val x = XOwnAccountStrategy()
        val facebook = FacebookOwnAccountStrategy()
        assertEquals(SocialCaptureMode.TEXT_ONLY, x.captureMode)
        assertEquals(SocialCaptureMode.TEXT_ONLY, facebook.captureMode)
        assertEquals(0, x.screenshotLimit(SocialScope.OWN_TWEETS, 48))
        assertEquals(0, facebook.screenshotLimit(SocialScope.OWN_POSTS, 48))
        assertEquals(
            SOCIAL_FEED_EXHAUST_SCROLL_BUDGET,
            x.additionalCaptureCount(SocialScope.OWN_TWEETS),
        )
        assertEquals(
            SOCIAL_FEED_EXHAUST_SCROLL_BUDGET,
            x.additionalCaptureCount(SocialScope.OWN_REPLIES),
        )
        assertEquals(
            SOCIAL_FEED_EXHAUST_SCROLL_BUDGET,
            facebook.additionalCaptureCount(SocialScope.OWN_POSTS),
        )
        assertEquals(
            SOCIAL_FEED_EXHAUST_SCROLL_BUDGET,
            facebook.additionalCaptureCount(SocialScope.OWN_COMMENTS),
        )
    }
}
