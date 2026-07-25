package com.siksik.agent.preprocessing

import android.content.ContentResolver
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.ParcelFileDescriptor
import com.siksik.agent.source.communication.CommunicationCaptureStore
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileNotFoundException
import java.io.InputStream
import kotlin.math.sqrt

class AndroidPreprocessInputFactory(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext
    private val resolver: ContentResolver = appContext.contentResolver
    private val communicationStore = CommunicationCaptureStore(appContext)

    fun create(record: StoredPreprocessRecord): PreprocessInput {
        val uri = record.contentUri?.let(::validatedContentUri)
        val scopedScreenshot = if (record.sourceKind == "visible_ui") {
            record.attachmentIds.firstNotNullOfOrNull { attachmentId ->
                communicationStore.screenshotForTransfer(
                    record.sessionId,
                    record.crawlId,
                    attachmentId,
                )
            }
        } else {
            null
        }
        val fallback = record.normalizedText.orEmpty().toByteArray(Charsets.UTF_8)
        return PreprocessInput(
            recordId = record.recordId,
            mimeType = record.mimeType,
            sizeBytes = scopedScreenshot?.length() ?: record.sizeBytes,
            width = record.width,
            height = record.height,
            streamProvider = when {
                uri != null -> ({ openStream(uri) })
                scopedScreenshot != null -> ({ FileInputStream(scopedScreenshot) })
                else -> ({ ByteArrayInputStream(fallback) })
            },
            bitmapProvider = when {
                uri != null && record.sourceKind == "media_image" -> BitmapProvider { maxPixels ->
                    decodeImage(uri, maxPixels)
                }
                uri != null && record.sourceKind == "media_video" -> BitmapProvider { maxPixels ->
                    decodeVideoFrame(uri, maxPixels)
                }
                scopedScreenshot != null -> BitmapProvider { maxPixels ->
                    decodeImage(scopedScreenshot, maxPixels)
                }
                else -> null
            },
            fileDescriptorProvider = if (uri != null && record.mimeType == "application/pdf") {
                { openDescriptor(uri) }
            } else {
                null
            },
        )
    }

    override fun close() {
        communicationStore.close()
    }

    private fun openStream(uri: Uri): InputStream =
        resolver.openInputStream(uri) ?: throw FileNotFoundException("content_unavailable")

    private fun openDescriptor(uri: Uri): ParcelFileDescriptor =
        resolver.openFileDescriptor(uri, "r") ?: throw FileNotFoundException("content_unavailable")

    private fun decodeImage(uri: Uri, maxPixels: Long): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        openStream(uri).use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight, maxPixels)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        return openStream(uri).use { BitmapFactory.decodeStream(it, null, options) }
            ?.bounded(maxPixels)
    }

    private fun sampleSize(width: Int, height: Int, maxPixels: Long): Int {
        var value = 1
        while (
            (width / value).toLong() * (height / value).toLong() > maxPixels &&
            value <= MAX_BITMAP_SAMPLE_SIZE
        ) {
            value *= 2
        }
        return value
    }

    private fun decodeImage(file: File, maxPixels: Long): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        FileInputStream(file).use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val sampleSize = sampleSize(bounds.outWidth, bounds.outHeight, maxPixels)
        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        return FileInputStream(file).use { BitmapFactory.decodeStream(it, null, options) }
            ?.bounded(maxPixels)
    }

    private fun decodeVideoFrame(uri: Uri, maxPixels: Long): Bitmap? {
        val retriever = MediaMetadataRetriever()
        return try {
            val descriptor = resolver.openAssetFileDescriptor(uri, "r") ?: return null
            descriptor.use {
                if (it.length >= 0) {
                    retriever.setDataSource(it.fileDescriptor, it.startOffset, it.length)
                } else {
                    retriever.setDataSource(it.fileDescriptor)
                }
            }
            retriever.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                ?.bounded(maxPixels)
        } finally {
            retriever.release()
        }
    }

    private fun Bitmap.bounded(maxPixels: Long): Bitmap {
        val pixels = width.toLong() * height.toLong()
        if (pixels <= maxPixels) return this
        val scale = sqrt(maxPixels.toDouble() / pixels.toDouble())
        val scaled = Bitmap.createScaledBitmap(
            this,
            (width * scale).toInt().coerceAtLeast(1),
            (height * scale).toInt().coerceAtLeast(1),
            true,
        )
        if (scaled !== this) recycle()
        return scaled
    }

    private fun validatedContentUri(value: String): Uri {
        val uri = Uri.parse(value)
        require(uri.scheme == ContentResolver.SCHEME_CONTENT && uri.authority?.isNotBlank() == true)
        return uri
    }

    companion object {
        private const val MAX_BITMAP_SAMPLE_SIZE = 128
    }
}
