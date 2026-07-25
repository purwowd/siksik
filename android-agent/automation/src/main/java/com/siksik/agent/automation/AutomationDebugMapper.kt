package com.siksik.agent.automation

import android.content.Context
import android.util.Log
import androidx.test.uiautomator.UiDevice
import java.io.File
import java.io.FileOutputStream
import org.json.JSONArray
import org.json.JSONObject

internal class AutomationDebugMapper(
    private val context: Context,
    private val device: UiDevice,
    private val sessionId: String,
    private val crawlId: String,
    private val enabled: Boolean,
) {
    private val entries = JSONArray()
    private var targetPackage: String? = null
    private var targetDirectory: File? = null
    private var sequence = 0

    fun startTarget(packageName: String) {
        if (!enabled) return
        val externalRoot = context.getExternalFilesDir(DEBUG_DIRECTORY_NAME) ?: run {
            Log.w(LOG_TAG, "event=debug_mapping stage=storage_unavailable")
            return
        }
        val directory = File(File(File(externalRoot, sessionId), crawlId), packageName)
        try {
            if (directory.exists() && !directory.deleteRecursively()) {
                Log.w(LOG_TAG, "event=debug_mapping stage=cleanup_failed")
                return
            }
            if (!directory.mkdirs() && !directory.isDirectory) {
                Log.w(LOG_TAG, "event=debug_mapping stage=directory_failed")
                return
            }
        } catch (_: SecurityException) {
            Log.w(LOG_TAG, "event=debug_mapping stage=storage_denied")
            return
        }
        targetPackage = packageName
        targetDirectory = directory
        sequence = 0
        writeManifest()
        Log.i(LOG_TAG, "event=debug_mapping stage=ready target=$packageName")
    }

    fun capture(
        stage: String,
        scope: SocialScope? = null,
        status: String = "observed",
    ): Boolean {
        val packageName = targetPackage ?: return false
        val directory = targetDirectory ?: return false
        if (!enabled || sequence >= MAX_SCREENSHOTS) return false
        val normalizedStage = stage
            .lowercase()
            .replace(Regex("[^a-z0-9_-]+"), "_")
            .trim('_')
            .take(MAX_STAGE_LENGTH)
            .ifEmpty { "state" }
        sequence += 1
        val fileName = "%03d__%s.png".format(sequence, normalizedStage)
        val screenshot = File(directory, fileName)
        val captured = try {
            device.takeScreenshot(screenshot) && screenshot.isFile && screenshot.length() > 0L
        } catch (_: RuntimeException) {
            false
        } catch (_: SecurityException) {
            false
        }
        if (!captured) screenshot.delete()
        val foreground = try {
            device.currentPackageName
        } catch (_: RuntimeException) {
            null
        }
        entries.put(
            JSONObject()
                .put("sequence", sequence)
                .put("captured_at_epoch_ms", System.currentTimeMillis())
                .put("target_package", packageName)
                .put("scope", scope?.wireName ?: JSONObject.NULL)
                .put("stage", normalizedStage)
                .put("status", status.take(MAX_STATUS_LENGTH))
                .put("foreground_package", foreground ?: JSONObject.NULL)
                .put("screenshot", if (captured) fileName else JSONObject.NULL),
        )
        writeManifest()
        Log.i(
            LOG_TAG,
            "event=debug_mapping stage=$normalizedStage scope=${scope?.wireName ?: "none"} " +
                "captured=$captured sequence=$sequence",
        )
        return captured
    }

    private fun writeManifest() {
        val directory = targetDirectory ?: return
        val target = File(directory, MANIFEST_FILE_NAME)
        val temporary = File(directory, ".$MANIFEST_FILE_NAME.tmp")
        try {
            val document = JSONObject()
                .put("schema_version", 1)
                .put("session_id", sessionId)
                .put("crawl_id", crawlId)
                .put("target_package", targetPackage ?: JSONObject.NULL)
                .put("screenshots", entries)
            FileOutputStream(temporary).use { output ->
                output.write(document.toString(2).toByteArray(Charsets.UTF_8))
            }
            if (target.exists() && !target.delete()) return
            if (!temporary.renameTo(target)) temporary.delete()
        } catch (_: java.io.IOException) {
            temporary.delete()
        } catch (_: SecurityException) {
            temporary.delete()
        }
    }

    companion object {
        const val DEBUG_DIRECTORY_NAME = "social_crawl_debug"
        private const val MANIFEST_FILE_NAME = "mapping.json"
        private const val MAX_SCREENSHOTS = 72
        private const val MAX_STAGE_LENGTH = 64
        private const val MAX_STATUS_LENGTH = 128
        private const val LOG_TAG = "SIKSIKAutomation"
    }
}
