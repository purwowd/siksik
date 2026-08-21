package com.siksik.agent.automation

import android.content.Context
import android.content.Intent
import androidx.test.uiautomator.UiDevice
import com.siksik.agent.source.communication.CommunicationPolicy

internal object TextOnlyCrawlCoverClient {
    private const val AGENT_PACKAGE = "com.siksik.agent"
    private const val RECEIVER_CLASS =
        "com.siksik.agent.accessibility.TextOnlyCrawlCoverReceiver"

    fun show(context: Context, device: UiDevice) {
        setVisible(context, device, visible = true)
    }

    fun hide(context: Context, device: UiDevice) {
        setVisible(context, device, visible = false)
    }

    private fun setVisible(context: Context, device: UiDevice, visible: Boolean) {
        val intent = Intent(CommunicationPolicy.TEXT_ONLY_COVER_ACTION)
            .setClassName(AGENT_PACKAGE, RECEIVER_CLASS)
            .putExtra(CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA, visible)
        context.sendBroadcast(intent)
        val flag = if (visible) "true" else "false"
        runCatching {
            device.executeShellCommand(
                "am broadcast -a ${CommunicationPolicy.TEXT_ONLY_COVER_ACTION} " +
                    "--ez ${CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA} $flag " +
                    "-n $AGENT_PACKAGE/$RECEIVER_CLASS",
            )
        }
    }
}
