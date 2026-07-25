package com.siksik.agent.session

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.siksik.agent.R
import com.siksik.agent.api.AgentServer
import java.io.IOException

class AgentService : Service() {
    private var server: AgentServer? = null
    private var authenticator: SessionAuthenticator? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val expiryStop = Runnable(::stopActiveSession)
    private val responseStop = Runnable(::stopActiveSession)

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())
        val payload = BootstrapValidator.validate(
            intent?.getStringExtra(EXTRA_SESSION_ID),
            intent?.getStringExtra(EXTRA_SESSION_TOKEN),
            intent?.getLongExtra(EXTRA_TOKEN_EXPIRES_AT, 0L) ?: 0L,
        )
        if (payload == null) {
            stopActiveSession()
            return START_NOT_STICKY
        }
        val currentAuthenticator = authenticator
        if (
            server != null &&
            currentAuthenticator?.matches(
                payload.sessionId,
                payload.token,
                payload.expiresAtEpochMs,
            ) == true
        ) {
            mainHandler.removeCallbacks(responseStop)
            mainHandler.removeCallbacks(expiryStop)
            mainHandler.postDelayed(
                expiryStop,
                (payload.expiresAtEpochMs - System.currentTimeMillis()).coerceAtLeast(1L),
            )
            AgentRuntime.activate(payload.sessionId)
            return START_NOT_STICKY
        }
        val nextAuthenticator = SessionAuthenticator(
            payload.sessionId,
            payload.token,
            payload.expiresAtEpochMs,
        )
        mainHandler.removeCallbacks(responseStop)
        closeServer()
        authenticator = nextAuthenticator
        mainHandler.removeCallbacks(expiryStop)
        mainHandler.postDelayed(
            expiryStop,
            (payload.expiresAtEpochMs - System.currentTimeMillis()).coerceAtLeast(1L),
        )
        val nextServer = AgentServer(
            applicationContext,
            nextAuthenticator,
            AgentRuntime.grants(applicationContext),
            { mainHandler.postDelayed(responseStop, STOP_RESPONSE_GRACE_MS) },
        )
        try {
            nextServer.start(SERVER_READ_TIMEOUT_MS, false)
        } catch (_: IOException) {
            nextServer.close()
            stopActiveSession()
            return START_NOT_STICKY
        }
        server = nextServer
        AgentRuntime.activate(payload.sessionId)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(expiryStop)
        mainHandler.removeCallbacks(responseStop)
        closeServer()
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun stopActiveSession() {
        mainHandler.removeCallbacks(expiryStop)
        mainHandler.removeCallbacks(responseStop)
        closeServer()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun closeServer() {
        val sessionId = authenticator?.sessionId
        server?.close()
        server = null
        authenticator?.destroy()
        authenticator = null
        if (sessionId != null) {
            AgentRuntime.clear(sessionId)
        }
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.background_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        channel.setShowBadge(false)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification() = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(R.drawable.ic_stat_active_session)
        .setContentTitle(getString(R.string.background_notification_title))
        .setContentText(getString(R.string.background_notification_text))
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setContentIntent(
            PendingIntent.getActivity(
                this,
                0,
                Intent(this, BootstrapActivity::class.java).setAction(
                    BootstrapActivity.ACTION_SHOW_STATUS,
                ),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        )
        .build()

    companion object {
        const val EXTRA_SESSION_ID = "session_id"
        const val EXTRA_SESSION_TOKEN = "session_token"
        const val EXTRA_TOKEN_EXPIRES_AT = "token_expires_at_epoch_ms"
        private const val CHANNEL_ID = "siksik_active_session"
        private const val NOTIFICATION_ID = 1001
        private const val SERVER_READ_TIMEOUT_MS = 5_000
        private const val STOP_RESPONSE_GRACE_MS = 2_000L
    }
}
