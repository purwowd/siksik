package com.siksik.agent.source.media

import android.content.Context
import android.net.Uri
import com.siksik.agent.session.SessionAuthenticator
import java.security.MessageDigest
import java.util.UUID

internal class MediaIdentifierStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun assign(
        grantId: String,
        uri: Uri,
        updates: MutableMap<String, String>,
    ): String {
        val fingerprint = sha256(uri.toString())
        val idKey = "id:$fingerprint"
        val existing = updates[idKey] ?: preferences.getString(idKey, null)
        val mediaId = if (existing != null && SessionAuthenticator.SAFE_ID.matches(existing)) {
            existing
        } else {
            "media_${UUID.randomUUID()}"
        }
        updates[idKey] = mediaId
        updates[uriKey(grantId, mediaId)] = uri.toString()
        return mediaId
    }

    fun commit(grantId: String, updates: Map<String, String>) {
        val uriPrefix = "uri:$grantId:"
        preferences.edit().also { editor ->
            preferences.all.keys
                .filter { it.startsWith(uriPrefix) && it !in updates }
                .forEach(editor::remove)
            updates.forEach { (key, value) -> editor.putString(key, value) }
        }.apply()
    }

    fun resolve(grantId: String, mediaId: String): Uri? =
        preferences.getString(uriKey(grantId, mediaId), null)?.let(Uri::parse)

    private fun uriKey(grantId: String, mediaId: String) = "uri:$grantId:$mediaId"

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    companion object {
        private const val PREFERENCES_NAME = "siksik_catalog_identifiers"
    }
}
