package com.siksik.agent.preprocessing

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import androidx.activity.result.contract.ActivityResultContracts
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.AgentCapabilitySnapshot
import com.siksik.agent.model.CapabilityState
import com.siksik.agent.model.CapabilityStatus
import com.siksik.agent.source.communication.CommunicationAccess

class CapabilityProbe(
    private val context: Context,
    private val preprocessing: () -> Map<String, EngineCapability> = { emptyMap() },
) {
    fun snapshot(sessionId: String): AgentCapabilitySnapshot {
        val images = visualPermissionState(mediaImagePermission())
        val videos = visualPermissionState(mediaVideoPermission())
        val audio = permissionState(mediaAudioPermission())
        val documents = documentCapabilityState()
        val publicMedia = aggregateMediaState(images, videos, audio)
        val smsPermission = permissionState(Manifest.permission.READ_SMS)
        val sms = if (
            context.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)
        ) {
            smsPermission
        } else {
            CapabilityState.UNAVAILABLE
        }
        val contacts = permissionState(Manifest.permission.READ_CONTACTS)
        val accessibility = specialAccessState(
            CommunicationAccess.accessibilityEnabled(context),
        )
        val notificationListener = specialAccessState(
            CommunicationAccess.notificationListenerEnabled(context),
        )
        return AgentCapabilitySnapshot(
            schemaVersion = BuildConfig.CAPABILITY_SCHEMA_VERSION,
            agentVersion = BuildConfig.AGENT_VERSION,
            agentBuildSha256 = BuildConfig.AGENT_BUILD_SHA256,
            apiVersion = BuildConfig.API_VERSION,
            apiPort = BuildConfig.API_PORT,
            androidApiLevel = Build.VERSION.SDK_INT,
            packageName = context.packageName,
            sourceCapabilities = linkedMapOf(
                "media_image" to CapabilityStatus(images, true),
                "media_video" to CapabilityStatus(videos, true),
                "media_audio" to CapabilityStatus(audio, true),
                "document" to CapabilityStatus(documents, true),
                "sms" to CapabilityStatus(sms, true),
                "contact" to CapabilityStatus(contacts, true),
                "visible_ui" to CapabilityStatus(accessibility, true),
                "notification" to CapabilityStatus(notificationListener, true),
            ),
            preprocessingCapabilities = preprocessingStatuses(),
            featureCapabilities = linkedMapOf(
                "loopback_api" to granted(),
                "photo_picker" to CapabilityStatus(
                    if (ActivityResultContracts.PickVisualMedia.isPhotoPickerAvailable(context)) {
                        CapabilityState.GRANTED
                    } else {
                        CapabilityState.UNAVAILABLE
                    },
                    false,
                ),
                "directory_grant" to granted(),
                "media_catalog" to granted(),
                "inventory_cursor_paging" to granted(),
                "inventory_resume" to granted(),
                "public_whatsapp_media" to CapabilityStatus(publicMedia, true),
                "public_telegram_media" to CapabilityStatus(publicMedia, true),
                "shared_storage_documents" to CapabilityStatus(documents, true),
                "sms_cursor_paging" to CapabilityStatus(sms, true),
                "contact_cursor_paging" to CapabilityStatus(contacts, true),
                "visible_ui_capture" to CapabilityStatus(accessibility, true),
                "notification_session_capture" to CapabilityStatus(notificationListener, true),
                "bounded_thumbnail" to granted(),
                "selective_staging" to granted(),
                "manifest_finalization" to granted(),
                "cleanup" to granted(),
                "automatic_scoring" to unavailable(true),
            ),
            permissionStates = linkedMapOf(
                "read_media_images" to CapabilityStatus(images, true),
                "read_media_video" to CapabilityStatus(videos, true),
                "read_media_audio" to CapabilityStatus(audio, true),
                "access_media_location" to CapabilityStatus(
                    permissionState(mediaLocationPermission()),
                    false,
                ),
                "post_notifications" to CapabilityStatus(
                    permissionState(notificationPermission()),
                    false,
                ),
                "read_sms" to CapabilityStatus(smsPermission, true),
                "read_contacts" to CapabilityStatus(contacts, true),
            ),
            specialAccessStates = linkedMapOf(
                "accessibility" to CapabilityStatus(accessibility, true),
                "notification_listener" to CapabilityStatus(notificationListener, true),
                "manage_all_files" to CapabilityStatus(
                    allFilesCapabilityState(),
                    Build.VERSION.SDK_INT >= 30,
                ),
            ),
            availableStorageBytes = availableStorageBytes(),
            activeSessionId = sessionId,
        )
    }

    private fun permissionState(permission: String?): CapabilityState {
        if (permission == null) {
            return CapabilityState.GRANTED
        }
        return if (context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) {
            CapabilityState.GRANTED
        } else {
            CapabilityState.NOT_GRANTED
        }
    }

    private fun visualPermissionState(permission: String): CapabilityState {
        val direct = permissionState(permission)
        if (direct == CapabilityState.GRANTED || Build.VERSION.SDK_INT < 34) return direct
        return if (
            context.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            CapabilityState.RESTRICTED
        } else {
            direct
        }
    }

    private fun mediaImagePermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_IMAGES
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun mediaVideoPermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_VIDEO
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun mediaAudioPermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_AUDIO
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun mediaLocationPermission(): String? = if (Build.VERSION.SDK_INT >= 29) {
        Manifest.permission.ACCESS_MEDIA_LOCATION
    } else {
        null
    }

    private fun documentCapabilityState(): CapabilityState = when {
        Build.VERSION.SDK_INT >= 30 && Environment.isExternalStorageManager() ->
            CapabilityState.GRANTED
        Build.VERSION.SDK_INT >= 30 -> CapabilityState.RESTRICTED
        else -> permissionState(Manifest.permission.READ_EXTERNAL_STORAGE)
    }

    private fun allFilesCapabilityState(): CapabilityState = when {
        Build.VERSION.SDK_INT < 30 -> CapabilityState.UNAVAILABLE
        Environment.isExternalStorageManager() -> CapabilityState.GRANTED
        else -> CapabilityState.NOT_GRANTED
    }

    private fun aggregateMediaState(vararg states: CapabilityState): CapabilityState = when {
        states.all { it == CapabilityState.GRANTED } -> CapabilityState.GRANTED
        states.any { it == CapabilityState.GRANTED } -> CapabilityState.RESTRICTED
        else -> CapabilityState.NOT_GRANTED
    }

    private fun notificationPermission(): String? = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.POST_NOTIFICATIONS
    } else {
        null
    }

    private fun specialAccessState(granted: Boolean): CapabilityState =
        if (granted) CapabilityState.GRANTED else CapabilityState.NOT_GRANTED

    private fun availableStorageBytes(): Long {
        val path = context.getExternalFilesDir(null) ?: context.filesDir
        return try {
            StatFs(path.absolutePath).availableBytes.coerceAtLeast(0L)
        } catch (_: IllegalArgumentException) {
            0L
        }
    }

    private fun granted() = CapabilityStatus(CapabilityState.GRANTED, false)

    private fun preprocessingStatuses(): Map<String, CapabilityStatus> {
        val values = preprocessing()
        return PREPROCESSING_NAMES.associateWith { name ->
            val capability = values[name]
            CapabilityStatus(
                when (capability?.availability) {
                    EngineAvailability.AVAILABLE -> CapabilityState.GRANTED
                    EngineAvailability.ERROR -> CapabilityState.ERROR
                    else -> CapabilityState.UNAVAILABLE
                },
                true,
            )
        }
    }

    private fun unavailable(required: Boolean) =
        CapabilityStatus(CapabilityState.UNAVAILABLE, required)

    companion object {
        private val PREPROCESSING_NAMES = listOf(
            "ocr",
            "document_text",
            "exact_hash",
            "perceptual_hash",
            "face_model",
            "object_model",
        )
    }
}
