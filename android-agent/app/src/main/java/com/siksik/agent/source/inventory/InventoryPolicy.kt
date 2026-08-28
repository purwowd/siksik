package com.siksik.agent.source.inventory

import android.webkit.MimeTypeMap
import java.security.MessageDigest
import java.util.Locale

object InventoryPolicy {
    val documentExtensions = setOf("pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "rtf")

    private val documentMimeByExtension = mapOf(
        "pdf" to "application/pdf",
        "doc" to "application/msword",
        "docx" to "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xls" to "application/vnd.ms-excel",
        "xlsx" to "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv" to "text/csv",
        "txt" to "text/plain",
        "rtf" to "application/rtf",
    )
    private val documentMimes = documentMimeByExtension.values + "text/rtf"
    private val favoriteTokens = listOf(
        "favorite",
        "favorites",
        "favourite",
        "favourites",
        "favorit",
    )
    val favoriteSqlLikePatterns = listOf("%favorit%", "%favourite%")
    private val publicPathFragments = mapOf(
        SourceAdapter.PUBLIC_WHATSAPP to listOf(
            "android/media/com.whatsapp/whatsapp/media/",
            "whatsapp/media/",
        ),
        SourceAdapter.PUBLIC_TELEGRAM to listOf(
            "android/media/org.telegram.messenger/telegram/",
            "telegram/",
        ),
    )

    fun sourceKind(mimeType: String, displayName: String): InventorySourceKind? = when {
        mimeType.startsWith("image/") -> InventorySourceKind.MEDIA_IMAGE
        mimeType.startsWith("video/") -> InventorySourceKind.MEDIA_VIDEO
        mimeType.startsWith("audio/") -> InventorySourceKind.MEDIA_AUDIO
        isSupportedDocument(mimeType, displayName) -> InventorySourceKind.DOCUMENT
        else -> null
    }

    fun isSupportedDocument(mimeType: String?, displayName: String): Boolean {
        val normalizedMime = mimeType?.lowercase()?.substringBefore(';')?.trim()
        if (normalizedMime in documentMimes) return true
        return extension(displayName) in documentExtensions
    }

    fun normalizedMime(mimeType: String?, displayName: String): String? {
        val normalized = mimeType?.lowercase()?.substringBefore(';')?.trim()
            ?.takeIf { it.length in 3..127 && '/' in it }
        if (normalized != null && normalized != "application/octet-stream") return normalized
        val extension = extension(displayName)
        return documentMimeByExtension[extension]
            ?: MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension)
    }

    fun safeDisplayName(raw: String?): String {
        val value = raw.orEmpty()
            .replace(Regex("[\\p{Cntrl}/\\\\]"), "_")
            .trim()
            .ifBlank { "unnamed_item" }
        return value.take(255)
    }

    fun normalizedDirectoryHint(raw: String?): String? {
        val parts = raw.orEmpty()
            .trim()
            .trim('/')
            .split('/')
            .take(24)
            .mapNotNull { rawPart ->
                val part = rawPart.replace(Regex("[\\p{Cntrl}\\\\]"), "_").trim().take(128)
                part.takeIf { it.isNotBlank() && it != "." && it != ".." }
            }
        return parts.joinToString("/").take(512).trimEnd('/').takeIf(String::isNotBlank)
    }

    fun joinedDirectoryHint(parent: String?, child: String): String? =
        normalizedDirectoryHint(listOfNotNull(parent, child).joinToString("/"))

    fun publicSqlPatterns(adapter: SourceAdapter): List<String> =
        publicPathFragments[adapter].orEmpty().flatMap { fragment ->
            listOf("$fragment%", "%/$fragment%")
        }

    fun isPublicDirectory(adapter: SourceAdapter, directoryHint: String?): Boolean {
        val normalized = normalizedDirectoryHint(directoryHint)?.lowercase() ?: return false
        return publicPathFragments[adapter].orEmpty().any { fragment ->
            normalized.startsWith(fragment) || "/$fragment" in normalized
        }
    }

    fun sourceApp(adapter: SourceAdapter, ownerPackageName: String?): String? = when {
        ownerPackageName == "com.whatsapp" -> "com.whatsapp"
        ownerPackageName == "com.whatsapp.w4b" -> "com.whatsapp.w4b"
        ownerPackageName == "org.telegram.messenger" -> "org.telegram.messenger"
        adapter == SourceAdapter.PUBLIC_WHATSAPP -> "com.whatsapp"
        adapter == SourceAdapter.PUBLIC_TELEGRAM -> "org.telegram.messenger"
        else -> null
    }

    fun identityHash(identity: String): String = sha256("siksik-inventory:$identity")

    fun recordId(identityHash: String): String = "record_${identityHash.take(40)}"

    fun sourceLocator(adapter: SourceAdapter, identityHash: String): String =
        "${adapter.wireName}:${identityHash.take(32)}"

    fun overlapDedupeHash(
        fallbackIdentityHash: String,
        displayName: String,
        mimeType: String,
        sizeBytes: Long?,
        modifiedAtEpochMs: Long?,
        directoryHint: String?,
    ): String {
        val directory = normalizedDirectoryHint(directoryHint) ?: return fallbackIdentityHash
        if (sizeBytes == null || modifiedAtEpochMs == null) return fallbackIdentityHash
        return identityHash(
            listOf(
                "shared_file",
                directory.lowercase(),
                safeDisplayName(displayName).lowercase(),
                mimeType.lowercase(),
                sizeBytes.toString(),
                modifiedAtEpochMs.toString(),
            ).joinToString(":"),
        )
    }

    fun extension(displayName: String): String = displayName.substringAfterLast('.', "").lowercase()

    fun looksFavorite(vararg parts: String?): Boolean {
        val haystack = parts.joinToString(" ") { part -> part.orEmpty() }
            .lowercase(Locale.ROOT)
        if (haystack.isBlank()) return false
        return favoriteTokens.any { token -> haystack.contains(token) }
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
