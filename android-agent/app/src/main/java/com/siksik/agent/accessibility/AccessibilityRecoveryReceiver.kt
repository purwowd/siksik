package com.siksik.agent.accessibility

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.siksik.agent.source.communication.CommunicationPolicy

class AccessibilityRecoveryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val appContext = context?.applicationContext
        if (appContext == null) {
            resultCode = Activity.RESULT_CANCELED
            resultData = "${CommunicationPolicy.ACCESSIBILITY_PROBE_PREFIX};status=unbound"
            return
        }
        when (intent?.action) {
            CommunicationPolicy.ACCESSIBILITY_PROBE_ACTION -> {
                resultCode = Activity.RESULT_OK
                resultData = "${CommunicationPolicy.ACCESSIBILITY_PROBE_PREFIX};" +
                    "status=${AccessibilityRecovery.probeStatus(appContext)}"
            }
            CommunicationPolicy.ACCESSIBILITY_RECOVERY_ACTION -> {
                resultCode = Activity.RESULT_OK
                resultData = "${CommunicationPolicy.ACCESSIBILITY_PROBE_PREFIX};" +
                    "status=${AccessibilityRecovery.recover(appContext)}"
            }
            CommunicationPolicy.ACCESSIBILITY_SUSPEND_ACTION -> {
                resultCode = Activity.RESULT_OK
                resultData = "${CommunicationPolicy.ACCESSIBILITY_PROBE_PREFIX};" +
                    "status=${AccessibilityRecovery.suspend(appContext)}"
            }
            else -> {
                resultCode = Activity.RESULT_CANCELED
                resultData = "${CommunicationPolicy.ACCESSIBILITY_PROBE_PREFIX};status=unbound"
            }
        }
    }
}
