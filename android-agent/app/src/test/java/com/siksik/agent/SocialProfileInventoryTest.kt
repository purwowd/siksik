package com.siksik.agent

import com.siksik.agent.source.communication.VisibleNodeRecord
import com.siksik.agent.source.communication.VisibleUiRecordMetadata
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventoryRecordJson
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.SourceAdapter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SocialProfileInventoryTest {
    @Test
    fun facebookFriendsAndPostsRemainSeparateProfileMetrics() {
        val nodes = listOf(
            node("Demo User", "profile_display_name", 0),
            node("2 friends", "friends_stat", 1),
            node("5 posts", "posts_stat", 2),
        )
        val record = InventoryRecord(
            recordId = "record_social_profile",
            identityHash = "a".repeat(64),
            dedupeHash = "b".repeat(64),
            sourceKind = InventorySourceKind.VISIBLE_UI,
            sourceAdapter = SourceAdapter.VISIBLE_UI,
            sourceApp = "com.facebook.katana",
            sourceLocator = "accessibility_visible_ui:profile",
            displayName = "social-profile.json",
            mimeType = "application/json",
            sizeBytes = null,
            width = null,
            height = null,
            durationMs = null,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = null,
            captureTimeEpochMs = 1_700_000_000_000L,
            captureTimeSource = "uiautomator",
            directoryHint = null,
            exif = null,
            warningCodes = emptyList(),
            thumbnailAvailable = false,
            observedAtEpochMs = 1_700_000_000_000L,
            contentUri = null,
            normalizedText = "Demo User\n2 friends\n5 posts",
            visibleUiMetadata = VisibleUiRecordMetadata(
                packageName = "com.facebook.katana",
                socialScope = "own_profile",
                windowId = 1,
                activityContext = "profile",
                eventType = 2048,
                screenSequence = 1,
                nodes = nodes,
                screenshotIds = emptyList(),
                profileLinks = emptyList(),
            ),
        )

        val metadata = InventoryRecordJson.encode(
            "session_12345678",
            "crawl_12345678",
            record,
        ).getJSONObject("metadata")
        val metrics = metadata.getJSONObject("profile_metrics")

        assertEquals(2L, metrics.getLong("friends"))
        assertEquals(5L, metrics.getLong("posts"))
        assertTrue(metrics.isNull("followers"))
        assertTrue(metadata.isNull("profile_bio"))
    }

    private fun node(text: String, resource: String, sequence: Int) = VisibleNodeRecord(
        sequence = sequence,
        depth = 0,
        text = text,
        contentDescription = null,
        className = "android.widget.TextView",
        viewId = "com.facebook.katana:id/$resource",
        left = 0,
        top = sequence * 100,
        right = 500,
        bottom = sequence * 100 + 80,
        clickable = false,
        scrollable = false,
    )
}
