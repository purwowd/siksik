package com.siksik.agent.automation

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import androidx.test.uiautomator.UiDevice
import com.siksik.agent.source.communication.CommunicationPolicy

internal object TextOnlyCrawlCoverClient {
    private const val AGENT_PACKAGE = "com.siksik.agent"
    private const val RECEIVER_CLASS =
        "com.siksik.agent.accessibility.TextOnlyCrawlCoverReceiver"
    private const val MAX_COVER_ATTEMPTS = 5
    private const val COVER_SETTLE_MS = 400L

    fun show(context: Context, device: UiDevice): Boolean =
        setVisibleWithVerification(context, device, visible = true)

    fun hide(context: Context, device: UiDevice) {
        setVisible(context, device, visible = false)
    }

    private fun setVisibleWithVerification(
        context: Context,
        device: UiDevice,
        visible: Boolean,
    ): Boolean {
        if (!visible) {
            setVisible(context, device, visible = false)
            return true
        }
        repeat(MAX_COVER_ATTEMPTS) {
            setVisible(context, device, visible = true)
            SystemClock.sleep(COVER_SETTLE_MS)
            if (probeCoverVisible(device)) return true
        }
        return false
    }

    private fun setVisible(context: Context, device: UiDevice, visible: Boolean) {
        val intent = Intent(CommunicationPolicy.TEXT_ONLY_COVER_ACTION)
            .setClassName(AGENT_PACKAGE, RECEIVER_CLASS)
            .putExtra(CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA, visible)
        context.sendBroadcast(intent)
        val flag = if (visible) "true" else "false"
        runCatching {
            device.executeShellCommand(
                "am broadcast --include-stopped-packages " +
                    "-a ${CommunicationPolicy.TEXT_ONLY_COVER_ACTION} " +
                    "--ez ${CommunicationPolicy.TEXT_ONLY_COVER_VISIBLE_EXTRA} $flag " +
                    "-n $AGENT_PACKAGE/$RECEIVER_CLASS",
            )
        }
    }

    private fun probeCoverVisible(device: UiDevice): Boolean =
        parseCoverProbeStatus(
            runCatching {
                device.executeShellCommand(
                    "am broadcast --include-stopped-packages " +
                        "-a ${CommunicationPolicy.TEXT_ONLY_COVER_PROBE_ACTION} " +
                        "-n $AGENT_PACKAGE/$RECEIVER_CLASS",
                )
            }.getOrDefault(""),
        ) == "shown"

    private fun parseCoverProbeStatus(output: String): String? {
        val marker = "${CommunicationPolicy.TEXT_ONLY_COVER_PROBE_PREFIX};status="
        val index = output.indexOf(marker)
        if (index < 0) return null
        return when {
            output.startsWith("shown", index + marker.length) -> "shown"
            output.startsWith("hidden", index + marker.length) -> "hidden"
            else -> null
        }
    }
}
