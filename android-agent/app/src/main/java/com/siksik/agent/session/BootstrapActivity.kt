package com.siksik.agent.session

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.siksik.agent.R
import com.siksik.agent.permission.GrantCoordinator

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
    }

    companion object {
        const val ACTION_SHOW_STATUS = "com.siksik.agent.action.SHOW_STATUS"
        const val EXTRA_SESSION_ID = "session_id"
        const val EXTRA_SESSION_TOKEN = "session_token"
        const val EXTRA_TOKEN_EXPIRES_AT = "token_expires_at_epoch_ms"
        private const val LOG_TAG = "SIKSIKAgent"
    }
}
