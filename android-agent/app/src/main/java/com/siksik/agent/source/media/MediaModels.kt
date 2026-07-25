package com.siksik.agent.source.media

import android.net.Uri

data class CatalogItem(
    val mediaId: String,
    val displayName: String,
    val mimeType: String,
    val sizeBytes: Long?,
    val width: Int?,
    val height: Int?,
    val durationMs: Long?,
    val captureTimeEpochMs: Long?,
    val captureTimeSource: String,
    val dateAddedEpochMs: Long?,
    val dateModifiedEpochMs: Long?,
    val directoryHint: String?,
    val thumbnailAvailable: Boolean,
)

data class CatalogPage(
    val catalogVersion: String,
    val items: List<CatalogItem>,
    val nextCursor: String?,
    val truncated: Boolean,
)

data class ResolvedMedia(
    val mediaId: String,
    val displayName: String,
    val mimeType: String,
    val declaredSizeBytes: Long?,
    val uri: Uri,
)

data class CopiedMedia(val sizeBytes: Long, val sha256: String)

