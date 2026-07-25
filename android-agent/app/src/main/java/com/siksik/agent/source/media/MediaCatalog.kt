package com.siksik.agent.source.media

import android.content.ContentUris
import android.content.Context
import android.database.Cursor
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.util.Size
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.session.SessionAuthenticator
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.OutputStream
import java.security.MessageDigest
import java.util.ArrayDeque
import java.util.UUID

class MediaCatalog(
    context: Context,
    private val grants: GrantGateway,
    private val clock: () -> Long = { System.currentTimeMillis() },
) {
    private val resolver = context.applicationContext.contentResolver
    private val identifiers = MediaIdentifierStore(context.applicationContext)
    private val snapshots = object : LinkedHashMap<String, Snapshot>(8, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Snapshot>?): Boolean =
            size > MAX_SNAPSHOTS
    }
    private val cursors = object : LinkedHashMap<String, CursorState>(64, 0.75f, true) {
        override fun removeEldestEntry(
            eldest: MutableMap.MutableEntry<String, CursorState>?,
        ): Boolean = size > MAX_CURSORS
    }

    @Synchronized
    fun list(
        sessionId: String,
        grantId: String,
        cursor: String?,
        limit: Int,
        reuseSnapshot: Boolean = false,
    ): CatalogPage {
        if (limit !in 1..MAX_PAGE_SIZE) {
            throw ApiException("validation_error", "Batas halaman katalog tidak valid.", 422)
        }
        grants.getApproved(sessionId, grantId)
        removeExpiredSnapshots()
        val (snapshot, offset) = if (cursor == null) {
            val reusable = if (reuseSnapshot) {
                snapshots.values.lastOrNull {
                    it.sessionId == sessionId && it.grantId == grantId
                }
            } else {
                null
            }
            reusable?.let { it to 0 } ?: run {
                val listed = enumerate(sessionId, grantId)
                val version = "catalog_${UUID.randomUUID()}"
                val created = Snapshot(
                    version,
                    sessionId,
                    grantId,
                    listed.first,
                    listed.second,
                    clock(),
                )
                snapshots[version] = created
                created to 0
            }
        } else {
            if (!SessionAuthenticator.SAFE_ID.matches(cursor)) {
                throw ApiException("validation_error", "Cursor katalog tidak valid.", 422)
            }
            val state = cursors[cursor]
                ?: throw ApiException("invalid_cursor", "Cursor katalog sudah kedaluwarsa.", 422)
            val existing = snapshots[state.catalogVersion]
                ?: throw ApiException("invalid_cursor", "Cursor katalog sudah kedaluwarsa.", 422)
            if (existing.sessionId != sessionId || existing.grantId != grantId) {
                throw ApiException(
                    "agent_session_mismatch",
                    "Cursor katalog tidak sesuai dengan grant.",
                    409,
                )
            }
            existing to state.offset
        }
        val end = (offset + limit).coerceAtMost(snapshot.items.size)
        val nextCursor = if (end < snapshot.items.size) {
            "cursor_${UUID.randomUUID()}".also {
                cursors[it] = CursorState(snapshot.catalogVersion, end)
            }
        } else {
            null
        }
        return CatalogPage(
            snapshot.catalogVersion,
            snapshot.items.subList(offset, end),
            nextCursor,
            snapshot.truncated,
        )
    }

    fun thumbnail(
        sessionId: String,
        grantId: String,
        mediaId: String,
        maxDimension: Int,
    ): ByteArray {
        validateMediaId(mediaId)
        if (maxDimension !in MIN_THUMBNAIL_DIMENSION..MAX_THUMBNAIL_DIMENSION) {
            throw ApiException("validation_error", "Dimensi thumbnail tidak valid.", 422)
        }
        grants.getApproved(sessionId, grantId)
        val uri = identifiers.resolve(grantId, mediaId)
            ?: throw ApiException("not_found", "Media tidak ditemukan.", 404)
        val loaded = try {
            if (Build.VERSION.SDK_INT >= 29) {
                resolver.loadThumbnail(uri, Size(maxDimension, maxDimension), null)
            } else {
                decodeSampledBitmap(uri, maxDimension)
            }
        } catch (_: SecurityException) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        } catch (_: IOException) {
            throw ApiException("media_decode_failed", "Thumbnail tidak dapat dibaca.", 422)
        } catch (_: IllegalArgumentException) {
            throw ApiException("media_decode_failed", "Thumbnail tidak dapat diproses.", 422)
        } ?: throw ApiException("media_decode_failed", "Thumbnail tidak dapat diproses.", 422)
        val bitmap = if (loaded.width > maxDimension || loaded.height > maxDimension) {
            val scale = minOf(
                maxDimension.toFloat() / loaded.width,
                maxDimension.toFloat() / loaded.height,
            )
            Bitmap.createScaledBitmap(
                loaded,
                (loaded.width * scale).toInt().coerceAtLeast(1),
                (loaded.height * scale).toInt().coerceAtLeast(1),
                true,
            ).also { loaded.recycle() }
        } else {
            loaded
        }
        try {
            for (quality in listOf(82, 70, 55)) {
                val output = ByteArrayOutputStream()
                if (!bitmap.compress(Bitmap.CompressFormat.JPEG, quality, output)) {
                    continue
                }
                val bytes = output.toByteArray()
                if (bytes.size <= BuildConfig.MAX_THUMBNAIL_BYTES) {
                    return bytes
                }
            }
        } finally {
            bitmap.recycle()
        }
        throw ApiException("media_too_large", "Thumbnail melewati batas ukuran.", 413)
    }

    fun resolveForStaging(sessionId: String, grantId: String, mediaId: String): ResolvedMedia {
        validateMediaId(mediaId)
        grants.getApproved(sessionId, grantId)
        val uri = identifiers.resolve(grantId, mediaId)
            ?: throw ApiException("not_found", "Media tidak ditemukan.", 404)
        val mime = resolver.getType(uri)?.takeIf(::isSupportedMedia)
            ?: throw ApiException("validation_error", "Tipe media tidak didukung.", 422)
        var displayName: String? = null
        var size: Long? = null
        try {
            resolver.query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
                null,
                null,
                null,
            )?.use { cursor ->
                if (cursor.moveToFirst()) {
                    displayName = cursor.text(OpenableColumns.DISPLAY_NAME)
                    size = cursor.long(OpenableColumns.SIZE)?.nonNegative()
                }
            }
        } catch (_: SecurityException) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        } catch (_: IllegalArgumentException) {
            throw ApiException("stage_failed", "Metadata media tidak dapat dibaca.", 422)
        }
        return ResolvedMedia(mediaId, safeDisplayName(displayName), mime, size, uri)
    }

    fun copyForStaging(
        source: ResolvedMedia,
        output: OutputStream,
        maxBytes: Long,
        isCancelled: () -> Boolean,
    ): CopiedMedia {
        if (source.declaredSizeBytes != null && source.declaredSizeBytes > maxBytes) {
            throw ApiException("media_too_large", "Media melewati batas staging.", 413)
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        try {
            val input = resolver.openInputStream(source.uri)
                ?: throw ApiException("stage_failed", "Stream media tidak tersedia.", 422)
            input.use { stream ->
                val buffer = ByteArray(COPY_BUFFER_BYTES)
                while (true) {
                    if (isCancelled()) {
                        throw ApiException("stage_cancelled", "Staging dibatalkan.", 409)
                    }
                    val read = stream.read(buffer)
                    if (read < 0) break
                    if (read == 0) continue
                    total += read
                    if (total > maxBytes) {
                        throw ApiException("media_too_large", "Media melewati batas staging.", 413)
                    }
                    output.write(buffer, 0, read)
                    digest.update(buffer, 0, read)
                }
            }
        } catch (exception: ApiException) {
            throw exception
        } catch (_: SecurityException) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        } catch (_: IOException) {
            throw ApiException("stage_failed", "Media tidak dapat disalin ke staging.", 422)
        }
        if (total <= 0) {
            throw ApiException("stage_failed", "Media kosong tidak dapat diproses.", 422)
        }
        return CopiedMedia(
            total,
            digest.digest().joinToString("") { "%02x".format(it) },
        )
    }

    private fun enumerate(sessionId: String, grantId: String): Pair<List<CatalogItem>, Boolean> {
        val grant = grants.getApproved(sessionId, grantId)
        val updates = linkedMapOf<String, String>()
        val items = when (grant.scopeType) {
            "media_library" -> queryMediaLibrary(grantId, grant.effectiveScope, updates)
            "photo_picker" -> queryExplicitUris(sessionId, grantId, updates)
            "directory" -> queryDirectories(sessionId, grantId, updates)
            else -> throw ApiException("grant_unsupported", "Grant tidak didukung.", 422)
        }
        identifiers.commit(grantId, updates)
        val truncated = items.size > BuildConfig.MAX_CATALOG_ITEMS
        return items.take(BuildConfig.MAX_CATALOG_ITEMS) to truncated
    }

    private fun queryMediaLibrary(
        grantId: String,
        effectiveScope: String?,
        updates: MutableMap<String, String>,
    ): List<CatalogItem> {
        val collection = MediaStore.Files.getContentUri("external")
        val projection = mutableListOf(
            MediaStore.Files.FileColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.MIME_TYPE,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.WIDTH,
            MediaStore.MediaColumns.HEIGHT,
            MediaStore.Images.ImageColumns.DATE_TAKEN,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.DATE_MODIFIED,
            MediaStore.Files.FileColumns.MEDIA_TYPE,
            MediaStore.Video.VideoColumns.DURATION,
        )
        if (Build.VERSION.SDK_INT >= 29) {
            projection.add(MediaStore.MediaColumns.RELATIVE_PATH)
        }
        val allowedTypes = when (effectiveScope) {
            "media_library_images" -> listOf(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE)
            "media_library_videos" -> listOf(MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO)
            else -> listOf(
                MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE,
                MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO,
            )
        }
        val placeholders = allowedTypes.joinToString(",") { "?" }
        val selection = "${MediaStore.Files.FileColumns.MEDIA_TYPE} IN ($placeholders)"
        val arguments = allowedTypes.map(Int::toString).toTypedArray()
        val result = mutableListOf<CatalogItem>()
        try {
            resolver.query(
                collection,
                projection.toTypedArray(),
                selection,
                arguments,
                "${MediaStore.Images.ImageColumns.DATE_TAKEN} DESC, " +
                    "${MediaStore.Files.FileColumns._ID} DESC",
            )?.use { cursor ->
                while (cursor.moveToNext() && result.size <= BuildConfig.MAX_CATALOG_ITEMS) {
                    val id = cursor.long(MediaStore.Files.FileColumns._ID) ?: continue
                    val uri = ContentUris.withAppendedId(collection, id)
                    val mime = cursor.text(MediaStore.MediaColumns.MIME_TYPE)
                        ?.takeIf(::isSupportedMedia) ?: continue
                    val dateTaken = cursor.long(MediaStore.Images.ImageColumns.DATE_TAKEN)
                        ?.takeIf { it > 0 }
                    val dateAdded = cursor.long(MediaStore.MediaColumns.DATE_ADDED)
                        ?.takeIf { it > 0 }?.times(1000)
                    val dateModified = cursor.long(MediaStore.MediaColumns.DATE_MODIFIED)
                        ?.takeIf { it > 0 }?.times(1000)
                    val capture = dateTaken ?: dateAdded ?: dateModified
                    val captureSource = when {
                        dateTaken != null -> "date_taken"
                        dateAdded != null -> "date_added"
                        dateModified != null -> "date_modified"
                        else -> "unknown"
                    }
                    result.add(
                        CatalogItem(
                            mediaId = identifiers.assign(grantId, uri, updates),
                            displayName = safeDisplayName(
                                cursor.text(MediaStore.MediaColumns.DISPLAY_NAME),
                            ),
                            mimeType = mime,
                            sizeBytes = cursor.long(MediaStore.MediaColumns.SIZE)?.nonNegative(),
                            width = cursor.int(MediaStore.MediaColumns.WIDTH)?.positive(),
                            height = cursor.int(MediaStore.MediaColumns.HEIGHT)?.positive(),
                            durationMs = cursor.long(MediaStore.Video.VideoColumns.DURATION)
                                ?.nonNegative(),
                            captureTimeEpochMs = capture,
                            captureTimeSource = captureSource,
                            dateAddedEpochMs = dateAdded,
                            dateModifiedEpochMs = dateModified,
                            directoryHint = if (Build.VERSION.SDK_INT >= 29) {
                                normalizedDirectoryHint(
                                    cursor.text(MediaStore.MediaColumns.RELATIVE_PATH),
                                )
                            } else {
                                null
                            },
                            thumbnailAvailable = mime.startsWith("image/") ||
                                Build.VERSION.SDK_INT >= 29,
                        ),
                    )
                }
            }
        } catch (_: SecurityException) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        } catch (_: IllegalArgumentException) {
            throw ApiException("validation_error", "Katalog media tidak dapat dibaca.", 422)
        }
        return result
    }

    private fun queryExplicitUris(
        sessionId: String,
        grantId: String,
        updates: MutableMap<String, String>,
    ): List<CatalogItem> = grants.grantedUris(sessionId, grantId)
        .take(BuildConfig.MAX_CATALOG_ITEMS + 1)
        .mapNotNull { uri -> querySingleUri(grantId, uri, null, updates) }
        .sortedWith(
            compareByDescending<CatalogItem> { it.captureTimeEpochMs ?: 0L }
                .thenBy(CatalogItem::mediaId),
        )

    private fun queryDirectories(
        sessionId: String,
        grantId: String,
        updates: MutableMap<String, String>,
    ): List<CatalogItem> {
        val result = mutableListOf<CatalogItem>()
        for (tree in grants.grantedUris(sessionId, grantId)) {
            val rootId = try {
                DocumentsContract.getTreeDocumentId(tree)
            } catch (_: IllegalArgumentException) {
                continue
            }
            val queue = ArrayDeque<DirectoryNode>()
            queue.add(DirectoryNode(rootId, 0, null))
            while (queue.isNotEmpty() && result.size <= BuildConfig.MAX_CATALOG_ITEMS) {
                val node = queue.removeFirst()
                val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, node.id)
                try {
                    resolver.query(children, DOCUMENT_PROJECTION, null, null, null)?.use { cursor ->
                        while (cursor.moveToNext() && result.size <= BuildConfig.MAX_CATALOG_ITEMS) {
                            val documentId = cursor.text(
                                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                            ) ?: continue
                            val name = safeDisplayName(
                                cursor.text(DocumentsContract.Document.COLUMN_DISPLAY_NAME),
                            )
                            val mime = cursor.text(DocumentsContract.Document.COLUMN_MIME_TYPE)
                                ?: continue
                            if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                                if (node.depth < MAX_DIRECTORY_DEPTH) {
                                    queue.add(
                                        DirectoryNode(
                                            documentId,
                                            node.depth + 1,
                                            joinedDirectoryHint(node.directoryHint, name),
                                        ),
                                    )
                                }
                                continue
                            }
                            if (!isSupportedMedia(mime)) continue
                            val uri = DocumentsContract.buildDocumentUriUsingTree(tree, documentId)
                            val modified = cursor.long(
                                DocumentsContract.Document.COLUMN_LAST_MODIFIED,
                            )?.takeIf { it > 0 }
                            result.add(
                                CatalogItem(
                                    mediaId = identifiers.assign(grantId, uri, updates),
                                    displayName = name,
                                    mimeType = mime,
                                    sizeBytes = cursor.long(
                                        DocumentsContract.Document.COLUMN_SIZE,
                                    )?.nonNegative(),
                                    width = null,
                                    height = null,
                                    durationMs = null,
                                    captureTimeEpochMs = modified,
                                    captureTimeSource = if (modified == null) {
                                        "unknown"
                                    } else {
                                        "date_modified"
                                    },
                                    dateAddedEpochMs = null,
                                    dateModifiedEpochMs = modified,
                                    directoryHint = node.directoryHint,
                                    thumbnailAvailable = mime.startsWith("image/") ||
                                        Build.VERSION.SDK_INT >= 29,
                                ),
                            )
                        }
                    }
                } catch (_: SecurityException) {
                    throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
                } catch (_: IllegalArgumentException) {
                    continue
                }
            }
        }
        return result.sortedWith(
            compareByDescending<CatalogItem> { it.captureTimeEpochMs ?: 0L }
                .thenBy(CatalogItem::mediaId),
        )
    }

    private fun querySingleUri(
        grantId: String,
        uri: Uri,
        directoryHint: String?,
        updates: MutableMap<String, String>,
    ): CatalogItem? {
        val mime = resolver.getType(uri)?.takeIf(::isSupportedMedia) ?: return null
        var name: String? = null
        var size: Long? = null
        try {
            resolver.query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
                null,
                null,
                null,
            )?.use { cursor ->
                if (cursor.moveToFirst()) {
                    name = cursor.text(OpenableColumns.DISPLAY_NAME)
                    size = cursor.long(OpenableColumns.SIZE)?.nonNegative()
                }
            }
        } catch (_: SecurityException) {
            throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
        } catch (_: IllegalArgumentException) {
            return null
        }
        return CatalogItem(
            mediaId = identifiers.assign(grantId, uri, updates),
            displayName = safeDisplayName(name),
            mimeType = mime,
            sizeBytes = size,
            width = null,
            height = null,
            durationMs = null,
            captureTimeEpochMs = null,
            captureTimeSource = "unknown",
            dateAddedEpochMs = null,
            dateModifiedEpochMs = null,
            directoryHint = directoryHint,
            thumbnailAvailable = mime.startsWith("image/") || Build.VERSION.SDK_INT >= 29,
        )
    }

    private fun decodeSampledBitmap(uri: Uri, maxDimension: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (
            bounds.outWidth / sample > maxDimension * 2 ||
            bounds.outHeight / sample > maxDimension * 2
        ) {
            sample *= 2
        }
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        return resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
    }

    @Synchronized
    private fun removeExpiredSnapshots() {
        val cutoff = clock() - SNAPSHOT_TTL_MS
        val expired = snapshots.values
            .filter { it.createdAtEpochMs < cutoff }
            .map(Snapshot::catalogVersion)
            .toSet()
        expired.forEach(snapshots::remove)
        cursors.entries.removeAll { it.value.catalogVersion in expired }
    }

    private fun validateMediaId(mediaId: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(mediaId)) {
            throw ApiException("validation_error", "ID media tidak valid.", 422)
        }
    }

    private fun Cursor.text(column: String): String? {
        val index = getColumnIndex(column)
        return if (index < 0 || isNull(index)) null else getString(index)
    }

    private fun Cursor.long(column: String): Long? {
        val index = getColumnIndex(column)
        return if (index < 0 || isNull(index)) null else getLong(index)
    }

    private fun Cursor.int(column: String): Int? {
        val index = getColumnIndex(column)
        return if (index < 0 || isNull(index)) null else getInt(index)
    }

    private fun Long.nonNegative(): Long? = takeIf { it >= 0 }

    private fun Int.positive(): Int? = takeIf { it > 0 }

    private data class Snapshot(
        val catalogVersion: String,
        val sessionId: String,
        val grantId: String,
        val items: List<CatalogItem>,
        val truncated: Boolean,
        val createdAtEpochMs: Long,
    )

    private data class CursorState(val catalogVersion: String, val offset: Int)

    private data class DirectoryNode(val id: String, val depth: Int, val directoryHint: String?)

    companion object {
        private const val MAX_PAGE_SIZE = 100
        private const val MAX_SNAPSHOTS = 4
        private const val MAX_CURSORS = 128
        private const val SNAPSHOT_TTL_MS = 5L * 60L * 1000L
        private const val MIN_THUMBNAIL_DIMENSION = 64
        private const val MAX_THUMBNAIL_DIMENSION = 512
        private const val MAX_DIRECTORY_DEPTH = 4
        private const val COPY_BUFFER_BYTES = 64 * 1024
        private val DOCUMENT_PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
    }
}
