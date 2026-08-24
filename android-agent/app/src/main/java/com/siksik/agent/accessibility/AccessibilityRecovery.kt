package com.siksik.agent.accessibility

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.SystemClock
import android.provider.Settings
import androidx.core.content.ContextCompat
import com.siksik.agent.source.communication.CommunicationAccess

internal object AccessibilityRecovery {
    private const val REBIND_PAUSE_MS = 500L
    private const val COMPONENT_REBIND_SETTLE_MS = 2_000L
    private const val POST_REBIND_SETTLE_MS = 800L

    fun recover(context: Context): String {
        if (!hasWriteSecureSettings(context)) {
            return "denied"
        }
        if (CaptureAccessibilityService.isServiceConnected()) {
            return "bound"
        }
        val component = ComponentName(context, CaptureAccessibilityService::class.java)
        val flattened = component.flattenToString()
        val resolver = context.contentResolver
        val current = Settings.Secure.getString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        val components = current.split(':')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
        val withoutTarget = components.filterNot { isSameComponent(it, component) }
        val removed = Settings.Secure.putString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            withoutTarget.joinToString(":"),
        )
        if (!removed) return probeStatus(context)
        SystemClock.sleep(REBIND_PAUSE_MS)
        val restored = components.toMutableList().apply {
            if (none { isSameComponent(it, component) }) add(flattened)
        }
        val componentRestored = Settings.Secure.putString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            restored.joinToString(":"),
        )
        if (!componentRestored) {
            // Retry the original list once so a provider-side write failure does
            // not intentionally leave SIKSIK removed from the user's settings.
            Settings.Secure.putString(
                resolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
                restored.joinToString(":"),
            )
            return probeStatus(context)
        }
        if (
            Settings.Secure.getInt(
                resolver,
                Settings.Secure.ACCESSIBILITY_ENABLED,
                0,
            ) != 1
        ) {
            if (!Settings.Secure.putInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 1)) {
                return probeStatus(context)
            }
        }
        SystemClock.sleep(COMPONENT_REBIND_SETTLE_MS)
        val componentOnlyStatus = probeStatus(context)
        if (componentOnlyStatus == "bound") {
            return componentOnlyStatus
        }

        // Last-resort OEM fallback. Keep the complete service list intact and
        // immediately restore the master switch so foreign services are never
        // removed from enabled settings.
        if (!Settings.Secure.putInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 0)) {
            return probeStatus(context)
        }
        SystemClock.sleep(REBIND_PAUSE_MS)
        val fallbackRestored = Settings.Secure.putString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            restored.joinToString(":"),
        )
        val fallbackMasterOn = Settings.Secure.putInt(
            resolver,
            Settings.Secure.ACCESSIBILITY_ENABLED,
            1,
        )
        if (!fallbackRestored || !fallbackMasterOn) {
            Settings.Secure.putString(
                resolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
                restored.joinToString(":"),
            )
            Settings.Secure.putInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 1)
        }
        SystemClock.sleep(POST_REBIND_SETTLE_MS)
        return probeStatus(context)
    }

    fun suspend(context: Context): String {
        if (!hasWriteSecureSettings(context)) {
            return "denied"
        }
        val component = ComponentName(context, CaptureAccessibilityService::class.java)
        val resolver = context.contentResolver
        val current = Settings.Secure.getString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        val components = current.split(':')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
        val withoutTarget = components.filterNot { isSameComponent(it, component) }
        if (!Settings.Secure.putString(
            resolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            withoutTarget.joinToString(":"),
        )) {
            return probeStatus(context)
        }
        if (withoutTarget.isEmpty()) {
            Settings.Secure.putInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 0)
        }
        SystemClock.sleep(POST_REBIND_SETTLE_MS)
        return probeStatus(context)
    }

    fun probeStatus(context: Context): String {
        if (!hasWriteSecureSettings(context) &&
            !CommunicationAccess.accessibilityEnabled(context) &&
            !CaptureAccessibilityService.isServiceConnected()
        ) {
            val component = ComponentName(context, CaptureAccessibilityService::class.java)
            val flattened = component.flattenToString()
            val listed = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ).orEmpty().split(':').any { it.trim() == flattened }
            val masterOn = Settings.Secure.getInt(
                context.contentResolver,
                Settings.Secure.ACCESSIBILITY_ENABLED,
                0,
            ) == 1
            if (listed && masterOn) {
                return "crashed"
            }
            return "unbound"
        }
        if (CaptureAccessibilityService.isServiceConnected()) {
            return "bound"
        }
        val component = ComponentName(context, CaptureAccessibilityService::class.java)
        val flattened = component.flattenToString()
        val listed = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty().split(':').any { it.trim() == flattened }
        val masterOn = Settings.Secure.getInt(
            context.contentResolver,
            Settings.Secure.ACCESSIBILITY_ENABLED,
            0,
        ) == 1
        if (listed && masterOn) {
            return "crashed"
        }
        return "unbound"
    }

    private fun hasWriteSecureSettings(context: Context): Boolean =
        ContextCompat.checkSelfPermission(
            context,
            android.Manifest.permission.WRITE_SECURE_SETTINGS,
        ) == PackageManager.PERMISSION_GRANTED

    private fun isSameComponent(raw: String, expected: ComponentName): Boolean {
        val parsed = ComponentName.unflattenFromString(raw.trim()) ?: return false
        return parsed.packageName == expected.packageName &&
            parsed.className == expected.className
    }
}
