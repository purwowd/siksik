package com.siksik.agent.permission

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.session.BootstrapActivity

class GrantCoordinator(
    private val activity: BootstrapActivity,
    private val store: GrantStore,
    private val onStatusChanged: (GrantRecord) -> Unit,
) {
    private var pendingGrantId: String? = null
    private var pendingScope: String? = null

    private val photoPicker = activity.registerForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(BuildConfig.MAX_PHOTO_ITEMS),
    ) { uris ->
        val grantId = consumePending("photo_picker") ?: return@registerForActivityResult
        if (uris.isEmpty()) {
            publish(store.finish(grantId, GrantState.CANCELLED))
        } else {
            uris.forEach(::persistReadAccessWhenSupported)
            publish(
                store.approve(
                    grantId,
                    effectiveScope = "explicit_selection",
                    uris = uris,
                    approvedItemCount = uris.size,
                ),
            )
        }
    }

    private val directoryPicker = activity.registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        val grantId = consumePending("directory") ?: return@registerForActivityResult
        if (uri == null) {
            publish(store.finish(grantId, GrantState.CANCELLED))
        } else {
            persistReadAccessWhenSupported(uri)
            publish(store.approve(grantId, effectiveScope = "directory", uris = listOf(uri)))
        }
    }

    private val permissionRequest = activity.registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { result ->
        val grantId = consumePending("media_library") ?: return@registerForActivityResult
        val effectiveScope = effectiveLibraryScope(result)
        publish(
            if (effectiveScope == null) {
                store.finish(grantId, GrantState.DENIED)
            } else {
                store.approve(grantId, effectiveScope = effectiveScope)
            },
        )
    }

    private val communicationPermissionRequest = activity.registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { result ->
        val grantId = consumePending("communication_runtime") ?: return@registerForActivityResult
        publish(
            if (effectiveCommunicationScope(result) == null) {
                store.finish(grantId, GrantState.DENIED)
            } else {
                store.approve(grantId, effectiveScope = "sms_and_contacts")
            },
        )
    }

    @Synchronized
    fun launch(sessionId: String, scope: String, grantId: String, maxItems: Int): GrantRecord {
        if (scope !in GrantStore.ALLOWED_SCOPES) {
            throw ApiException("grant_unsupported", "Jenis grant tidak didukung.", 422)
        }
        if (pendingGrantId != null) {
            throw ApiException("conflict", "Permintaan akses lain masih aktif.", 409)
        }
        if (maxItems !in 1..BuildConfig.MAX_PHOTO_ITEMS) {
            throw ApiException("validation_error", "Batas item Photo Picker tidak valid.", 422)
        }
        val record = store.createAwaiting(sessionId, grantId, scope)
        if (record.state != GrantState.AWAITING_USER) {
            return record
        }
        pendingGrantId = grantId
        pendingScope = scope
        activity.runOnUiThread {
            when (scope) {
                "photo_picker" -> photoPicker.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageAndVideo),
                )
                "directory" -> directoryPicker.launch(null)
                "media_library" -> permissionRequest.launch(libraryPermissions())
                "communication_runtime" -> communicationPermissionRequest.launch(
                    communicationPermissions(),
                )
            }
        }
        publish(record)
        return record
    }

    @Synchronized
    private fun consumePending(expectedScope: String): String? {
        if (pendingScope != expectedScope) {
            return null
        }
        val grantId = pendingGrantId
        pendingGrantId = null
        pendingScope = null
        return grantId
    }

    private fun publish(record: GrantRecord) {
        onStatusChanged(record)
    }

    private fun persistReadAccessWhenSupported(uri: Uri) {
        try {
            activity.contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION,
            )
        } catch (_: SecurityException) {
            return
        }
    }

    private fun libraryPermissions(): Array<String> = when {
        Build.VERSION.SDK_INT >= 34 -> arrayOf(
            Manifest.permission.READ_MEDIA_IMAGES,
            Manifest.permission.READ_MEDIA_VIDEO,
            Manifest.permission.READ_MEDIA_AUDIO,
            Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
        )
        Build.VERSION.SDK_INT >= 33 -> arrayOf(
            Manifest.permission.READ_MEDIA_IMAGES,
            Manifest.permission.READ_MEDIA_VIDEO,
            Manifest.permission.READ_MEDIA_AUDIO,
        )
        else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
    }

    private fun communicationPermissions(): Array<String> = arrayOf(
        Manifest.permission.READ_SMS,
        Manifest.permission.READ_CONTACTS,
    )

    private fun effectiveCommunicationScope(result: Map<String, Boolean>): String? {
        val sms = result[Manifest.permission.READ_SMS] == true
        val contacts = result[Manifest.permission.READ_CONTACTS] == true
        return if (sms && contacts) "sms_and_contacts" else null
    }

    private fun effectiveLibraryScope(result: Map<String, Boolean>): String? {
        if (
            Build.VERSION.SDK_INT >= 34 &&
            result[Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED] == true &&
            result[Manifest.permission.READ_MEDIA_IMAGES] != true &&
            result[Manifest.permission.READ_MEDIA_VIDEO] != true
        ) {
            return if (result[Manifest.permission.READ_MEDIA_AUDIO] == true) {
                "media_library_selected_and_audio"
            } else {
                "media_library_selected"
            }
        }
        if (Build.VERSION.SDK_INT >= 33) {
            val images = result[Manifest.permission.READ_MEDIA_IMAGES] == true
            val videos = result[Manifest.permission.READ_MEDIA_VIDEO] == true
            val audio = result[Manifest.permission.READ_MEDIA_AUDIO] == true
            val scopes = buildList {
                if (images) add("images")
                if (videos) add("videos")
                if (audio) add("audio")
            }
            return scopes.takeIf(List<String>::isNotEmpty)?.joinToString(
                separator = "_and_",
                prefix = "media_library_",
            )
        }
        return if (result[Manifest.permission.READ_EXTERNAL_STORAGE] == true) {
            "media_library_images_and_videos_and_audio"
        } else {
            null
        }
    }
}
