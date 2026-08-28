package com.siksik.agent.source.inventory

import android.Manifest
import android.content.ContentResolver
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import org.json.JSONObject

class MediaStoreInventorySource(
    context: Context,
    override val adapter: SourceAdapter,
    private val resolver: ContentResolver = context.contentResolver,
    private val exifReader: ExifMetadataReader = ExifMetadataReader(context),
    private val clock: () -> Long = System::currentTimeMillis,
) : InventorySource {
    private val appContext = context.applicationContext

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability {
        val state = when (adapter) {
            SourceAdapter.MEDIA_IMAGE -> mediaPermissionState(imagePermission())
            SourceAdapter.MEDIA_VIDEO -> mediaPermissionState(videoPermission())
            SourceAdapter.MEDIA_AUDIO -> permissionState(audioPermission())
            SourceAdapter.PUBLIC_WHATSAPP,
            SourceAdapter.PUBLIC_TELEGRAM,
            -> if (hasAnyMediaPermission()) {
                InventorySourceState.PENDING
            } else {
                InventorySourceState.DENIED
            }
            SourceAdapter.DOCUMENT_SHARED -> if (hasDocumentVisibility()) {
                InventorySourceState.PENDING
            } else {
                InventorySourceState.RESTRICTED
            }
            SourceAdapter.DOCUMENT_TREE,
            SourceAdapter.SMS,
            SourceAdapter.CONTACT,
            SourceAdapter.VISIBLE_UI,
            SourceAdapter.NOTIFICATION,
            -> InventorySourceState.UNSUPPORTED
        }
        return SourceAvailability(
            state,
            when (state) {
                InventorySourceState.DENIED -> "runtime_permission_not_granted"
                InventorySourceState.RESTRICTED -> "all_files_or_document_tree_required"
                InventorySourceState.UNSUPPORTED -> "source_adapter_mismatch"
                else -> null
            },
        )
    }

    override fun page(
        sessionId: String,
        documentGrantId: String?,
        checkpoint: String?,
        limit: Int,
        timeScope: InventoryTimeScope,
        isCancelled: () -> Boolean,
    ): AdapterPage {
        if (limit !in 1..BuildConfig.MAX_INVENTORY_PAGE_SIZE) {
            throw ApiException("validation_error", "Batas halaman inventory tidak valid.", 422)
        }
        val available = availability(sessionId, documentGrantId)
        if (available.state != InventorySourceState.PENDING) {
            return AdapterPage(emptyList(), null, 0, available.state, available.reason)
        }
        val lastId = decodeCheckpoint(checkpoint)
        val favoriteRecords = if (lastId == null) {
            loadFavoriteRecords(timeScope, isCancelled)
        } else {
            emptyList()
        }
        val favoriteIds = favoriteRecords.map(InventoryRecord::recordId).toSet()
        val timeLimit = (limit - favoriteRecords.size).coerceAtLeast(1)
        fun merged(page: List<InventoryRecord>): List<InventoryRecord> =
            (favoriteRecords + page).distinctBy(InventoryRecord::recordId)
        fun pageOut(
            page: List<InventoryRecord>,
            nextCheckpoint: String?,
            scannedCount: Int,
            terminalState: InventorySourceState,
            terminalReason: String?,
        ): AdapterPage = AdapterPage(
            merged(page),
            nextCheckpoint,
            scannedCount + favoriteRecords.size,
            terminalState,
            terminalReason,
        )
        val spec = querySpec(lastId, timeLimit, timeScope)
        val signal = CancellationSignal()
        val records = mutableListOf<InventoryRecord>()
        var scanned = 0
        var lastScannedId: Long? = null
        try {
            resolver.query(spec.uri, spec.projection, spec.arguments, signal)?.use { cursor ->
                while (cursor.moveToNext()) {
                    if (isCancelled()) {
                        return pageOut(
                            records,
                            lastScannedId?.let(::encodeCheckpoint),
                            scanned,
                            InventorySourceState.CANCELLED,
                            "crawl_cancelled",
                        )
                    }
                    scanned += 1
                    val id = cursor.long(MediaStore.MediaColumns._ID) ?: continue
                    lastScannedId = id
                    mapRecord(cursor, spec)?.let { record ->
                        if (record.recordId in favoriteIds) return@let
                        if (timeScope.includes(record)) records.add(record)
                    }
                }
            } ?: return pageOut(
                emptyList(),
                null,
                0,
                InventorySourceState.PARTIAL,
                "media_provider_unavailable",
            )
        } catch (_: SecurityException) {
            return if (favoriteRecords.isNotEmpty()) {
                pageOut(
                    emptyList(),
                    checkpoint,
                    scanned,
                    InventorySourceState.PARTIAL,
                    "runtime_permission_revoked",
                )
            } else {
                AdapterPage(
                    emptyList(),
                    checkpoint,
                    scanned,
                    InventorySourceState.DENIED,
                    "runtime_permission_revoked",
                )
            }
        } catch (_: IllegalArgumentException) {
            return pageOut(
                emptyList(),
                checkpoint,
                scanned,
                InventorySourceState.PARTIAL,
                "media_provider_query_rejected",
            )
        }
        val mergedRecords = merged(records)
        val favoriteCount = mergedRecords.count(InventoryRecord::isFavorite)
        if (favoriteCount > 0) {
            Log.i(
                LOG_TAG,
                "event=inventory_favorites adapter=${adapter.wireName} count=$favoriteCount",
            )
        }
        val hasMore = scanned >= timeLimit
        val terminal = if (
            !hasMore && adapter == SourceAdapter.DOCUMENT_SHARED && !hasFullDocumentVisibility()
        ) {
            InventorySourceState.PARTIAL
        } else if (!hasMore && !hasCompletePermissionScope()) {
            InventorySourceState.PARTIAL
        } else {
            InventorySourceState.COMPLETE
        }
        return AdapterPage(
            records = mergedRecords,
            nextCheckpoint = lastScannedId?.let(::encodeCheckpoint).takeIf { hasMore },
            scannedCount = scanned + favoriteRecords.size,
            terminalState = terminal,
            terminalReason = when {
                terminal != InventorySourceState.PARTIAL -> null
                adapter == SourceAdapter.DOCUMENT_SHARED ->
                    "document_visibility_limited_by_scoped_storage"
                else -> "media_permission_scope_partial"
            },
        )
    }

    private fun querySpec(
        lastId: Long?,
        limit: Int,
        timeScope: InventoryTimeScope,
    ): QuerySpec {
        val clauses = mutableListOf<String>()
        val selectionArgs = mutableListOf<String>()
        if (lastId != null) {
            clauses.add("${MediaStore.MediaColumns._ID} < ?")
            selectionArgs.add(lastId.toString())
        }
        addTimeFilter(clauses, selectionArgs, timeScope)
        when (adapter) {
            SourceAdapter.PUBLIC_WHATSAPP -> addPublicPathFilter(
                clauses,
                selectionArgs,
                InventoryPolicy.publicSqlPatterns(SourceAdapter.PUBLIC_WHATSAPP),
            )
            SourceAdapter.PUBLIC_TELEGRAM -> addPublicPathFilter(
                clauses,
                selectionArgs,
                InventoryPolicy.publicSqlPatterns(SourceAdapter.PUBLIC_TELEGRAM),
            )
            SourceAdapter.DOCUMENT_SHARED -> addDocumentFilter(clauses, selectionArgs)
            else -> Unit
        }
        val bundle = Bundle().apply {
            putString(
                ContentResolver.QUERY_ARG_SQL_SELECTION,
                clauses.joinToString(" AND ").takeIf(String::isNotBlank),
            )
            putStringArray(
                ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS,
                selectionArgs.toTypedArray(),
            )
            putString(
                ContentResolver.QUERY_ARG_SQL_SORT_ORDER,
                "${MediaStore.MediaColumns._ID} DESC",
            )
            putInt(ContentResolver.QUERY_ARG_LIMIT, limit)
        }
        return QuerySpec(collectionUri(), projection(), bundle)
    }

    /** Starred Camera-roll items are older than the 3/6-month cut; a dedicated query
     *  avoids (time OR IS_FAVORITE) + LIMIT ranking them behind newer in-window rows. */
    private fun loadFavoriteRecords(
        timeScope: InventoryTimeScope,
        isCancelled: () -> Boolean,
    ): List<InventoryRecord> {
        if (Build.VERSION.SDK_INT < 30) return emptyList()
        if (
            adapter !in setOf(
                SourceAdapter.MEDIA_IMAGE,
                SourceAdapter.MEDIA_VIDEO,
            )
        ) {
            return emptyList()
        }
        val out = mutableListOf<InventoryRecord>()
        for (spec in favoriteQuerySpecs()) {
            if (isCancelled()) break
            try {
                resolver.query(spec.uri, spec.projection, spec.arguments, CancellationSignal())
                    ?.use { cursor ->
                        while (cursor.moveToNext()) {
                            if (isCancelled()) break
                            mapRecord(cursor, spec)?.let { record ->
                                if (timeScope.includes(record)) out.add(record)
                            }
                        }
                    }
            } catch (_: SecurityException) {
                continue
            } catch (_: IllegalArgumentException) {
                continue
            }
            // MATCH_ONLY is the platform-supported query. The SQL form below
            // is retained solely as a compatibility fallback for OEM providers.
            if (out.isNotEmpty()) break
        }
        return out.distinctBy(InventoryRecord::recordId)
    }

    private fun favoriteQuerySpecs(): List<QuerySpec> {
        fun common(bundle: Bundle): Bundle = bundle.apply {
            putString(
                ContentResolver.QUERY_ARG_SQL_SORT_ORDER,
                "${MediaStore.MediaColumns._ID} DESC",
            )
            putInt(ContentResolver.QUERY_ARG_LIMIT, BuildConfig.MAX_INVENTORY_PAGE_SIZE)
        }
        val matchOnly = common(Bundle().apply {
            putInt(MediaStore.QUERY_ARG_MATCH_FAVORITE, MediaStore.MATCH_ONLY)
        })
        val sqlFallback = common(Bundle().apply {
            putString(
                ContentResolver.QUERY_ARG_SQL_SELECTION,
                "${MediaStore.MediaColumns.IS_FAVORITE} = ?",
            )
            putStringArray(
                ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS,
                arrayOf("1"),
            )
        })
        return listOf(matchOnly, sqlFallback).map { bundle ->
            QuerySpec(collectionUri(), projection(), bundle)
        }
    }

    private fun collectionUri(): Uri = when (adapter) {
        SourceAdapter.MEDIA_IMAGE -> MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        SourceAdapter.MEDIA_VIDEO -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        SourceAdapter.MEDIA_AUDIO -> MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        SourceAdapter.DOCUMENT_SHARED,
        SourceAdapter.PUBLIC_WHATSAPP,
        SourceAdapter.PUBLIC_TELEGRAM,
        -> MediaStore.Files.getContentUri("external")
        SourceAdapter.DOCUMENT_TREE,
        SourceAdapter.SMS,
        SourceAdapter.CONTACT,
        SourceAdapter.VISIBLE_UI,
        SourceAdapter.NOTIFICATION,
        -> throw IllegalStateException("invalid MediaStore adapter")
    }

    private fun addTimeFilter(
        clauses: MutableList<String>,
        arguments: MutableList<String>,
        timeScope: InventoryTimeScope,
    ) {
        if (!timeScope.isBounded) return
        val dateAdded = MediaStore.MediaColumns.DATE_ADDED
        val dateModified = MediaStore.MediaColumns.DATE_MODIFIED
        val seconds = Math.floorDiv(timeScope.notBeforeEpochMs, 1000L).toString()
        val supportsDateTaken = adapter in setOf(
            SourceAdapter.MEDIA_IMAGE,
            SourceAdapter.MEDIA_VIDEO,
            SourceAdapter.PUBLIC_WHATSAPP,
            SourceAdapter.PUBLIC_TELEGRAM,
        )
        val timeClause = if (supportsDateTaken) {
            val dateTaken = MediaStore.Images.ImageColumns.DATE_TAKEN
            arguments.add(timeScope.notBeforeEpochMs.toString())
            arguments.add(seconds)
            arguments.add(seconds)
            "($dateTaken >= ? OR $dateAdded >= ? OR $dateModified >= ? OR " +
                "(($dateTaken IS NULL OR $dateTaken <= 0) AND " +
                "($dateAdded IS NULL OR $dateAdded <= 0) AND " +
                "($dateModified IS NULL OR $dateModified <= 0)))"
        } else {
            arguments.add(seconds)
            arguments.add(seconds)
            "($dateAdded >= ? OR $dateModified >= ? OR " +
                "(($dateAdded IS NULL OR $dateAdded <= 0) AND " +
                "($dateModified IS NULL OR $dateModified <= 0)))"
        }
        val favoriteClause = favoriteBypassClause(arguments)
        if (favoriteClause == null) {
            clauses.add(timeClause)
        } else {
            clauses.add("($timeClause OR $favoriteClause)")
        }
    }

    private fun favoriteBypassClause(arguments: MutableList<String>): String? {
        if (
            adapter !in setOf(
                SourceAdapter.MEDIA_IMAGE,
                SourceAdapter.MEDIA_VIDEO,
                SourceAdapter.PUBLIC_WHATSAPP,
                SourceAdapter.PUBLIC_TELEGRAM,
            )
        ) {
            return null
        }
        val parts = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 30) {
            parts.add("${MediaStore.MediaColumns.IS_FAVORITE} = 1")
        }
        val pathColumn = if (Build.VERSION.SDK_INT >= 29) {
            MediaStore.MediaColumns.RELATIVE_PATH
        } else {
            MediaStore.MediaColumns.DATA
        }
        val nameColumn = MediaStore.MediaColumns.DISPLAY_NAME
        InventoryPolicy.favoriteSqlLikePatterns.forEach { pattern ->
            parts.add("LOWER($pathColumn) LIKE ?")
            arguments.add(pattern)
            parts.add("LOWER($nameColumn) LIKE ?")
            arguments.add(pattern)
        }
        if (parts.isEmpty()) return null
        return parts.joinToString(" OR ", prefix = "(", postfix = ")")
    }

    private fun projection(): Array<String> = buildList {
        add(MediaStore.MediaColumns._ID)
        add(MediaStore.MediaColumns.DISPLAY_NAME)
        add(MediaStore.MediaColumns.MIME_TYPE)
        add(MediaStore.MediaColumns.SIZE)
        add(MediaStore.MediaColumns.DATE_ADDED)
        add(MediaStore.MediaColumns.DATE_MODIFIED)
        if (Build.VERSION.SDK_INT >= 29) add(MediaStore.MediaColumns.RELATIVE_PATH)
        if (Build.VERSION.SDK_INT < 29) add(MediaStore.MediaColumns.DATA)
        if (adapter != SourceAdapter.MEDIA_AUDIO && adapter != SourceAdapter.DOCUMENT_SHARED) {
            add(MediaStore.MediaColumns.WIDTH)
            add(MediaStore.MediaColumns.HEIGHT)
        }
        if (
            adapter in setOf(
                SourceAdapter.MEDIA_IMAGE,
                SourceAdapter.MEDIA_VIDEO,
                SourceAdapter.PUBLIC_WHATSAPP,
                SourceAdapter.PUBLIC_TELEGRAM,
            )
        ) {
            add(MediaStore.Images.ImageColumns.DATE_TAKEN)
        }
        if (Build.VERSION.SDK_INT >= 30) {
            add(MediaStore.MediaColumns.IS_FAVORITE)
            add(MediaStore.MediaColumns.OWNER_PACKAGE_NAME)
        }
        if (adapter != SourceAdapter.MEDIA_IMAGE && adapter != SourceAdapter.DOCUMENT_SHARED) {
            add(MediaStore.Video.VideoColumns.DURATION)
        }
    }.distinct().toTypedArray()

    private fun mapRecord(cursor: Cursor, spec: QuerySpec): InventoryRecord? {
        val id = cursor.long(MediaStore.MediaColumns._ID) ?: return null
        val name = InventoryPolicy.safeDisplayName(cursor.text(MediaStore.MediaColumns.DISPLAY_NAME))
        val mime = InventoryPolicy.normalizedMime(
            cursor.text(MediaStore.MediaColumns.MIME_TYPE),
            name,
        ) ?: return null
        val kind = InventoryPolicy.sourceKind(mime, name) ?: return null
        if (!acceptsKind(kind)) return null
        val identity = "external:$id"
        val identityHash = InventoryPolicy.identityHash(identity)
        val recordId = InventoryPolicy.recordId(identityHash)
        val uri = ContentUrisCompat.withAppendedId(spec.uri, id)
        val dateTaken = cursor.long(MediaStore.Images.ImageColumns.DATE_TAKEN)?.positive()
        val dateAdded = cursor.long(MediaStore.MediaColumns.DATE_ADDED)?.secondsToMillis()
        val dateModified = cursor.long(MediaStore.MediaColumns.DATE_MODIFIED)?.secondsToMillis()
        val exif = if (kind == InventorySourceKind.MEDIA_IMAGE) exifReader.read(uri, mime) else null
        val directoryHint = directoryHint(cursor)
        val ownerPackageName = if (Build.VERSION.SDK_INT >= 30) {
            cursor.text(MediaStore.MediaColumns.OWNER_PACKAGE_NAME)
        } else {
            null
        }
        val flaggedFavorite = if (Build.VERSION.SDK_INT >= 30) {
            cursor.int(MediaStore.MediaColumns.IS_FAVORITE)?.let { it != 0 } ?: false
        } else {
            false
        }
        val isFavorite = flaggedFavorite ||
            InventoryPolicy.looksFavorite(directoryHint, name)
        val sizeBytes = cursor.long(MediaStore.MediaColumns.SIZE)?.nonNegative()
        val captureCandidates = listOf(
            exif?.capturedAtEpochMs to "exif_original",
            dateTaken to "date_taken",
            dateAdded to "date_added",
            dateModified to "date_modified",
        )
        val capture = captureCandidates.firstOrNull { it.first != null }
        return InventoryRecord(
            recordId = recordId,
            identityHash = identityHash,
            dedupeHash = InventoryPolicy.overlapDedupeHash(
                identityHash,
                name,
                mime,
                sizeBytes,
                dateModified,
                directoryHint,
            ),
            sourceKind = kind,
            sourceAdapter = adapter,
            sourceApp = InventoryPolicy.sourceApp(adapter, ownerPackageName),
            sourceLocator = InventoryPolicy.sourceLocator(adapter, identityHash),
            displayName = name,
            mimeType = mime,
            sizeBytes = sizeBytes,
            width = cursor.int(MediaStore.MediaColumns.WIDTH)?.positive(),
            height = cursor.int(MediaStore.MediaColumns.HEIGHT)?.positive(),
            durationMs = cursor.long(MediaStore.Video.VideoColumns.DURATION)?.nonNegative(),
            dateTakenEpochMs = dateTaken,
            dateAddedEpochMs = dateAdded,
            dateModifiedEpochMs = dateModified,
            captureTimeEpochMs = capture?.first,
            captureTimeSource = capture?.second ?: "unknown",
            directoryHint = directoryHint,
            isFavorite = isFavorite,
            exif = exif,
            warningCodes = emptyList(),
            thumbnailAvailable = kind in setOf(
                InventorySourceKind.MEDIA_IMAGE,
                InventorySourceKind.MEDIA_VIDEO,
            ),
            observedAtEpochMs = clock(),
            contentUri = uri,
        )
    }

    private fun acceptsKind(kind: InventorySourceKind): Boolean = when (adapter) {
        SourceAdapter.MEDIA_IMAGE -> kind == InventorySourceKind.MEDIA_IMAGE
        SourceAdapter.MEDIA_VIDEO -> kind == InventorySourceKind.MEDIA_VIDEO
        SourceAdapter.MEDIA_AUDIO -> kind == InventorySourceKind.MEDIA_AUDIO
        SourceAdapter.DOCUMENT_SHARED -> kind == InventorySourceKind.DOCUMENT
        SourceAdapter.PUBLIC_WHATSAPP,
        SourceAdapter.PUBLIC_TELEGRAM,
        -> kind != InventorySourceKind.DOCUMENT
        SourceAdapter.DOCUMENT_TREE,
        SourceAdapter.SMS,
        SourceAdapter.CONTACT,
        SourceAdapter.VISIBLE_UI,
        SourceAdapter.NOTIFICATION,
        -> false
    }

    private fun addPublicPathFilter(
        clauses: MutableList<String>,
        arguments: MutableList<String>,
        paths: List<String>,
    ) {
        val column = if (Build.VERSION.SDK_INT >= 29) {
            MediaStore.MediaColumns.RELATIVE_PATH
        } else {
            MediaStore.MediaColumns.DATA
        }
        clauses.add("(" + paths.joinToString(" OR ") { "LOWER($column) LIKE ?" } + ")")
        arguments.addAll(paths)
        clauses.add(
            "${MediaStore.Files.FileColumns.MEDIA_TYPE} IN (?, ?, ?)",
        )
        arguments.addAll(
            listOf(
                MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE.toString(),
                MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO.toString(),
                MediaStore.Files.FileColumns.MEDIA_TYPE_AUDIO.toString(),
            ),
        )
    }

    private fun addDocumentFilter(
        clauses: MutableList<String>,
        arguments: MutableList<String>,
    ) {
        val extensionClauses = InventoryPolicy.documentExtensions.sorted().map {
            "LOWER(${MediaStore.MediaColumns.DISPLAY_NAME}) LIKE ?"
        }
        clauses.add("(" + extensionClauses.joinToString(" OR ") + ")")
        arguments.addAll(InventoryPolicy.documentExtensions.sorted().map { "%.$it" })
    }

    private fun directoryHint(cursor: Cursor): String? {
        if (Build.VERSION.SDK_INT >= 29) {
            return InventoryPolicy.normalizedDirectoryHint(
                cursor.text(MediaStore.MediaColumns.RELATIVE_PATH),
            )
        }
        val path = cursor.text(MediaStore.MediaColumns.DATA) ?: return null
        val relative = path.substringAfter("/storage/emulated/0/", "")
        return InventoryPolicy.normalizedDirectoryHint(relative.substringBeforeLast('/', ""))
    }

    private fun permissionState(permission: String): InventorySourceState =
        if (appContext.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) {
            InventorySourceState.PENDING
        } else {
            InventorySourceState.DENIED
        }

    private fun mediaPermissionState(permission: String): InventorySourceState = when {
        appContext.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED ->
            InventorySourceState.PENDING
        hasSelectedVisualPermission() -> InventorySourceState.PENDING
        else -> InventorySourceState.DENIED
    }

    private fun hasAnyMediaPermission(): Boolean = listOf(
        imagePermission(),
        videoPermission(),
        audioPermission(),
    ).any { appContext.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED } ||
        hasSelectedVisualPermission()

    private fun hasAllMediaPermissions(): Boolean = listOf(
        imagePermission(),
        videoPermission(),
        audioPermission(),
    ).all { appContext.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    private fun hasCompletePermissionScope(): Boolean = when (adapter) {
        SourceAdapter.MEDIA_IMAGE ->
            appContext.checkSelfPermission(imagePermission()) == PackageManager.PERMISSION_GRANTED
        SourceAdapter.MEDIA_VIDEO ->
            appContext.checkSelfPermission(videoPermission()) == PackageManager.PERMISSION_GRANTED
        SourceAdapter.MEDIA_AUDIO ->
            appContext.checkSelfPermission(audioPermission()) == PackageManager.PERMISSION_GRANTED
        SourceAdapter.PUBLIC_WHATSAPP,
        SourceAdapter.PUBLIC_TELEGRAM,
        -> hasAllMediaPermissions()
        else -> true
    }

    private fun hasSelectedVisualPermission(): Boolean =
        Build.VERSION.SDK_INT >= 34 &&
            appContext.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED) ==
            PackageManager.PERMISSION_GRANTED

    private fun hasDocumentVisibility(): Boolean = when {
        Build.VERSION.SDK_INT >= 30 -> Environment.isExternalStorageManager()
        else -> appContext.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun hasFullDocumentVisibility(): Boolean = when {
        Build.VERSION.SDK_INT >= 30 -> Environment.isExternalStorageManager()
        Build.VERSION.SDK_INT == 29 -> false
        else -> true
    }

    private fun imagePermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_IMAGES
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun videoPermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_VIDEO
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun audioPermission(): String = if (Build.VERSION.SDK_INT >= 33) {
        Manifest.permission.READ_MEDIA_AUDIO
    } else {
        Manifest.permission.READ_EXTERNAL_STORAGE
    }

    private fun decodeCheckpoint(value: String?): Long? {
        if (value == null) return null
        return try {
            JSONObject(value).getLong("last_id").takeIf { it > 0 }
                ?: throw ApiException("invalid_cursor", "Checkpoint inventory tidak valid.", 422)
        } catch (_: org.json.JSONException) {
            throw ApiException("invalid_cursor", "Checkpoint inventory tidak valid.", 422)
        }
    }

    private fun encodeCheckpoint(lastId: Long): String = JSONObject()
        .put("last_id", lastId)
        .toString()

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

    private fun Long.positive(): Long? = takeIf { it > 0 }

    private fun Long.secondsToMillis(): Long? =
        takeIf { it in 1..Long.MAX_VALUE / 1000 }?.times(1000)

    private fun Int.positive(): Int? = takeIf { it > 0 }

    private data class QuerySpec(
        val uri: Uri,
        val projection: Array<String>,
        val arguments: Bundle,
    )

    companion object {
        private const val LOG_TAG = "SIKSIKInventory"
    }
}

private object ContentUrisCompat {
    fun withAppendedId(uri: Uri, id: Long): Uri = Uri.withAppendedPath(uri, id.toString())
}
