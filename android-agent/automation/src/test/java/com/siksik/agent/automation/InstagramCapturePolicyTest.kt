package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Test

class InstagramCapturePolicyTest {
    @Test
    fun commentsPrecedeArchiveAndArchiveUsesThreeScrolls() {
        val strategy = InstagramOwnAccountStrategy()

        assertEquals(
            listOf(
                SocialScope.OWN_PROFILE,
                SocialScope.OWN_POSTS,
                SocialScope.OWN_COMMENTS,
                SocialScope.OWN_STORY_ARCHIVE,
            ),
            strategy.scopes,
        )
        assertEquals(
            INSTAGRAM_ARCHIVE_SCROLL_LIMIT,
            strategy.additionalCaptureCount(SocialScope.OWN_STORY_ARCHIVE),
        )
        assertEquals(4, strategy.screenshotLimit(SocialScope.OWN_STORY_ARCHIVE, 24))
    }
}
