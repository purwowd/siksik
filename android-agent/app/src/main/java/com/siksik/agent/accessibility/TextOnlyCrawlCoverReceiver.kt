package com.siksik.agent.accessibility

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.siksik.agent.source.communication.CommunicationPolicy

class TextOnlyCrawlCoverReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        when (intent?.action) {
            CommunicationPolicy.TEXT_ONLY_COVER_PROBE_ACTION -> {
                val connected = CaptureAccessibilityService.isServiceConnected()
                val attached = connected && (
                    CaptureAccessibilityService.isTextOnlyCoverAttached() ||
                        context?.let(TextOnlyCrawlCoverState::isMarkedAttached) == true
                    )
                if (!connected && context != null) {
                    TextOnlyCrawlCoverState.markAttached(context, false)
                }
                resultCode = Activity.RESULT_OK
                resultData = "${CommunicationPolicy.TEXT_ONLY_COVER_PROBE_PREFIX};" +
                    "status=${if (attached) "shown" else "hidden"}"
            }
            CommunicationPolicy.TEXT_ONLY_COVER_ACTION -> {
                val visible = intent.getBooleanExtra(
                    CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA,
                    false,
                )
                val applied = CaptureAccessibilityService.applyTextOnlyCover(visible)
                if (context != null && !applied) {
                    TextOnlyCrawlCoverState.markAttached(context, false)
                }
            }
            CommunicationPolicy.A11Y_TAP_ACTION -> {
                if (!intent.hasExtra(CommunicationPolicy.A11Y_TAP_X_EXTRA) ||
                    !intent.hasExtra(CommunicationPolicy.A11Y_TAP_Y_EXTRA)
                ) {
                    reportGesture(false)
                    return
                }
                reportGesture(
                    CaptureAccessibilityService.applyAccessibilityTap(
                        intent.getIntExtra(CommunicationPolicy.A11Y_TAP_X_EXTRA, -1),
                        intent.getIntExtra(CommunicationPolicy.A11Y_TAP_Y_EXTRA, -1),
                    ),
                )
            }
            CommunicationPolicy.A11Y_SWIPE_ACTION -> {
                val required = listOf(
                    CommunicationPolicy.A11Y_SWIPE_X_FROM_EXTRA,
                    CommunicationPolicy.A11Y_SWIPE_Y_FROM_EXTRA,
                    CommunicationPolicy.A11Y_SWIPE_X_TO_EXTRA,
                    CommunicationPolicy.A11Y_SWIPE_Y_TO_EXTRA,
                    CommunicationPolicy.A11Y_SWIPE_DURATION_EXTRA,
                )
                if (required.any { key -> !intent.hasExtra(key) }) {
                    reportGesture(false)
                    return
                }
                reportGesture(
                    CaptureAccessibilityService.applyAccessibilitySwipe(
                        intent.getIntExtra(CommunicationPolicy.A11Y_SWIPE_X_FROM_EXTRA, -1),
                        intent.getIntExtra(CommunicationPolicy.A11Y_SWIPE_Y_FROM_EXTRA, -1),
                        intent.getIntExtra(CommunicationPolicy.A11Y_SWIPE_X_TO_EXTRA, -1),
                        intent.getIntExtra(CommunicationPolicy.A11Y_SWIPE_Y_TO_EXTRA, -1),
                        intent.getLongExtra(CommunicationPolicy.A11Y_SWIPE_DURATION_EXTRA, 0L),
                    ),
                )
            }
            CommunicationPolicy.A11Y_BACK_ACTION -> {
                reportGesture(CaptureAccessibilityService.applyAccessibilityBack())
            }
        }
    }

    private fun reportGesture(accepted: Boolean) {
        resultCode = if (accepted) Activity.RESULT_OK else Activity.RESULT_CANCELED
        resultData = "${CommunicationPolicy.A11Y_GESTURE_RESULT_PREFIX};" +
            "status=${if (accepted) "accepted" else "unavailable"}"
    }
}
