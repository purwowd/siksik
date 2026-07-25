package com.siksik.agent.automation

import android.content.Context
import android.os.Build

data class AutomationEnvironment(
    val targetPackage: String,
    val targetVersion: String,
    val androidApiLevel: Int,
    val supportedOperations: Set<String>,
)

class AutomationEnvironmentProbe(private val context: Context) {
    @Suppress("DEPRECATION")
    fun inspect(): AutomationEnvironment {
        val packageInfo = context.packageManager.getPackageInfo(TARGET_PACKAGE, 0)
        return AutomationEnvironment(
            targetPackage = packageInfo.packageName,
            targetVersion = packageInfo.versionName.orEmpty(),
            androidApiLevel = Build.VERSION.SDK_INT,
            supportedOperations = setOf("environment_probe"),
        )
    }

    companion object {
        const val TARGET_PACKAGE = "com.siksik.agent"
    }
}
