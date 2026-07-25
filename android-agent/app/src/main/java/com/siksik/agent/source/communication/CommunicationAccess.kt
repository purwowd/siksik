package com.siksik.agent.source.communication

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ComponentName
import android.content.Context
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.core.app.NotificationManagerCompat
import com.siksik.agent.accessibility.CaptureAccessibilityService
import com.siksik.agent.notification.SessionNotificationListener

object CommunicationAccess {
    fun accessibilityEnabled(context: Context): Boolean {
        val expected = ComponentName(context, CaptureAccessibilityService::class.java)
        val manager = context.getSystemService(AccessibilityManager::class.java)
        val serviceEnabled = manager
            ?.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            .orEmpty()
            .any { capability ->
                val service = capability.resolveInfo?.serviceInfo ?: return@any false
                ComponentName(service.packageName, service.name) == expected
            }
        if (serviceEnabled) return true
        val enabled = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return enabled.split(':').any { value ->
            ComponentName.unflattenFromString(value.trim()) == expected
        }
    }

    fun notificationListenerEnabled(context: Context): Boolean =
        context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)
}
