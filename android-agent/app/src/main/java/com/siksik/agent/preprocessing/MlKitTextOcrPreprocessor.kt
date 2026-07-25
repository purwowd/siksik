package com.siksik.agent.preprocessing

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.siksik.agent.BuildConfig
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class MlKitTextOcrPreprocessor(
    private val recognizer: TextRecognizer = TextRecognition.getClient(
        TextRecognizerOptions.DEFAULT_OPTIONS,
    ),
    private val timeoutMs: Long = BuildConfig.PREPROCESS_ITEM_TIMEOUT_MS,
) : TextOcrPreprocessor {
    private val identity = EngineIdentity(
        "ML Kit Text Recognition Latin",
        "16.0.1-bundled",
    )

    @Volatile
    private var health: EngineCapability? = null

    override fun capability(): EngineCapability = health ?: synchronized(this) {
        health ?: runHealthCheck().also { health = it }
    }

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): TextOcrResult {
        val capability = capability()
        if (capability.availability != EngineAvailability.AVAILABLE) {
            return emptyResult(
                ExecutionStatus.SKIPPED,
                0,
                listOf(capability.reason ?: "ocr_unavailable"),
            )
        }
        if (cancellation.isCancelled()) {
            return emptyResult(ExecutionStatus.CANCELLED, 0, listOf("cancelled"))
        }
        if (
            input.sizeBytes != null &&
            input.sizeBytes > BuildConfig.MAX_PREPROCESS_INPUT_BYTES
        ) {
            return emptyResult(ExecutionStatus.SKIPPED, 0, listOf("input_oversized"))
        }
        val provider = input.bitmapProvider ?: return emptyResult(
            ExecutionStatus.SKIPPED,
            0,
            listOf("visual_decoder_unavailable"),
        )
        val started = System.nanoTime()
        val bitmap = try {
            provider.decode(BuildConfig.MAX_OCR_IMAGE_PIXELS)
        } catch (_: IllegalArgumentException) {
            null
        } catch (_: SecurityException) {
            return emptyResult(
                ExecutionStatus.FAILED,
                elapsedMs(started),
                listOf("input_access_denied"),
            )
        } ?: return emptyResult(
            ExecutionStatus.FAILED,
            elapsedMs(started),
            listOf("image_decode_failed"),
        )
        return try {
            if (
                bitmap.width.toLong() * bitmap.height.toLong() >
                BuildConfig.MAX_OCR_IMAGE_PIXELS
            ) {
                emptyResult(
                    ExecutionStatus.SKIPPED,
                    elapsedMs(started),
                    listOf("image_dimensions_unsupported"),
                )
            } else {
                recognize(bitmap, started, cancellation)
            }
        } finally {
            bitmap.recycle()
        }
    }

    override fun close() {
        recognizer.close()
    }

    private fun runHealthCheck(): EngineCapability {
        val bitmap = Bitmap.createBitmap(640, 160, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.WHITE)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.BLACK
            textSize = 72f
            typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
        }
        canvas.drawText(HEALTH_TEXT, 24f, 108f, paint)
        return try {
            val result = recognize(bitmap, System.nanoTime(), CancellationToken.NONE)
            if (
                result.execution.status == ExecutionStatus.COMPLETED &&
                result.normalizedText.uppercase().contains(HEALTH_TEXT)
            ) {
                EngineCapability(EngineAvailability.AVAILABLE, identity)
            } else {
                EngineCapability(EngineAvailability.ERROR, identity, "ocr_health_failed")
            }
        } catch (_: RuntimeException) {
            EngineCapability(EngineAvailability.ERROR, identity, "ocr_initialization_failed")
        } finally {
            bitmap.recycle()
        }
    }

    private fun recognize(
        bitmap: Bitmap,
        started: Long,
        cancellation: CancellationToken,
    ): TextOcrResult {
        if (cancellation.isCancelled()) {
            return emptyResult(ExecutionStatus.CANCELLED, elapsedMs(started), listOf("cancelled"))
        }
        return try {
            val text = Tasks.await(
                recognizer.process(InputImage.fromBitmap(bitmap, 0)),
                timeoutMs,
                TimeUnit.MILLISECONDS,
            )
            if (cancellation.isCancelled()) {
                return emptyResult(
                    ExecutionStatus.CANCELLED,
                    elapsedMs(started),
                    listOf("cancelled"),
                )
            }
            var regionsTruncated = false
            val regions = buildList {
                text.textBlocks.forEach { block ->
                    block.lines.forEach { line ->
                        line.elements.forEach { element ->
                            val bounds = element.boundingBox ?: return@forEach
                            val normalized = normalizeSearchText(element.text, MAX_REGION_TEXT)
                            if (normalized.isNotEmpty()) {
                                if (size >= BuildConfig.MAX_OCR_REGIONS) {
                                    regionsTruncated = true
                                } else {
                                    add(
                                        TextRegion(
                                            normalized,
                                            bounds.left,
                                            bounds.top,
                                            bounds.right,
                                            bounds.bottom,
                                            element.confidence,
                                        ),
                                    )
                                }
                            }
                        }
                    }
                }
            }
            val normalizedText = normalizeSearchTextBounded(
                text.text,
                BuildConfig.MAX_OCR_TEXT_CHARS,
            )
            val confidences = regions.mapNotNull(TextRegion::confidence)
            val warnings = buildList {
                if (normalizedText.truncated) add("ocr_text_truncated")
                if (regionsTruncated) add("ocr_regions_truncated")
            }
            TextOcrResult(
                ExecutionInfo(
                    identity,
                    if (warnings.isEmpty()) ExecutionStatus.COMPLETED else ExecutionStatus.TRUNCATED,
                    elapsedMs(started),
                    warnings,
                ),
                normalizedText.value,
                regions,
                confidences.takeIf(List<Float>::isNotEmpty)?.average()?.toFloat(),
            )
        } catch (_: TimeoutException) {
            emptyResult(ExecutionStatus.FAILED, elapsedMs(started), listOf("ocr_timeout"))
        } catch (_: ExecutionException) {
            emptyResult(ExecutionStatus.FAILED, elapsedMs(started), listOf("ocr_inference_failed"))
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            emptyResult(ExecutionStatus.CANCELLED, elapsedMs(started), listOf("cancelled"))
        }
    }

    private fun emptyResult(
        status: ExecutionStatus,
        durationMs: Long,
        warnings: List<String>,
    ) = TextOcrResult(
        ExecutionInfo(identity, status, durationMs, warnings),
        "",
        emptyList(),
        null,
    )

    companion object {
        private const val HEALTH_TEXT = "SIKSIK"
        private const val MAX_REGION_TEXT = 1024
    }
}

internal data class NormalizedSearchText(
    val value: String,
    val truncated: Boolean,
)

internal fun normalizeSearchTextBounded(
    value: String,
    maxCharacters: Int,
): NormalizedSearchText {
    require(maxCharacters > 0)
    val normalized = value
        .replace("\u0000", "")
        .replace("\r\n", "\n")
        .replace('\r', '\n')
        .lineSequence()
        .map { line -> line.trim().replace(Regex("[\\t ]+"), " ") }
        .filter(String::isNotEmpty)
        .joinToString("\n")
    return NormalizedSearchText(
        normalized.take(maxCharacters),
        normalized.length > maxCharacters,
    )
}

internal fun normalizeSearchText(value: String, maxCharacters: Int): String =
    normalizeSearchTextBounded(value, maxCharacters).value
