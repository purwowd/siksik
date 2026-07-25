package com.siksik.agent.notification

import android.app.Notification
import android.content.ComponentName
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.NotificationRecordMetadata

class SessionNotificationListener : NotificationListenerService() {
    private lateinit var store: CommunicationCaptureStore

    @Volatile
    private var destroyed = false

    override fun onCreate() {
        super.onCreate()
        store = CommunicationCaptureStore(applicationContext)
    }

    override fun onNotificationPosted(notification: StatusBarNotification?) {
        captureSafely("notification_capture_failed") {
            val value = notification ?: return@captureSafely
            if (store.activeSession() == null) return@captureSafely
            if (value.packageName in CommunicationPolicy.supportedSocialTargets) {
                return@captureSafely
            }
            val extras = value.notification.extras
            val title = CommunicationPolicy.boundedText(
                extras.getCharSequence(Notification.EXTRA_TITLE),
                BuildConfig.MAX_UI_TEXT_LENGTH,
            )
            val text = CommunicationPolicy.boundedText(
                extras.getCharSequence(Notification.EXTRA_TEXT),
                BuildConfig.MAX_UI_TEXT_LENGTH,
            )
            val subText = CommunicationPolicy.boundedText(
                extras.getCharSequence(Notification.EXTRA_SUB_TEXT),
                BuildConfig.MAX_UI_TEXT_LENGTH,
            )
            val bigText = CommunicationPolicy.boundedText(
                extras.getCharSequence(Notification.EXTRA_BIG_TEXT),
                BuildConfig.MAX_SMS_TEXT_LENGTH,
            )
            val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
                ?.asSequence()
                ?.mapNotNull { CommunicationPolicy.boundedText(it, BuildConfig.MAX_UI_TEXT_LENGTH) }
                ?.take(MAX_TEXT_LINES)
                ?.toList()
                .orEmpty()
            val category = CommunicationPolicy.boundedText(value.notification.category, 256)
            val channelId = CommunicationPolicy.boundedText(value.notification.channelId, 512)
            val key = value.key ?: listOf(
                value.packageName,
                value.id.toString(),
                value.tag.orEmpty(),
                value.user.hashCode().toString(),
            ).joinToString(":")
            val contentHash = CommunicationPolicy.contentHash(
                value.packageName,
                title,
                text,
                subText,
                bigText,
                lines.joinToString("\u001e"),
                category,
                channelId,
                value.postTime.toString(),
            )
            store.recordNotification(
                packageName = value.packageName,
                notificationKey = key,
                metadata = NotificationRecordMetadata(
                    packageName = value.packageName,
                    notificationIdentity = CommunicationPolicy.identityHash("notification", key),
                    title = title,
                    text = text,
                    subText = subText,
                    bigText = bigText,
                    textLines = lines,
                    category = category,
                    channelId = channelId,
                    postTimeEpochMs = value.postTime,
                    removedAtEpochMs = null,
                    updateCount = 0,
                ),
                contentHash = contentHash,
                now = System.currentTimeMillis(),
            )
        }
    }

    override fun onNotificationRemoved(notification: StatusBarNotification?) {
        captureSafely("notification_remove_failed") {
            val key = notification?.key ?: return@captureSafely
            store.markNotificationRemoved(key, System.currentTimeMillis())
        }
    }

    override fun onListenerDisconnected() {
        if (destroyed || !::store.isInitialized) return
        captureSafely("notification_listener_disconnected") {
            store.markNotificationIssue("notification_listener_disconnected", System.currentTimeMillis())
            requestRebind(ComponentName(this, SessionNotificationListener::class.java))
        }
    }

    override fun onDestroy() {
        destroyed = true
        if (::store.isInitialized) store.close()
        super.onDestroy()
    }

    private inline fun captureSafely(reason: String, action: () -> Unit) {
        try {
            action()
        } catch (error: RuntimeException) {
            Log.e(
                LOG_TAG,
                "event=notification_callback_failed reason=$reason " +
                    "error_type=${error.javaClass.simpleName}",
                error,
            )
            runCatching {
                if (::store.isInitialized) {
                    store.markNotificationIssue(reason, System.currentTimeMillis())
                }
            }.onFailure { issueError ->
                Log.e(
                    LOG_TAG,
                    "event=notification_issue_persist_failed " +
                        "error_type=${issueError.javaClass.simpleName}",
                )
            }
        }
    }

    companion object {
        private const val LOG_TAG = "SIKSIKAgent"
        private const val MAX_TEXT_LINES = 32
    }
}
