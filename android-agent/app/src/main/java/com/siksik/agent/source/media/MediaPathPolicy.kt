package com.siksik.agent.source.media

private const val MAX_DIRECTORY_HINT_LENGTH = 256
private const val MAX_DIRECTORY_SEGMENTS = 16
private const val MAX_DIRECTORY_SEGMENT_LENGTH = 128
private const val MAX_DISPLAY_NAME_LENGTH = 255

internal fun normalizedDirectoryHint(raw: String?): String? {
    val segments = raw
        ?.trim()
        ?.trim('/')
        ?.split('/')
        ?.take(MAX_DIRECTORY_SEGMENTS)
        ?.mapNotNull { segment ->
            val cleaned = segment
                .replace(Regex("[\\p{Cntrl}\\\\]"), "_")
                .trim()
                .take(MAX_DIRECTORY_SEGMENT_LENGTH)
            when (cleaned) {
                "", ".", ".." -> null
                else -> cleaned
            }
        }
        .orEmpty()
    return segments.joinToString("/")
        .take(MAX_DIRECTORY_HINT_LENGTH)
        .trimEnd('/')
        .takeIf(String::isNotBlank)
}

internal fun joinedDirectoryHint(parent: String?, child: String): String? =
    normalizedDirectoryHint(listOfNotNull(parent, child).joinToString("/"))

internal fun safeDisplayName(raw: String?): String {
    val cleaned = raw.orEmpty().replace(Regex("[\\p{Cntrl}/\\\\]"), "_").trim()
    return (cleaned.ifBlank { "unnamed_media" }).take(MAX_DISPLAY_NAME_LENGTH)
}

internal fun isSupportedMedia(mime: String): Boolean =
    mime.startsWith("image/") || mime.startsWith("video/")

