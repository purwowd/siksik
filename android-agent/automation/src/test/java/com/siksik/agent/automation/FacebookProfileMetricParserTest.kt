package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FacebookProfileMetricParserTest {
    @Test
    fun parsesCombinedEnglishAndIndonesianMetrics() {
        assertEquals(
            listOf(
                FacebookProfileMetricToken("2 friends", FacebookProfileMetricKind.FRIENDS),
                FacebookProfileMetricToken("5 posts", FacebookProfileMetricKind.POSTS),
            ),
            FacebookProfileMetricParser.parse("2 friends · 5 posts"),
        )
        assertEquals(
            listOf(
                FacebookProfileMetricToken("3 teman", FacebookProfileMetricKind.FRIENDS),
                FacebookProfileMetricToken("7 postingan", FacebookProfileMetricKind.POSTS),
            ),
            FacebookProfileMetricParser.parse("3 teman | 7 postingan"),
        )
    }

    @Test
    fun rejectsChromeAndSentencesThatOnlyMentionMetricWords() {
        assertTrue(FacebookProfileMetricParser.isMetricLine("2 friends · 5 posts"))
        assertFalse(
            FacebookProfileMetricParser.isMetricLine(
                "We made it easier to see which friends like it too.",
            ),
        )
        assertFalse(FacebookProfileMetricParser.isMetricLine("I published 5 posts today"))
    }
}
