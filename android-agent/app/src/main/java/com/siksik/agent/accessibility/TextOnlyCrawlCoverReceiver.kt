package com.siksik.agent.accessibility

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.siksik.agent.source.communication.CommunicationPolicy

class TextOnlyCrawlCoverReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        when (intent?.action) {
            CommunicationPolicy.TEXT_ONLY_COVER_ACTION -> {
                val visible = intent.getBooleanExtra(
                    CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA,
                    false,
                )
                CaptureAccessibilityService.applyTextOnlyCover(visible)
            }
            CommunicationPolicy.A11Y_TAP_ACTION -> {
                if (!intent.hasExtra(CommunicationPolicy.A11Y_TAP_X_EXTRA) ||
                    !intent.hasExtra(CommunicationPolicy.A11Y_TAP_Y_EXTRA)
                ) return
                CaptureAccessibilityService.applyAccessibilityTap(
                    intent.getIntExtra(CommunicationPolicy.A11Y_TAP_X_EXTRA, -1),
                    intent.getIntExtra(CommunicationPolicy.A11Y_TAP_Y_EXTRA, -1),
                )
            }
        }
    }
}
