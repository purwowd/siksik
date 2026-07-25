package com.siksik.agent.preprocessing

import android.graphics.Bitmap
import android.os.ParcelFileDescriptor
import java.io.InputStream

enum class EngineAvailability(val wireName: String) {
    AVAILABLE("available"),
    UNAVAILABLE("unavailable"),
    ERROR("error"),
}

enum class ExecutionStatus(val wireName: String) {
    COMPLETED("completed"),
    SKIPPED("skipped"),
    TRUNCATED("truncated"),
    FAILED("failed"),
    CANCELLED("cancelled"),
}

enum class DocumentState(val wireName: String) {
    EXTRACTED("extracted"),
    BLANK("blank"),
    ENCRYPTED("encrypted"),
    CORRUPT("corrupt"),
    UNSUPPORTED_FEATURE("unsupported_feature"),
    OVERSIZED("oversized"),
    TRUNCATED("truncated"),
}

data class EngineIdentity(
    val name: String,
    val version: String,
    val modelAsset: String? = null,
    val modelSha256: String? = null,
)

data class EngineCapability(
    val availability: EngineAvailability,
    val identity: EngineIdentity,
    val reason: String? = null,
)

data class ExecutionInfo(
    val engine: EngineIdentity,
    val status: ExecutionStatus,
    val durationMs: Long,
    val warnings: List<String> = emptyList(),
)

fun interface CancellationToken {
    fun isCancelled(): Boolean

    companion object {
        val NONE = CancellationToken { false }
    }
}

fun interface BitmapProvider {
    fun decode(maxPixels: Long): Bitmap?
}

data class PreprocessInput(
    val recordId: String,
    val mimeType: String,
    val sizeBytes: Long?,
    val width: Int?,
    val height: Int?,
    val streamProvider: () -> InputStream,
    val bitmapProvider: BitmapProvider? = null,
    val fileDescriptorProvider: (() -> ParcelFileDescriptor)? = null,
)

data class TextRegion(
    val text: String,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val confidence: Float?,
)

data class TextOcrResult(
    val execution: ExecutionInfo,
    val normalizedText: String,
    val regions: List<TextRegion>,
    val meanConfidence: Float?,
)

data class DocumentTextResult(
    val execution: ExecutionInfo,
    val state: DocumentState,
    val normalizedText: String,
    val extractedCharacters: Int,
)

data class ExactHashResult(
    val execution: ExecutionInfo,
    val sha256: String?,
    val bytesRead: Long,
)

data class PerceptualHashResult(
    val execution: ExecutionInfo,
    val algorithm: String,
    val hash: String?,
)

data class FaceEmbedding(
    val faceIndex: Int,
    val confidence: Float,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val vector: FloatArray,
)

data class FaceEmbeddingResult(
    val execution: ExecutionInfo,
    val embeddings: List<FaceEmbedding>,
)

data class ObjectLabel(
    val label: String,
    val confidence: Float,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
)

data class ObjectDetectionResult(
    val execution: ExecutionInfo,
    val labels: List<ObjectLabel>,
)

data class DuplicateMembership(
    val recordId: String,
    val exactGroupId: String?,
    val perceptualGroupId: String?,
    val representativeRecordId: String?,
)

data class FaceClusterMembership(
    val recordId: String,
    val clusterIds: List<String>,
)

data class RecordPreprocessResult(
    val recordId: String,
    val ocr: TextOcrResult? = null,
    val documentText: DocumentTextResult? = null,
    val exactHash: ExactHashResult? = null,
    val perceptualHash: PerceptualHashResult? = null,
    val faceEmbedding: FaceEmbeddingResult? = null,
    val objectDetection: ObjectDetectionResult? = null,
    val duplicateMembership: DuplicateMembership? = null,
    val faceClusters: FaceClusterMembership? = null,
    val warnings: List<String> = emptyList(),
)

interface TextOcrPreprocessor : AutoCloseable {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): TextOcrResult
}

interface DocumentTextPreprocessor {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): DocumentTextResult
}

interface ExactHashPreprocessor {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): ExactHashResult
}

interface PerceptualHashPreprocessor {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): PerceptualHashResult
}

interface FaceEmbeddingPreprocessor : AutoCloseable {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): FaceEmbeddingResult
}

interface ObjectDetectionPreprocessor : AutoCloseable {
    fun capability(): EngineCapability

    fun process(
        input: PreprocessInput,
        cancellation: CancellationToken = CancellationToken.NONE,
    ): ObjectDetectionResult
}
