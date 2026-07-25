package com.siksik.agent

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.os.ParcelFileDescriptor
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.preprocessing.BitmapProvider
import com.siksik.agent.preprocessing.BoundedDocumentTextPreprocessor
import com.siksik.agent.preprocessing.CancellationToken
import com.siksik.agent.preprocessing.DocumentState
import com.siksik.agent.preprocessing.EngineAvailability
import com.siksik.agent.preprocessing.ExecutionStatus
import com.siksik.agent.preprocessing.MediaPipeFaceEmbeddingPreprocessor
import com.siksik.agent.preprocessing.MediaPipeObjectDetectionPreprocessor
import com.siksik.agent.preprocessing.MlKitTextOcrPreprocessor
import com.siksik.agent.preprocessing.ModelAssetRegistry
import com.siksik.agent.preprocessing.ModelAssetSource
import com.siksik.agent.preprocessing.PreprocessInput
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileOutputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PreprocessingEngineTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    @Test
    fun realModelAssetsAndTensorContractsInitialize() {
        val registry = ModelAssetRegistry.from(context)
        assertEquals(
            setOf("face_detector", "face_embedder", "object_detector"),
            registry.snapshot.models.keys,
        )
        registry.snapshot.models.keys.forEach { id ->
            assertEquals(EngineAvailability.AVAILABLE, registry.validate(id).availability)
        }

        MediaPipeFaceEmbeddingPreprocessor(context, registry).use { face ->
            assertEquals(EngineAvailability.AVAILABLE, face.capability().availability)
            val result = face.process(visualInput("face-health"), CancellationToken.NONE)
            assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        }
        MediaPipeObjectDetectionPreprocessor(context, registry).use { objects ->
            assertEquals(EngineAvailability.AVAILABLE, objects.capability().availability)
            val result = objects.process(visualInput("object-health"), CancellationToken.NONE)
            assertTrue(
                result.execution.status in
                    setOf(ExecutionStatus.COMPLETED, ExecutionStatus.TRUNCATED),
            )
        }
    }

    @Test
    fun tensorRegistryMismatchDoesNotClaimCapability() {
        val registryBytes = context.assets.open("models/registry.json").use { it.readBytes() }
        val altered = registryBytes.toString(Charsets.UTF_8)
            .replaceFirst("\"input_width\": 320", "\"input_width\": 321")
            .toByteArray()
        val assets = context.assets
        val registry = ModelAssetRegistry.from(ModelAssetSource(assets::open), altered)
        MediaPipeObjectDetectionPreprocessor(context, registry).use { objects ->
            val capability = objects.capability()
            assertEquals(EngineAvailability.ERROR, capability.availability)
            assertEquals("model_tensor_mismatch", capability.reason)
        }
    }

    @Test
    fun bundledOcrRunsRealInferenceAndHonorsBounds() {
        MlKitTextOcrPreprocessor().use { ocr ->
            assertEquals(EngineAvailability.AVAILABLE, ocr.capability().availability)
            val result = ocr.process(textImageInput("SIKSIK OCR"), CancellationToken.NONE)
            assertTrue(
                result.execution.status in
                    setOf(ExecutionStatus.COMPLETED, ExecutionStatus.TRUNCATED),
            )
            assertTrue(result.normalizedText.uppercase().contains("SIKSIK"))
            assertTrue(result.regions.isNotEmpty())

            val blank = ocr.process(
                visualInput("blank") {
                    Bitmap.createBitmap(320, 320, Bitmap.Config.ARGB_8888).apply {
                        eraseColor(Color.WHITE)
                    }
                },
                CancellationToken.NONE,
            )
            assertEquals(ExecutionStatus.COMPLETED, blank.execution.status)
            assertTrue(blank.normalizedText.isEmpty())
            assertTrue(blank.regions.isEmpty())

            val corrupt = ocr.process(
                visualInput("corrupt") { null },
                CancellationToken.NONE,
            )
            assertEquals(ExecutionStatus.FAILED, corrupt.execution.status)
            assertEquals(listOf("image_decode_failed"), corrupt.execution.warnings)

            val oversized = ocr.process(
                visualInput("oversized", declaredSize = BuildConfig.MAX_PREPROCESS_INPUT_BYTES + 1),
                CancellationToken.NONE,
            )
            assertEquals(ExecutionStatus.SKIPPED, oversized.execution.status)
            assertEquals(listOf("input_oversized"), oversized.execution.warnings)
        }
    }

    @Test
    fun pdfRendererFeedsBundledOcr() {
        val pdf = File(context.cacheDir, "phase06-${System.nanoTime()}.pdf")
        writePdfFixture(pdf, "SIKSIK PDF")
        try {
            MlKitTextOcrPreprocessor().use { ocr ->
                val documents = BoundedDocumentTextPreprocessor(ocr)
                val result = documents.process(
                    PreprocessInput(
                        recordId = "pdf-health",
                        mimeType = "application/pdf",
                        sizeBytes = pdf.length(),
                        width = null,
                        height = null,
                        streamProvider = { pdf.inputStream() },
                        fileDescriptorProvider = {
                            ParcelFileDescriptor.open(pdf, ParcelFileDescriptor.MODE_READ_ONLY)
                        },
                    ),
                    CancellationToken.NONE,
                )
                assertTrue(
                    result.execution.status in
                        setOf(ExecutionStatus.COMPLETED, ExecutionStatus.TRUNCATED),
                )
                assertTrue(result.state in setOf(DocumentState.EXTRACTED, DocumentState.TRUNCATED))
                assertTrue(result.normalizedText.uppercase().contains("SIKSIK"))
            }
        } finally {
            pdf.delete()
        }
    }

    private fun visualInput(
        recordId: String,
        declaredSize: Long = 4096,
        bitmap: () -> Bitmap? = { gradientBitmap(320, 320) },
    ) = PreprocessInput(
        recordId = recordId,
        mimeType = "image/png",
        sizeBytes = declaredSize,
        width = 320,
        height = 320,
        streamProvider = { ByteArrayInputStream(ByteArray(0)) },
        bitmapProvider = BitmapProvider { bitmap() },
    )

    private fun textImageInput(value: String) = PreprocessInput(
        recordId = "ocr-health",
        mimeType = "image/png",
        sizeBytes = 4096,
        width = 800,
        height = 240,
        streamProvider = { ByteArrayInputStream(ByteArray(0)) },
        bitmapProvider = BitmapProvider { textBitmap(800, 240, value) },
    )

    private fun textBitmap(width: Int, height: Int, value: String): Bitmap {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply {
            drawColor(Color.WHITE)
            drawText(
                value,
                24f,
                150f,
                Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = Color.BLACK
                    textSize = 92f
                    typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
                },
            )
        }
        return bitmap
    }

    private fun gradientBitmap(width: Int, height: Int): Bitmap {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val pixels = IntArray(width * height)
        for (y in 0 until height) {
            for (x in 0 until width) {
                val red = x * 255 / width
                val green = y * 255 / height
                val blue = (x + y) * 127 / (width + height)
                pixels[y * width + x] =
                    0xff000000.toInt() or (red shl 16) or (green shl 8) or blue
            }
        }
        bitmap.setPixels(pixels, 0, width, 0, 0, width, height)
        return bitmap
    }

    private fun writePdfFixture(file: File, value: String) {
        val document = PdfDocument()
        try {
            val page = document.startPage(
                PdfDocument.PageInfo.Builder(800, 400, 1).create(),
            )
            page.canvas.apply {
                drawColor(Color.WHITE)
                drawText(
                    value,
                    40f,
                    220f,
                    Paint(Paint.ANTI_ALIAS_FLAG).apply {
                        color = Color.BLACK
                        textSize = 110f
                        typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
                    },
                )
            }
            document.finishPage(page)
            FileOutputStream(file).use(document::writeTo)
        } finally {
            document.close()
        }
    }
}
