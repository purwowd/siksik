package com.siksik.agent.session

import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.app.AlertDialog
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.siksik.agent.R
import com.siksik.agent.accessibility.CaptureAccessibilityService
import com.siksik.agent.model.ApiException
import com.siksik.agent.notification.SessionNotificationListener
import com.siksik.agent.permission.GrantCoordinator
import java.util.UUID

class BootstrapActivity : ComponentActivity() {
    private lateinit var grantCoordinator: GrantCoordinator

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bootstrap)
        val grants = AgentRuntime.grants(applicationContext)
        grantCoordinator = GrantCoordinator(this, grants.store()) { record ->
            Log.i(LOG_TAG, "event=agent_grant_state_changed state=${record.state.wireName}")
        }
        grants.attach(grantCoordinator)
        activate(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        activate(intent)
    }

    override fun onDestroy() {
        AgentRuntime.grants(applicationContext).detach(grantCoordinator)
        super.onDestroy()
    }

    private fun activate(intent: Intent) {
        if (intent.action == ACTION_SHOW_STATUS) {
            maybeRequestMediaPermissions(intent)
            maybeRequestCommunicationPermissions(intent)
            maybeRequestSpecialAccess(intent)
            return
        }
        val payload = BootstrapValidator.validate(
            intent.getStringExtra(EXTRA_SESSION_ID),
            intent.getStringExtra(EXTRA_SESSION_TOKEN),
            intent.getLongExtra(EXTRA_TOKEN_EXPIRES_AT, 0L),
        )
        if (payload == null) {
            Log.w(LOG_TAG, "event=agent_bootstrap_rejected error_category=validation_error")
            return
        }
        val serviceIntent = Intent(this, AgentService::class.java)
            .putExtra(AgentService.EXTRA_SESSION_ID, payload.sessionId)
            .putExtra(AgentService.EXTRA_SESSION_TOKEN, payload.token)
            .putExtra(AgentService.EXTRA_TOKEN_EXPIRES_AT, payload.expiresAtEpochMs)
        try {
            ContextCompat.startForegroundService(this, serviceIntent)
        } catch (_: IllegalStateException) {
            Log.e(LOG_TAG, "event=agent_service_start_failed error_category=invalid_state")
        } catch (_: SecurityException) {
            Log.e(LOG_TAG, "event=agent_service_start_failed error_category=access_denied")
        }
        maybeRequestMediaPermissions(intent, payload.sessionId)
        maybeRequestCommunicationPermissions(intent, payload.sessionId)
        maybeRequestSpecialAccess(intent, payload.sessionId)
    }

    private fun maybeRequestMediaPermissions(intent: Intent, sessionId: String? = null) {
        val requested = intent.getBooleanExtra(EXTRA_REQUEST_MEDIA_PERMISSIONS, false) ||
            intent.getStringExtra(EXTRA_REQUEST_MEDIA_PERMISSIONS) == "1"
        if (!requested) {
            return
        }
        val resolvedSession = sessionId
            ?: intent.getStringExtra(EXTRA_SESSION_ID)
            ?: AgentRuntime.activeSessionId
        if (resolvedSession.isNullOrBlank()) {
            Log.w(LOG_TAG, "event=agent_media_permission_skipped error_category=validation_error")
            return
        }
        // Defer until after Activity is resumed so the system dialog can attach.
        window.decorView.post {
            try {
                AgentRuntime.grants(applicationContext).launch(
                    resolvedSession,
                    "media_library",
                    "bootstrap-media-${UUID.randomUUID()}",
                    1,
                )
                Log.i(LOG_TAG, "event=agent_media_permission_launched")
            } catch (exc: ApiException) {
                Log.w(
                    LOG_TAG,
                    "event=agent_media_permission_failed error_category=${exc.code} detail=${exc.message}",
                )
            } catch (exc: IllegalStateException) {
                Log.w(LOG_TAG, "event=agent_media_permission_failed error_category=invalid_state")
            }
        }
    }

    private fun maybeRequestCommunicationPermissions(intent: Intent, sessionId: String? = null) {
        val requested = intent.getBooleanExtra(EXTRA_REQUEST_COMMUNICATION_PERMISSIONS, false) ||
            intent.getStringExtra(EXTRA_REQUEST_COMMUNICATION_PERMISSIONS) == "1"
        if (!requested) {
            return
        }
        val resolvedSession = sessionId
            ?: intent.getStringExtra(EXTRA_SESSION_ID)
            ?: AgentRuntime.activeSessionId
        if (resolvedSession.isNullOrBlank()) {
            Log.w(LOG_TAG, "event=agent_comm_permission_skipped error_category=validation_error")
            return
        }
        window.decorView.post {
            try {
                AgentRuntime.grants(applicationContext).launch(
                    resolvedSession,
                    "communication_runtime",
                    "bootstrap-comm-${UUID.randomUUID()}",
                    1,
                )
                Log.i(LOG_TAG, "event=agent_comm_permission_launched")
            } catch (exc: ApiException) {
                Log.w(
                    LOG_TAG,
                    "event=agent_comm_permission_failed error_category=${exc.code} detail=${exc.message}",
                )
            } catch (exc: IllegalStateException) {
                Log.w(LOG_TAG, "event=agent_comm_permission_failed error_category=invalid_state")
            }
        }
    }

    private fun maybeRequestSpecialAccess(intent: Intent, sessionId: String? = null) {
        val access = intent.getStringExtra(EXTRA_REQUEST_SPECIAL_ACCESS)
            ?.trim()
            ?.takeIf(SUPPORTED_SPECIAL_ACCESS::contains)
            ?: return
        val resolvedSession = sessionId
            ?: intent.getStringExtra(EXTRA_SESSION_ID)
            ?: AgentRuntime.activeSessionId
        if (resolvedSession.isNullOrBlank()) {
            Log.w(LOG_TAG, "event=agent_special_access_skipped error_category=validation_error")
            return
        }
        val status = findViewById<TextView>(R.id.bootstrap_status)
        status.setText(R.string.permission_waiting)
        window.decorView.postDelayed(
            {
                AlertDialog.Builder(this)
                    .setTitle(R.string.permission_special_access_title)
                    .setMessage(specialAccessMessage(access))
                    .setPositiveButton(R.string.permission_allow) { _, _ ->
                        val opened = openSpecialAccessSettings(access)
                        if (!opened) status.setText(R.string.permission_settings_unavailable)
                        Log.i(
                            LOG_TAG,
                            "event=agent_special_access_launched access=$access success=$opened",
                        )
                    }
                    .setNegativeButton(R.string.permission_deny) { _, _ ->
                        status.setText(R.string.permission_denied)
                        Log.i(LOG_TAG, "event=agent_special_access_declined access=$access")
                    }
                    .setOnCancelListener {
                        status.setText(R.string.permission_cancelled)
                    }
                    .show()
            },
            SPECIAL_ACCESS_LAUNCH_DELAY_MS,
        )
    }

    private fun specialAccessMessage(access: String): Int = when (access) {
        SPECIAL_ACCESS_MANAGE_ALL_FILES -> R.string.permission_manage_all_files_message
        SPECIAL_ACCESS_ACCESSIBILITY -> R.string.permission_accessibility_message
        SPECIAL_ACCESS_NOTIFICATION_LISTENER -> R.string.permission_notification_listener_message
        else -> R.string.permission_waiting
    }

    private fun openSpecialAccessSettings(access: String): Boolean {
        val appDetails = Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:$packageName"),
        )
        val intents = when (access) {
            SPECIAL_ACCESS_MANAGE_ALL_FILES -> buildList {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    add(
                        Intent(
                            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:$packageName"),
                        ),
                    )
                    add(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
                }
                add(appDetails)
            }
            SPECIAL_ACCESS_ACCESSIBILITY -> listOf(
                Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS").putExtra(
                    "android.intent.extra.COMPONENT_NAME",
                    ComponentName(this, CaptureAccessibilityService::class.java),
                ),
                Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
                appDetails,
            )
            SPECIAL_ACCESS_NOTIFICATION_LISTENER -> listOf(
                Intent("android.settings.NOTIFICATION_LISTENER_DETAIL_SETTINGS").putExtra(
                    "android.provider.extra.NOTIFICATION_LISTENER_COMPONENT_NAME",
                    ComponentName(this, SessionNotificationListener::class.java),
                ),
                Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"),
                appDetails,
            )
            else -> emptyList()
        }
        for (settingsIntent in intents) {
            try {
                startActivity(settingsIntent)
                return true
            } catch (_: ActivityNotFoundException) {
                continue
            } catch (_: SecurityException) {
                continue
            } catch (_: IllegalArgumentException) {
                continue
            }
        }
        return false
    }

    companion object {
        const val ACTION_SHOW_STATUS = "com.siksik.agent.action.SHOW_STATUS"
        const val EXTRA_SESSION_ID = "session_id"
        const val EXTRA_SESSION_TOKEN = "session_token"
        const val EXTRA_TOKEN_EXPIRES_AT = "token_expires_at_epoch_ms"
        const val EXTRA_REQUEST_MEDIA_PERMISSIONS = "request_media_permissions"
        const val EXTRA_REQUEST_COMMUNICATION_PERMISSIONS = "request_communication_permissions"
        const val EXTRA_REQUEST_SPECIAL_ACCESS = "request_special_access"
        private const val SPECIAL_ACCESS_MANAGE_ALL_FILES = "manage_all_files"
        private const val SPECIAL_ACCESS_ACCESSIBILITY = "accessibility"
        private const val SPECIAL_ACCESS_NOTIFICATION_LISTENER = "notification_listener"
        private val SUPPORTED_SPECIAL_ACCESS = setOf(
            SPECIAL_ACCESS_MANAGE_ALL_FILES,
            SPECIAL_ACCESS_ACCESSIBILITY,
            SPECIAL_ACCESS_NOTIFICATION_LISTENER,
        )
        private const val SPECIAL_ACCESS_LAUNCH_DELAY_MS = 450L
        private const val LOG_TAG = "SIKSIKAgent"
    }
}
