package com.siksik.agent.source.inventory

import android.content.ContentResolver
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import java.io.IOException

data class BinaryMetadata(
    val width: Int?,
    val height: Int?,
    val durationMs: Long?,
    val warningCode: String?,
)

class BinaryMetadataReader(private val resolver: ContentResolver) {
    fun read(uri: Uri, kind: InventorySourceKind): BinaryMetadata {
        if (kind == InventorySourceKind.DOCUMENT) {
            return BinaryMetadata(null, null, null, null)
        }
        if (kind == InventorySourceKind.MEDIA_IMAGE) {
            return readImageBounds(uri)
        }
        val retriever = MediaMetadataRetriever()
        return try {
            val descriptor = resolver.openFileDescriptor(uri, "r")
                ?: return BinaryMetadata(null, null, null, "metadata_stream_unavailable")
            descriptor.use {
                retriever.setDataSource(it.fileDescriptor)
                BinaryMetadata(
                    width = retriever
                        .extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)
                        ?.toIntOrNull()?.takeIf { value -> value > 0 },
                    height = retriever
                        .extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)
                        ?.toIntOrNull()?.takeIf { value -> value > 0 },
                    durationMs = retriever
                        .extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                        ?.toLongOrNull()?.takeIf { value -> value >= 0 },
                    warningCode = null,
                )
            }
        } catch (_: SecurityException) {
            BinaryMetadata(null, null, null, "metadata_permission_revoked")
        } catch (_: IOException) {
            BinaryMetadata(null, null, null, "metadata_malformed")
        } catch (_: IllegalArgumentException) {
            BinaryMetadata(null, null, null, "metadata_malformed")
        } catch (_: IllegalStateException) {
            BinaryMetadata(null, null, null, "metadata_malformed")
        } finally {
            retriever.release()
        }
    }

    private fun readImageBounds(uri: Uri): BinaryMetadata {
        return try {
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            val opened = resolver.openInputStream(uri)
                ?: return BinaryMetadata(null, null, null, "metadata_stream_unavailable")
            opened.use { BitmapFactory.decodeStream(it, null, options) }
            if (options.outWidth > 0 && options.outHeight > 0) {
                BinaryMetadata(options.outWidth, options.outHeight, null, null)
            } else {
                BinaryMetadata(null, null, null, "metadata_malformed")
            }
        } catch (_: SecurityException) {
            BinaryMetadata(null, null, null, "metadata_permission_revoked")
        } catch (_: IOException) {
            BinaryMetadata(null, null, null, "metadata_malformed")
        } catch (_: IllegalArgumentException) {
            BinaryMetadata(null, null, null, "metadata_malformed")
        }
    }
}
