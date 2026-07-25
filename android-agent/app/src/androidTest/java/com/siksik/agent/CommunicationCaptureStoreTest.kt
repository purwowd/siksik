package com.siksik.agent

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.source.communication.AutomationTargetResult
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.NotificationRecordMetadata
import com.siksik.agent.source.communication.VisibleNodeRecord
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CommunicationCaptureStoreTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun captureIsSessionBoundDeduplicatedPagedAndCleaned() {
        val suffix = UUID.randomUUID().toString()
        val sessionId = "session_$suffix"
        val crawlId = "crawl_$suffix"
        val now = 1_700_000_000_000L
        CommunicationCaptureStore(context).use { store ->
            try {
                store.beginSession(
                    sessionId,
                    crawlId,
                    setOf("com.instagram.android"),
                    now,
                )
                CommunicationCaptureStore(context).use { restarted ->
                    assertEquals(crawlId, restarted.activeSession()?.crawlId)
                }
                assertTrue(
                    store.setVerifiedSocialScope(
                        crawlId,
                        "com.instagram.android",
                        "own_posts",
                        now,
                    ),
                )
                val node = VisibleNodeRecord(
                    sequence = 0,
                    depth = 0,
                    text = "fixture visible text",
                    contentDescription = null,
                    className = "android.widget.TextView",
                    viewId = null,
                    left = 0,
                    top = 0,
                    right = 100,
                    bottom = 100,
                    clickable = false,
                    scrollable = false,
                )
                assertTrue(
                    store.recordVisibleSnapshot(
                        "com.instagram.android",
                        1,
                        "FixtureActivity",
                        2048,
                        now,
                        listOf(node),
                        "fixture visible text",
                        "a".repeat(64),
                        "own_posts",
                        listOf("shot_fixture"),
                        now,
                    ),
                )
                assertFalse(
                    store.recordVisibleSnapshot(
                        "com.example.not-allowed",
                        1,
                        "FixtureActivity",
                        2048,
                        now,
                        listOf(node),
                        "fixture visible text",
                        "d".repeat(64),
                        "own_posts",
                        emptyList(),
                        now,
                    ),
                )
                assertFalse(
                    store.recordVisibleSnapshot(
                        "com.instagram.android",
                        1,
                        "FixtureActivity",
                        2048,
                        now,
                        listOf(node),
                        "fixture visible text",
                        "a".repeat(64),
                        "own_posts",
                        emptyList(),
                        now,
                    ),
                )
                store.recordAutomationResult(
                    crawlId,
                    AutomationTargetResult(
                        "com.instagram.android",
                        "complete",
                        null,
                        3,
                        listOf("shot_fixture"),
                        500,
                    ),
                    now,
                )
                val visible = store.visiblePage(crawlId, 0, 10)
                assertEquals(1, visible.size)
                assertEquals(listOf("shot_fixture"), visible.single().screenshotIds)

                val notification = NotificationRecordMetadata(
                    packageName = "com.example.chat",
                    notificationIdentity = "b".repeat(64),
                    title = "Fixture",
                    text = "Fixture notification",
                    subText = null,
                    bigText = null,
                    textLines = emptyList(),
                    category = "msg",
                    channelId = "messages",
                    postTimeEpochMs = now,
                    removedAtEpochMs = null,
                    updateCount = 0,
                )
                assertTrue(
                    store.recordNotification(
                        "com.example.chat",
                        "notification-key",
                        notification,
                        "b".repeat(64),
                        now,
                    ),
                )
                assertFalse(
                    store.recordNotification(
                        "com.example.chat",
                        "notification-key",
                        notification,
                        "b".repeat(64),
                        now,
                    ),
                )
                assertTrue(
                    store.recordNotification(
                        "com.example.chat",
                        "notification-key",
                        notification.copy(text = "Updated fixture"),
                        "c".repeat(64),
                        now + 1,
                    ),
                )
                store.markNotificationRemoved("notification-key", now + 2)
                val storedNotification = store.notificationPage(crawlId, 0, 10).single()
                assertEquals(2, storedNotification.updateCount)
                assertEquals(now + 2, storedNotification.removedAt)

                store.markAccessibilityIssue("accessibility_disconnected", now + 2)
                store.markNotificationIssue("notification_listener_disconnected", now + 2)
                assertEquals(
                    "accessibility_disconnected",
                    store.session(crawlId)?.accessibilityReason,
                )
                assertEquals(
                    "notification_listener_disconnected",
                    store.session(crawlId)?.notificationReason,
                )

                store.finishSession(crawlId, now + 3)
                assertFalse(requireNotNull(store.session(crawlId)).active)
                assertFalse(
                    store.recordNotification(
                        "com.example.chat",
                        "notification-after-finish",
                        notification,
                        "e".repeat(64),
                        now + 4,
                    ),
                )
            } finally {
                store.clearSession(sessionId)
            }
            assertEquals(null, store.session(crawlId))
        }
    }
}
