package com.siksik.agent.permission

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat

class RuntimePermissionProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_PROBE) {
            resultCode = Activity.RESULT_CANCELED
            resultData = "$RESULT_PREFIX;status=unsupported"
            return
        }
        val requested = try {
            context.packageManager.getPackageInfo(
                context.packageName,
                PackageManager.GET_PERMISSIONS,
            ).requestedPermissions?.toSet().orEmpty()
        } catch (_: PackageManager.NameNotFoundException) {
            emptySet()
        }
        val states = PERMISSIONS.joinToString(separator = ";") { permission ->
            val state = when {
                permission !in requested -> "unsupported"
                ContextCompat.checkSelfPermission(
                    context,
                    permission,
                ) == PackageManager.PERMISSION_GRANTED -> "granted"
                else -> "denied"
            }
            "$permission=$state"
        }
        resultCode = Activity.RESULT_OK
        resultData = "$RESULT_PREFIX;$states"
    }

    companion object {
        const val ACTION_PROBE = "com.siksik.agent.action.PROBE_RUNTIME_PERMISSIONS"
        const val RESULT_PREFIX = "SIKSIK_PERMISSION_V1"
        private val PERMISSIONS = listOf(
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_MEDIA_AUDIO",
            "android.permission.ACCESS_MEDIA_LOCATION",
            "android.permission.POST_NOTIFICATIONS",
            "android.permission.READ_SMS",
            "android.permission.READ_CONTACTS",
        )
    }
}
