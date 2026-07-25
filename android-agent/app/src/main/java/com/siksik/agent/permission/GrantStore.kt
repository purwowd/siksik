package com.siksik.agent.permission

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.annotation.RequiresApi
import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import org.json.JSONObject
import java.util.UUID

class GrantStore(private val context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    @Synchronized
    fun createAwaiting(sessionId: String, grantId: String, scopeType: String): GrantRecord {
        require(SessionAuthenticator.SAFE_ID.matches(sessionId)) { "invalid session id" }
        require(SessionAuthenticator.SAFE_ID.matches(grantId)) { "invalid grant id" }
        require(scopeType in ALLOWED_SCOPES) { "invalid grant scope" }
        val existing = get(grantId)
        if (existing != null) {
            if (existing.scopeType != scopeType || existing.sessionId != sessionId) {
                throw ApiException("agent_session_mismatch", "Grant tidak sesuai dengan sesi.", 409)
            }
            return existing
        }
        return save(
            GrantRecord(
                grantId = grantId,
                sessionId = sessionId,
                scopeType = scopeType,
                effectiveScope = null,
                state = GrantState.AWAITING_USER,
                grantRef = null,
                approvedItemCount = null,
                updatedAtEpochMs = System.currentTimeMillis(),
            ),
        )
    }

    @Synchronized
    fun approve(
        grantId: String,
        effectiveScope: String,
        uris: Collection<Uri> = emptyList(),
        approvedItemCount: Int? = null,
    ): GrantRecord {
        val current = requireRecord(grantId)
        val record = current.copy(
            effectiveScope = effectiveScope,
            state = GrantState.APPROVED,
            grantRef = current.grantRef ?: "grantref_${UUID.randomUUID()}",
            approvedItemCount = approvedItemCount,
            updatedAtEpochMs = System.currentTimeMillis(),
        )
        preferences.edit()
            .putStringSet(uriKey(grantId), uris.map(Uri::toString).toSet())
            .apply()
        return save(record)
    }

    @Synchronized
    fun finish(grantId: String, state: GrantState): GrantRecord {
        require(state in setOf(GrantState.DENIED, GrantState.CANCELLED, GrantState.REVOKED))
        return save(
            requireRecord(grantId).copy(
                state = state,
                updatedAtEpochMs = System.currentTimeMillis(),
            ),
        )
    }

    @Synchronized
    fun get(grantId: String): GrantRecord? {
        val raw = preferences.getString(recordKey(grantId), null) ?: return null
        return decode(JSONObject(raw))
    }

    @Synchronized
    fun refreshRevocation(grantId: String): GrantRecord {
        val record = requireRecord(grantId)
        if (record.state != GrantState.APPROVED) {
            return record
        }
        val stillGranted = when (record.scopeType) {
            "photo_picker", "directory" -> storedUris(grantId).isNotEmpty() &&
                storedUris(grantId).all(::hasUriReadAccess)
            "media_library" -> hasLibraryAccess(record.effectiveScope)
            else -> false
        }
        return if (stillGranted) record else finish(grantId, GrantState.REVOKED)
    }

    @Synchronized
    fun getApproved(sessionId: String, grantId: String): GrantRecord {
        val record = refreshRevocation(grantId)
        if (record.sessionId != sessionId) {
            throw ApiException("agent_session_mismatch", "Grant bukan milik sesi aktif.", 409)
        }
        if (record.state == GrantState.REVOKED) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        }
        if (record.state != GrantState.APPROVED) {
            throw ApiException("conflict", "Grant belum disetujui.", 409)
        }
        return record
    }

    @Synchronized
    fun grantedUris(sessionId: String, grantId: String): List<Uri> {
        getApproved(sessionId, grantId)
        return storedUris(grantId)
    }

    private fun hasUriReadAccess(uri: Uri): Boolean {
        val result = context.checkUriPermission(
            uri,
            android.os.Process.myPid(),
            android.os.Process.myUid(),
            Intent.FLAG_GRANT_READ_URI_PERMISSION,
        )
        return result == PackageManager.PERMISSION_GRANTED
    }

    private fun hasLibraryAccess(effectiveScope: String?): Boolean = when {
        Build.VERSION.SDK_INT >= 34 && "selected" in effectiveScope.orEmpty() ->
            context.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED) ==
                PackageManager.PERMISSION_GRANTED &&
                (
                    "audio" !in effectiveScope.orEmpty() ||
                        context.checkSelfPermission(Manifest.permission.READ_MEDIA_AUDIO) ==
                        PackageManager.PERMISSION_GRANTED
                    )
        Build.VERSION.SDK_INT >= 33 -> requiredLibraryPermissions(effectiveScope).all {
            context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
        }
        else -> context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) ==
            PackageManager.PERMISSION_GRANTED
    }

    @RequiresApi(33)
    private fun requiredLibraryPermissions(effectiveScope: String?): List<String> {
        val scope = effectiveScope.orEmpty()
        return buildList {
            if ("images" in scope) add(Manifest.permission.READ_MEDIA_IMAGES)
            if ("videos" in scope) add(Manifest.permission.READ_MEDIA_VIDEO)
            if ("audio" in scope) add(Manifest.permission.READ_MEDIA_AUDIO)
        }.ifEmpty {
            listOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
                Manifest.permission.READ_MEDIA_AUDIO,
            )
        }
    }

    private fun storedUris(grantId: String): List<Uri> =
        preferences.getStringSet(uriKey(grantId), emptySet()).orEmpty().map(Uri::parse)

    private fun requireRecord(grantId: String): GrantRecord = get(grantId)
        ?: throw ApiException("not_found", "Grant tidak ditemukan.", 404)

    private fun save(record: GrantRecord): GrantRecord {
        preferences.edit().putString(recordKey(record.grantId), encode(record).toString()).apply()
        return record
    }

    private fun encode(record: GrantRecord): JSONObject = JSONObject()
        .put("grant_id", record.grantId)
        .put("session_id", record.sessionId)
        .put("scope_type", record.scopeType)
        .put("effective_scope", record.effectiveScope)
        .put("state", record.state.wireName)
        .put("grant_ref", record.grantRef)
        .put("approved_item_count", record.approvedItemCount)
        .put("updated_at_epoch_ms", record.updatedAtEpochMs)
        .put("grant_version", record.grantVersion)

    private fun decode(payload: JSONObject): GrantRecord {
        val state = GrantState.entries.firstOrNull { it.wireName == payload.getString("state") }
            ?: throw IllegalStateException("stored grant has an invalid state")
        return GrantRecord(
            grantId = payload.getString("grant_id"),
            sessionId = payload.getString("session_id"),
            scopeType = payload.getString("scope_type"),
            effectiveScope = payload.optString("effective_scope").takeIf(String::isNotBlank),
            state = state,
            grantRef = payload.optString("grant_ref").takeIf(String::isNotBlank),
            approvedItemCount = if (payload.isNull("approved_item_count")) {
                null
            } else {
                payload.getInt("approved_item_count")
            },
            updatedAtEpochMs = payload.getLong("updated_at_epoch_ms"),
            grantVersion = payload.optInt("grant_version", 1),
        )
    }

    private fun recordKey(grantId: String) = "record:$grantId"

    private fun uriKey(grantId: String) = "uris:$grantId"

    companion object {
        private const val PREFERENCES_NAME = "siksik_grant_state"
        val ALLOWED_SCOPES = setOf("photo_picker", "directory", "media_library")
    }
}
