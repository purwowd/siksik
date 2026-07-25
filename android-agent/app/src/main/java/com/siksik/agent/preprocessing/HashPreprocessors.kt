package com.siksik.agent.preprocessing

import android.graphics.Bitmap
import com.siksik.agent.BuildConfig
import java.io.IOException
import java.security.MessageDigest

class StreamingExactHashPreprocessor(
    private val maxBytes: Long = BuildConfig.MAX_STAGE_FILE_BYTES,
) : ExactHashPreprocessor {
    private val identity = EngineIdentity("SHA-256", "FIPS-180-4")

    override fun capability() = EngineCapability(EngineAvailability.AVAILABLE, identity)

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): ExactHashResult {
        val started = System.nanoTime()
        if (input.sizeBytes != null && input.sizeBytes > maxBytes) {
            return result(
                started,
                ExecutionStatus.SKIPPED,
                null,
                0,
                listOf("input_oversized"),
            )
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        return try {
            input.streamProvider().use { stream ->
                val buffer = ByteArray(BUFFER_SIZE)
                while (true) {
                    if (cancellation.isCancelled()) {
                        return result(
                            started,
                            ExecutionStatus.CANCELLED,
                            null,
                            total,
                            listOf("cancelled"),
                        )
                    }
                    val read = stream.read(buffer)
                    if (read < 0) break
                    if (read == 0) continue
                    total += read
                    if (total > maxBytes) {
                        return result(
                            started,
                            ExecutionStatus.TRUNCATED,
                            null,
                            total,
                            listOf("input_oversized"),
                        )
                    }
                    digest.update(buffer, 0, read)
                }
            }
            result(
                started,
                ExecutionStatus.COMPLETED,
                digest.digest().joinToString("") { "%02x".format(it) },
                total,
                emptyList(),
            )
        } catch (_: IOException) {
            result(started, ExecutionStatus.FAILED, null, total, listOf("input_read_failed"))
        } catch (_: SecurityException) {
            result(started, ExecutionStatus.FAILED, null, total, listOf("input_access_denied"))
        }
    }

    private fun result(
        started: Long,
        status: ExecutionStatus,
        hash: String?,
        bytesRead: Long,
        warnings: List<String>,
    ) = ExactHashResult(
        ExecutionInfo(identity, status, elapsedMs(started), warnings),
        hash,
        bytesRead,
    )

    companion object {
        private const val BUFFER_SIZE = 256 * 1024
    }
}

class DifferenceHashPreprocessor(
    private val maxPixels: Long = BuildConfig.MAX_PERCEPTUAL_HASH_PIXELS,
) : PerceptualHashPreprocessor {
    private val identity = EngineIdentity("difference-hash", "64-bit-v1")

    override fun capability() = EngineCapability(EngineAvailability.AVAILABLE, identity)

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): PerceptualHashResult {
        val started = System.nanoTime()
        if (cancellation.isCancelled()) {
            return result(started, ExecutionStatus.CANCELLED, null, listOf("cancelled"))
        }
        if (input.width != null && input.height != null) {
            val pixels = input.width.toLong() * input.height.toLong()
            if (pixels <= 0) {
                return result(
                    started,
                    ExecutionStatus.SKIPPED,
                    null,
                    listOf("image_dimensions_unsupported"),
                )
            }
        }
        val provider = input.bitmapProvider ?: return result(
            started,
            ExecutionStatus.SKIPPED,
            null,
            listOf("visual_decoder_unavailable"),
        )
        return try {
            val bitmap = provider.decode(maxPixels) ?: return result(
                started,
                ExecutionStatus.FAILED,
                null,
                listOf("image_decode_failed"),
            )
            bitmap.useOwned {
                if (it.width.toLong() * it.height.toLong() > maxPixels) {
                    return result(
                        started,
                        ExecutionStatus.SKIPPED,
                        null,
                        listOf("image_dimensions_unsupported"),
                    )
                }
                val scaled = Bitmap.createScaledBitmap(it, SAMPLE_WIDTH, SAMPLE_HEIGHT, true)
                try {
                    val pixels = IntArray(SAMPLE_WIDTH * SAMPLE_HEIGHT)
                    scaled.getPixels(pixels, 0, SAMPLE_WIDTH, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT)
                    result(
                        started,
                        ExecutionStatus.COMPLETED,
                        DifferenceHash.fromArgb(pixels),
                        emptyList(),
                    )
                } finally {
                    if (scaled !== it) scaled.recycle()
                }
            }
        } catch (_: IllegalArgumentException) {
            result(started, ExecutionStatus.FAILED, null, listOf("image_decode_failed"))
        } catch (_: SecurityException) {
            result(started, ExecutionStatus.FAILED, null, listOf("input_access_denied"))
        }
    }

    private fun result(
        started: Long,
        status: ExecutionStatus,
        hash: String?,
        warnings: List<String>,
    ) = PerceptualHashResult(
        ExecutionInfo(identity, status, elapsedMs(started), warnings),
        "dhash-64",
        hash,
    )

    companion object {
        private const val SAMPLE_WIDTH = 9
        private const val SAMPLE_HEIGHT = 8
    }
}

object DifferenceHash {
    fun fromArgb(pixels: IntArray): String {
        require(pixels.size == 72)
        var hash = 0uL
        var bit = 0
        for (row in 0 until 8) {
            for (column in 0 until 8) {
                val left = luminance(pixels[row * 9 + column])
                val right = luminance(pixels[row * 9 + column + 1])
                if (left > right) hash = hash or (1uL shl bit)
                bit += 1
            }
        }
        return hash.toString(16).padStart(16, '0')
    }

    fun hammingDistance(left: String, right: String): Int {
        require(HEX64.matches(left) && HEX64.matches(right))
        return (left.toULong(16) xor right.toULong(16)).countOneBits()
    }

    private fun luminance(color: Int): Int {
        val red = color shr 16 and 0xff
        val green = color shr 8 and 0xff
        val blue = color and 0xff
        return (red * 299 + green * 587 + blue * 114) / 1000
    }

    private val HEX64 = Regex("^[0-9a-f]{16}$")
}

private inline fun <T> Bitmap.useOwned(block: (Bitmap) -> T): T = try {
    block(this)
} finally {
    recycle()
}

internal fun elapsedMs(startedNanos: Long): Long =
    ((System.nanoTime() - startedNanos) / 1_000_000L).coerceAtLeast(0L)
