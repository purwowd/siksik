package com.siksik.agent.source.inventory

import android.content.ContentResolver
import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.DocumentsContract
import androidx.core.net.toUri
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.util.ArrayDeque

class DocumentTreeInventorySource(
    context: Context,
    private val grants: GrantGateway,
    private val resolver: ContentResolver = context.contentResolver,
    private val exifReader: ExifMetadataReader = ExifMetadataReader(context),
    private val binaryReader: BinaryMetadataReader = BinaryMetadataReader(resolver),
    private val clock: () -> Long = System::currentTimeMillis,
) : InventorySource {
    override val adapter = SourceAdapter.DOCUMENT_TREE
    private val appContext = context.applicationContext

    override fun availability(sessionId: String, documentGrantId: String?): SourceAvailability {
        if (documentGrantId == null) {
            if (hasFullSharedDocumentVisibility()) {
                return SourceAvailability(
                    InventorySourceState.COMPLETE,
                    "document_tree_not_required",
                )
            }
            return SourceAvailability(
                InventorySourceState.RESTRICTED,
                "document_tree_grant_required",
            )
        }
        return try {
            val uris = grants.grantedUris(sessionId, documentGrantId)
            if (uris.isEmpty()) {
                SourceAvailability(InventorySourceState.RESTRICTED, "document_tree_empty")
            } else {
                SourceAvailability(InventorySourceState.PENDING)
            }
        } catch (exception: ApiException) {
            val state = if (exception.code == "grant_revoked") {
                InventorySourceState.DENIED
            } else {
                InventorySourceState.RESTRICTED
            }
            SourceAvailability(state, exception.code)
        }
    }

    private fun hasFullSharedDocumentVisibility(): Boolean = when {
        Build.VERSION.SDK_INT >= 30 -> Environment.isExternalStorageManager()
        Build.VERSION.SDK_INT == 29 -> false
        else -> appContext.checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
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
        if (available.state != InventorySourceState.PENDING || documentGrantId == null) {
            return AdapterPage(emptyList(), null, 0, available.state, available.reason)
        }
        val state = if (checkpoint == null) {
            initialState(grants.grantedUris(sessionId, documentGrantId))
        } else {
            decodeCheckpoint(checkpoint)
        }
        val records = mutableListOf<InventoryRecord>()
        var scanned = 0
        var stopForPage = false
        var activeNode: TreeNode? = null
        try {
            while (state.queue.isNotEmpty() && !stopForPage) {
                if (isCancelled()) {
                    return AdapterPage(
                        records,
                        encodeCheckpoint(state),
                        scanned,
                        InventorySourceState.CANCELLED,
                        "crawl_cancelled",
                    )
                }
                val node = state.queue.removeFirst()
                activeNode = node
                val children = DocumentsContract.buildChildDocumentsUriUsingTree(
                    node.treeUri,
                    node.documentId,
                )
                val cursor = resolver.query(
                    children,
                    PROJECTION,
                    null,
                    null,
                    DOCUMENT_SORT_ORDER,
                )
                if (cursor == null) {
                    state.queue.addFirst(node)
                    activeNode = null
                    return AdapterPage(
                        records,
                        encodeCheckpoint(state),
                        scanned,
                        InventorySourceState.PARTIAL,
                        "document_provider_unavailable",
                    )
                }
                cursor.use {
                    if (node.offset > 0) {
                        if (isCancelled()) {
                            state.queue.addFirst(node)
                            activeNode = null
                            return AdapterPage(
                                records,
                                encodeCheckpoint(state),
                                scanned,
                                InventorySourceState.CANCELLED,
                                "crawl_cancelled",
                            )
                        }
                        cursor.moveToPosition(node.offset - 1)
                    }
                    while (!stopForPage && cursor.moveToNext()) {
                        if (isCancelled()) {
                            state.queue.addFirst(node.copy(offset = cursor.position))
                            activeNode = null
                            return AdapterPage(
                                records,
                                encodeCheckpoint(state),
                                scanned,
                                InventorySourceState.CANCELLED,
                                "crawl_cancelled",
                            )
                        }
                        scanned += 1
                        if (scanned >= limit) {
                            if (!cursor.isLast) {
                                state.queue.addFirst(node.copy(offset = cursor.position + 1))
                            }
                            activeNode = null
                            stopForPage = true
                        }
                        val documentId = cursor.text(
                            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                        ) ?: continue
                        if (documentId.length > MAX_DOCUMENT_ID_LENGTH) {
                            state.partialReason = "document_id_limit"
                            continue
                        }
                        val name = InventoryPolicy.safeDisplayName(
                            cursor.text(DocumentsContract.Document.COLUMN_DISPLAY_NAME),
                        )
                        val rawMime = cursor.text(DocumentsContract.Document.COLUMN_MIME_TYPE)
                            ?: continue
                        if (rawMime == DocumentsContract.Document.MIME_TYPE_DIR) {
                            enqueueDirectory(state, node, documentId, name)
                            continue
                        }
                        val mime = InventoryPolicy.normalizedMime(rawMime, name) ?: continue
                        val kind = InventoryPolicy.sourceKind(mime, name) ?: continue
                        val modified = cursor.long(
                            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
                        )?.takeIf { it > 0 }
                        if (
                            kind != InventorySourceKind.MEDIA_IMAGE &&
                            !timeScope.includesTimestamp(modified)
                        ) {
                            continue
                        }
                        val uri = DocumentsContract.buildDocumentUriUsingTree(
                            node.treeUri,
                            documentId,
                        )
                        val record = mapRecord(
                            node,
                            cursor,
                            documentId,
                            uri,
                            name,
                            mime,
                            kind,
                            modified,
                        )
                        if (timeScope.includes(record)) records.add(record)
                    }
                }
                activeNode = null
            }
        } catch (_: SecurityException) {
            activeNode?.let(state.queue::addFirst)
            return AdapterPage(
                records,
                encodeCheckpoint(state),
                scanned,
                InventorySourceState.DENIED,
                "document_tree_grant_revoked",
            )
        } catch (_: IllegalArgumentException) {
            activeNode?.let(state.queue::addFirst)
            return AdapterPage(
                records,
                encodeCheckpoint(state),
                scanned,
                InventorySourceState.PARTIAL,
                "document_provider_disappeared",
            )
        } catch (_: IllegalStateException) {
            activeNode?.let(state.queue::addFirst)
            return AdapterPage(
                records,
                encodeCheckpoint(state),
                scanned,
                InventorySourceState.PARTIAL,
                "document_provider_disappeared",
            )
        }
        val next = encodeCheckpoint(state).takeIf { state.queue.isNotEmpty() }
        val terminal = if (next == null && state.partialReason != null) {
            InventorySourceState.PARTIAL
        } else {
            InventorySourceState.COMPLETE
        }
        return AdapterPage(
            records,
            next,
            scanned,
            terminal,
            state.partialReason,
        )
    }

    private fun initialState(trees: List<Uri>): TreeState {
        val queue = ArrayDeque<TreeNode>()
        var partialReason: String? = null
        trees.sortedBy(Uri::toString).take(MAX_TREE_ROOTS).forEach { tree ->
            try {
                val rootId = DocumentsContract.getTreeDocumentId(tree)
                if (rootId.length <= MAX_DOCUMENT_ID_LENGTH) {
                    queue.add(
                        TreeNode(
                            tree,
                            rootId,
                            rootDirectoryHint(tree, rootId),
                            0,
                            0,
                        ),
                    )
                } else {
                    partialReason = "document_id_limit"
                }
            } catch (_: IllegalArgumentException) {
                partialReason = "document_tree_invalid"
            }
        }
        if (trees.size > MAX_TREE_ROOTS) partialReason = "document_tree_root_limit"
        return TreeState(queue, partialReason)
    }

    private fun enqueueDirectory(
        state: TreeState,
        parent: TreeNode,
        documentId: String,
        name: String,
    ) {
        if (parent.depth >= MAX_TREE_DEPTH) {
            state.partialReason = "document_tree_depth_limit"
            return
        }
        if (state.queue.size >= BuildConfig.MAX_DOCUMENT_TREE_QUEUE) {
            state.partialReason = "document_tree_queue_limit"
            return
        }
        state.queue.add(
            TreeNode(
                parent.treeUri,
                documentId,
                InventoryPolicy.joinedDirectoryHint(parent.directoryHint, name),
                parent.depth + 1,
                0,
            ),
        )
    }

    private fun mapRecord(
        node: TreeNode,
        cursor: Cursor,
        documentId: String,
        uri: Uri,
        name: String,
        mime: String,
        kind: InventorySourceKind,
        modified: Long?,
    ): InventoryRecord {
        val identityHash = InventoryPolicy.identityHash(
            "tree:${node.treeUri.authority}:$documentId",
        )
        val exif = if (kind == InventorySourceKind.MEDIA_IMAGE) exifReader.read(uri, mime) else null
        val binary = binaryReader.read(uri, kind)
        val capture = exif?.capturedAtEpochMs ?: modified
        val warningCodes = listOfNotNull(binary.warningCode)
        val mergedExif = exif?.copy(warningCodes = (exif.warningCodes + warningCodes).distinct())
        val sizeBytes = cursor.long(DocumentsContract.Document.COLUMN_SIZE)?.takeIf { it >= 0 }
        val dedupeHash = if (node.treeUri.authority == EXTERNAL_STORAGE_AUTHORITY) {
            InventoryPolicy.overlapDedupeHash(
                identityHash,
                name,
                mime,
                sizeBytes,
                modified,
                node.directoryHint,
            )
        } else {
            identityHash
        }
        return InventoryRecord(
            recordId = InventoryPolicy.recordId(identityHash),
            identityHash = identityHash,
            dedupeHash = dedupeHash,
            sourceKind = kind,
            sourceAdapter = adapter,
            sourceApp = null,
            sourceLocator = InventoryPolicy.sourceLocator(adapter, identityHash),
            displayName = name,
            mimeType = mime,
            sizeBytes = sizeBytes,
            width = binary.width,
            height = binary.height,
            durationMs = binary.durationMs,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = modified,
            captureTimeEpochMs = capture,
            captureTimeSource = when {
                exif?.capturedAtEpochMs != null -> "exif_original"
                modified != null -> "date_modified"
                else -> "unknown"
            },
            directoryHint = node.directoryHint,
            exif = mergedExif,
            warningCodes = warningCodes,
            thumbnailAvailable = cursor.int(DocumentsContract.Document.COLUMN_FLAGS)?.let {
                it and DocumentsContract.Document.FLAG_SUPPORTS_THUMBNAIL != 0
            } ?: false,
            observedAtEpochMs = clock(),
            contentUri = uri,
        )
    }

    private fun encodeCheckpoint(state: TreeState): String = JSONObject()
        .put("partial_reason", state.partialReason)
        .put(
            "queue",
            JSONArray().also { array ->
                state.queue.forEach { node ->
                    array.put(
                        JSONObject()
                            .put("tree", node.treeUri.toString())
                            .put("document_id", node.documentId)
                            .put("directory_hint", node.directoryHint)
                            .put("depth", node.depth)
                            .put("offset", node.offset),
                    )
                }
            },
        )
        .toString()

    private fun rootDirectoryHint(tree: Uri, documentId: String): String? {
        if (tree.authority != EXTERNAL_STORAGE_AUTHORITY) return null
        return InventoryPolicy.normalizedDirectoryHint(documentId.substringAfter(':', ""))
    }

    private fun decodeCheckpoint(value: String): TreeState {
        try {
            val payload = JSONObject(value)
            val items = payload.getJSONArray("queue")
            if (items.length() > BuildConfig.MAX_DOCUMENT_TREE_QUEUE) {
                throw ApiException("invalid_cursor", "Checkpoint tree melewati batas.", 422)
            }
            val queue = ArrayDeque<TreeNode>()
            for (index in 0 until items.length()) {
                val item = items.getJSONObject(index)
                val documentId = item.getString("document_id")
                val depth = item.getInt("depth")
                val offset = item.getInt("offset")
                if (
                    documentId.length > MAX_DOCUMENT_ID_LENGTH ||
                    depth !in 0..MAX_TREE_DEPTH ||
                    offset !in 0..MAX_DIRECTORY_CHILDREN
                ) {
                    throw ApiException("invalid_cursor", "Checkpoint tree tidak valid.", 422)
                }
                val tree = item.getString("tree").toUri()
                if (
                    tree.scheme != ContentResolver.SCHEME_CONTENT ||
                    tree.authority.isNullOrBlank() ||
                    !DocumentsContract.isTreeUri(tree)
                ) {
                    throw ApiException("invalid_cursor", "Checkpoint tree tidak valid.", 422)
                }
                queue.add(
                    TreeNode(
                        tree,
                        documentId,
                        InventoryPolicy.normalizedDirectoryHint(
                            item.optString("directory_hint").takeIf(String::isNotBlank),
                        ),
                        depth,
                        offset,
                    ),
                )
            }
            return TreeState(
                queue,
                payload.optString("partial_reason").takeIf(String::isNotBlank),
            )
        } catch (exception: ApiException) {
            throw exception
        } catch (_: JSONException) {
            throw ApiException("invalid_cursor", "Checkpoint tree tidak valid.", 422)
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

    private data class TreeState(
        val queue: ArrayDeque<TreeNode>,
        var partialReason: String?,
    )

    private data class TreeNode(
        val treeUri: Uri,
        val documentId: String,
        val directoryHint: String?,
        val depth: Int,
        val offset: Int,
    )

    companion object {
        private const val MAX_TREE_ROOTS = 8
        private const val MAX_TREE_DEPTH = 32
        private const val MAX_DOCUMENT_ID_LENGTH = 2048
        private const val MAX_DIRECTORY_CHILDREN = 1_000_000
        private const val EXTERNAL_STORAGE_AUTHORITY = "com.android.externalstorage.documents"
        private const val DOCUMENT_SORT_ORDER =
            "${DocumentsContract.Document.COLUMN_DOCUMENT_ID} ASC"
        private val PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            DocumentsContract.Document.COLUMN_FLAGS,
        )
    }
}
