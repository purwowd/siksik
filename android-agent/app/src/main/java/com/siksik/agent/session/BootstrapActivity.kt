package com.siksik.agent.session

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.siksik.agent.R
import com.siksik.agent.model.ApiException
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

    companion object {
        const val ACTION_SHOW_STATUS = "com.siksik.agent.action.SHOW_STATUS"
        const val EXTRA_SESSION_ID = "session_id"
        const val EXTRA_SESSION_TOKEN = "session_token"
        const val EXTRA_TOKEN_EXPIRES_AT = "token_expires_at_epoch_ms"
        const val EXTRA_REQUEST_MEDIA_PERMISSIONS = "request_media_permissions"
        const val EXTRA_REQUEST_COMMUNICATION_PERMISSIONS = "request_communication_permissions"
        private const val LOG_TAG = "SIKSIKAgent"
    }
}
