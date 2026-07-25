package com.siksik.agent.preprocessing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.components.containers.Detection
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.facedetector.FaceDetector
import com.google.mediapipe.tasks.vision.imageembedder.ImageEmbedder
import com.google.mediapipe.tasks.vision.objectdetector.ObjectDetector
import com.siksik.agent.BuildConfig
import java.security.MessageDigest
import kotlin.math.ceil
import kotlin.math.floor

class MediaPipeFaceEmbeddingPreprocessor(
    context: Context,
    private val registry: ModelAssetRegistry = ModelAssetRegistry.from(context),
) : FaceEmbeddingPreprocessor {
    private val applicationContext = context.applicationContext
    private val initializationLock = Any()
    private val inferenceLock = Any()

    @Volatile
    private var runtime: FaceRuntime? = null

    @Volatile
    private var closed = false

    override fun capability(): EngineCapability = runtime().capability

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): FaceEmbeddingResult {
        val started = System.nanoTime()
        val active = runtime()
        if (active.capability.availability != EngineAvailability.AVAILABLE) {
            return result(
                active.capability.identity,
                started,
                ExecutionStatus.SKIPPED,
                emptyList(),
                listOf(active.capability.reason ?: "face_model_unavailable"),
            )
        }
        val loaded = BoundedBitmapLoader.load(input, cancellation)
        if (loaded.bitmap == null) {
            return result(
                active.capability.identity,
                started,
                loaded.status,
                emptyList(),
                listOf(loaded.reason),
            )
        }
        val bitmap = loaded.bitmap
        return try {
            if (cancellation.isCancelled()) {
                result(
                    active.capability.identity,
                    started,
                    ExecutionStatus.CANCELLED,
                    emptyList(),
                    listOf("cancelled"),
                )
            } else {
                inferFaces(active, bitmap, started, cancellation)
            }
        } catch (_: OutOfMemoryError) {
            result(
                active.capability.identity,
                started,
                ExecutionStatus.FAILED,
                emptyList(),
                listOf("face_resource_exhausted"),
            )
        } catch (_: RuntimeException) {
            result(
                active.capability.identity,
                started,
                ExecutionStatus.FAILED,
                emptyList(),
                listOf("face_inference_failed"),
            )
        } finally {
            if (!bitmap.isRecycled) bitmap.recycle()
        }
    }

    override fun close() {
        synchronized(initializationLock) {
            if (closed) return
            closed = true
            runtime?.close()
            runtime = null
        }
    }

    private fun runtime(): FaceRuntime = runtime ?: synchronized(initializationLock) {
        runtime ?: initialize().also { runtime = it }
    }

    private fun initialize(): FaceRuntime {
        val detectorDescriptor = registry.snapshot.models[FACE_DETECTOR_ID]
        val embedderDescriptor = registry.snapshot.models[FACE_EMBEDDER_ID]
        val identity = faceIdentity(detectorDescriptor, embedderDescriptor)
        if (closed) return FaceRuntime.error(identity, "engine_closed")
        val detectorCapability = registry.validate(FACE_DETECTOR_ID)
        if (detectorCapability.availability != EngineAvailability.AVAILABLE) {
            return FaceRuntime.error(identity, detectorCapability.reason ?: "face_detector_unavailable")
        }
        val embedderCapability = registry.validate(FACE_EMBEDDER_ID)
        if (embedderCapability.availability != EngineAvailability.AVAILABLE) {
            return FaceRuntime.error(identity, embedderCapability.reason ?: "face_embedder_unavailable")
        }
        val detectorModel = requireNotNull(detectorDescriptor)
        val embedderModel = requireNotNull(embedderDescriptor)
        if (!validFaceTensorContracts(detectorModel, embedderModel)) {
            return FaceRuntime.error(identity, "model_tensor_mismatch")
        }
        var detector: FaceDetector? = null
        var embedder: ImageEmbedder? = null
        return try {
            detector = FaceDetector.createFromOptions(
                applicationContext,
                FaceDetector.FaceDetectorOptions.builder()
                    .setBaseOptions(baseOptions(detectorModel))
                    .setMinDetectionConfidence(MIN_FACE_CONFIDENCE)
                    .setMinSuppressionThreshold(MIN_FACE_SUPPRESSION)
                    .build(),
            )
            embedder = ImageEmbedder.createFromOptions(
                applicationContext,
                ImageEmbedder.ImageEmbedderOptions.builder()
                    .setBaseOptions(baseOptions(embedderModel))
                    .setL2Normalize(true)
                    .setQuantize(false)
                    .build(),
            )
            validateFaceHealth(detector, embedder, embedderModel)
            FaceRuntime(
                EngineCapability(EngineAvailability.AVAILABLE, identity),
                detector,
                embedder,
                embedderModel.outputVectorSize,
            )
        } catch (_: RuntimeException) {
            detector?.close()
            embedder?.close()
            FaceRuntime.error(identity, "face_model_initialization_failed")
        }
    }

    private fun inferFaces(
        active: FaceRuntime,
        bitmap: Bitmap,
        started: Long,
        cancellation: CancellationToken,
    ): FaceEmbeddingResult = synchronized(inferenceLock) {
        val detector = requireNotNull(active.detector)
        val embedder = requireNotNull(active.embedder)
        val detections = BitmapImageBuilder(bitmap).build().use { image ->
            detector.detect(image).detections()
        }.sortedWith(
            compareByDescending<Detection>(::detectionConfidence)
                .thenBy { it.boundingBox().top }
                .thenBy { it.boundingBox().left },
        )
        val warnings = mutableListOf<String>()
        if (detections.size > BuildConfig.MAX_FACE_SIGNALS) {
            warnings.add("face_results_truncated")
        }
        val embeddings = mutableListOf<FaceEmbedding>()
        for (detection in detections.take(BuildConfig.MAX_FACE_SIGNALS)) {
            if (cancellation.isCancelled()) {
                return@synchronized result(
                    active.capability.identity,
                    started,
                    ExecutionStatus.CANCELLED,
                    emptyList(),
                    listOf("cancelled"),
                )
            }
            val bounds = boundedRect(detection.boundingBox(), bitmap.width, bitmap.height)
                ?: continue
            val crop = Bitmap.createBitmap(
                bitmap,
                bounds.left,
                bounds.top,
                bounds.right - bounds.left,
                bounds.bottom - bounds.top,
            )
            val vector = try {
                BitmapImageBuilder(crop).build().use { image ->
                    embeddingVector(embedder, image, active.embeddingSize)
                }
            } finally {
                if (!crop.isRecycled) crop.recycle()
            }
            embeddings.add(
                FaceEmbedding(
                    embeddings.size,
                    detectionConfidence(detection),
                    bounds.left,
                    bounds.top,
                    bounds.right,
                    bounds.bottom,
                    vector,
                ),
            )
        }
        result(
            active.capability.identity,
            started,
            if (warnings.isEmpty()) ExecutionStatus.COMPLETED else ExecutionStatus.TRUNCATED,
            embeddings,
            warnings,
        )
    }

    private fun validateFaceHealth(
        detector: FaceDetector,
        embedder: ImageEmbedder,
        descriptor: ModelDescriptor,
    ) {
        val fixture = healthFixture(descriptor.inputWidth, descriptor.inputHeight)
        try {
            BitmapImageBuilder(fixture).build().use { image ->
                detector.detect(image)
                embeddingVector(embedder, image, descriptor.outputVectorSize)
            }
        } finally {
            fixture.recycle()
        }
    }

    private fun result(
        identity: EngineIdentity,
        started: Long,
        status: ExecutionStatus,
        embeddings: List<FaceEmbedding>,
        warnings: List<String>,
    ) = FaceEmbeddingResult(
        ExecutionInfo(identity, status, elapsedMs(started), warnings),
        embeddings,
    )

    companion object {
        private const val FACE_DETECTOR_ID = "face_detector"
        private const val FACE_EMBEDDER_ID = "face_embedder"
        private const val MIN_FACE_CONFIDENCE = 0.5f
        private const val MIN_FACE_SUPPRESSION = 0.3f
    }
}

class MediaPipeObjectDetectionPreprocessor(
    context: Context,
    private val registry: ModelAssetRegistry = ModelAssetRegistry.from(context),
) : ObjectDetectionPreprocessor {
    private val applicationContext = context.applicationContext
    private val initializationLock = Any()
    private val inferenceLock = Any()

    @Volatile
    private var runtime: ObjectRuntime? = null

    @Volatile
    private var closed = false

    override fun capability(): EngineCapability = runtime().capability

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): ObjectDetectionResult {
        val started = System.nanoTime()
        val active = runtime()
        if (active.capability.availability != EngineAvailability.AVAILABLE) {
            return result(
                active.capability.identity,
                started,
                ExecutionStatus.SKIPPED,
                emptyList(),
                listOf(active.capability.reason ?: "object_model_unavailable"),
            )
        }
        val loaded = BoundedBitmapLoader.load(input, cancellation)
        if (loaded.bitmap == null) {
            return result(
                active.capability.identity,
                started,
                loaded.status,
                emptyList(),
                listOf(loaded.reason),
            )
        }
        val bitmap = loaded.bitmap
        return try {
            if (cancellation.isCancelled()) {
                result(
                    active.capability.identity,
                    started,
                    ExecutionStatus.CANCELLED,
                    emptyList(),
                    listOf("cancelled"),
                )
            } else {
                inferObjects(active, bitmap, started)
            }
        } catch (_: OutOfMemoryError) {
            result(
                active.capability.identity,
                started,
                ExecutionStatus.FAILED,
                emptyList(),
                listOf("object_resource_exhausted"),
            )
        } catch (_: RuntimeException) {
            result(
                active.capability.identity,
                started,
                ExecutionStatus.FAILED,
                emptyList(),
                listOf("object_inference_failed"),
            )
        } finally {
            if (!bitmap.isRecycled) bitmap.recycle()
        }
    }

    override fun close() {
        synchronized(initializationLock) {
            if (closed) return
            closed = true
            runtime?.close()
            runtime = null
        }
    }

    private fun runtime(): ObjectRuntime = runtime ?: synchronized(initializationLock) {
        runtime ?: initialize().also { runtime = it }
    }

    private fun initialize(): ObjectRuntime {
        val descriptor = registry.snapshot.models[OBJECT_DETECTOR_ID]
        val identity = descriptor?.identity(registry.snapshot.runtimeVersion)
            ?: EngineIdentity("MediaPipe object detector", registry.snapshot.runtimeVersion)
        if (closed) return ObjectRuntime.error(identity, "engine_closed")
        val capability = registry.validate(OBJECT_DETECTOR_ID)
        if (capability.availability != EngineAvailability.AVAILABLE) {
            return ObjectRuntime.error(identity, capability.reason ?: "object_model_unavailable")
        }
        val model = requireNotNull(descriptor)
        if (!validObjectTensorContract(model)) {
            return ObjectRuntime.error(identity, "model_tensor_mismatch")
        }
        var detector: ObjectDetector? = null
        return try {
            detector = ObjectDetector.createFromOptions(
                applicationContext,
                ObjectDetector.ObjectDetectorOptions.builder()
                    .setBaseOptions(baseOptions(model))
                    .setMaxResults(MAX_ENGINE_RESULTS)
                    .setScoreThreshold(MIN_OBJECT_CONFIDENCE)
                    .build(),
            )
            validateObjectHealth(detector, model)
            ObjectRuntime(
                EngineCapability(EngineAvailability.AVAILABLE, identity),
                detector,
            )
        } catch (_: RuntimeException) {
            detector?.close()
            ObjectRuntime.error(identity, "object_model_initialization_failed")
        }
    }

    private fun inferObjects(
        active: ObjectRuntime,
        bitmap: Bitmap,
        started: Long,
    ): ObjectDetectionResult = synchronized(inferenceLock) {
        val detections = BitmapImageBuilder(bitmap).build().use { image ->
            requireNotNull(active.detector).detect(image).detections()
        }
        val labels = detections.mapNotNull { detection ->
            val category = detection.categories().maxByOrNull { it.score() } ?: return@mapNotNull null
            val label = category.categoryName().ifBlank { category.displayName() }
                .trim()
                .take(MAX_LABEL_CHARACTERS)
            val bounds = boundedRect(detection.boundingBox(), bitmap.width, bitmap.height)
            if (label.isEmpty() || bounds == null || !category.score().isFinite()) {
                null
            } else {
                ObjectLabel(
                    label,
                    category.score().coerceIn(0f, 1f),
                    bounds.left,
                    bounds.top,
                    bounds.right,
                    bounds.bottom,
                )
            }
        }.sortedWith(
            compareByDescending<ObjectLabel>(ObjectLabel::confidence)
                .thenBy(ObjectLabel::label)
                .thenBy(ObjectLabel::top)
                .thenBy(ObjectLabel::left),
        )
        val warnings = if (labels.size > BuildConfig.MAX_OBJECT_LABELS) {
            listOf("object_results_truncated")
        } else {
            emptyList()
        }
        result(
            active.capability.identity,
            started,
            if (warnings.isEmpty()) ExecutionStatus.COMPLETED else ExecutionStatus.TRUNCATED,
            labels.take(BuildConfig.MAX_OBJECT_LABELS),
            warnings,
        )
    }

    private fun validateObjectHealth(detector: ObjectDetector, descriptor: ModelDescriptor) {
        val fixture = healthFixture(descriptor.inputWidth, descriptor.inputHeight)
        try {
            BitmapImageBuilder(fixture).build().use { image ->
                detector.detect(image).detections().forEach { detection ->
                    check(detection.boundingBox().isFinite())
                    check(detection.categories().all { it.score().isFinite() })
                }
            }
        } finally {
            fixture.recycle()
        }
    }

    private fun result(
        identity: EngineIdentity,
        started: Long,
        status: ExecutionStatus,
        labels: List<ObjectLabel>,
        warnings: List<String>,
    ) = ObjectDetectionResult(
        ExecutionInfo(identity, status, elapsedMs(started), warnings),
        labels,
    )

    companion object {
        private const val OBJECT_DETECTOR_ID = "object_detector"
        private const val MAX_ENGINE_RESULTS = 50
        private const val MAX_LABEL_CHARACTERS = 128
        private const val MIN_OBJECT_CONFIDENCE = 0.25f
    }
}

private data class FaceRuntime(
    val capability: EngineCapability,
    val detector: FaceDetector?,
    val embedder: ImageEmbedder?,
    val embeddingSize: Int,
) {
    fun close() {
        detector?.close()
        embedder?.close()
    }

    companion object {
        fun error(identity: EngineIdentity, reason: String) = FaceRuntime(
            EngineCapability(EngineAvailability.ERROR, identity, reason),
            null,
            null,
            0,
        )
    }
}

private data class ObjectRuntime(
    val capability: EngineCapability,
    val detector: ObjectDetector?,
) {
    fun close() {
        detector?.close()
    }

    companion object {
        fun error(identity: EngineIdentity, reason: String) = ObjectRuntime(
            EngineCapability(EngineAvailability.ERROR, identity, reason),
            null,
        )
    }
}

private data class BitmapLoadResult(
    val bitmap: Bitmap?,
    val status: ExecutionStatus,
    val reason: String,
)

private object BoundedBitmapLoader {
    fun load(input: PreprocessInput, cancellation: CancellationToken): BitmapLoadResult {
        if (cancellation.isCancelled()) {
            return BitmapLoadResult(null, ExecutionStatus.CANCELLED, "cancelled")
        }
        if (
            input.sizeBytes != null &&
            input.sizeBytes > BuildConfig.MAX_PREPROCESS_INPUT_BYTES
        ) {
            return BitmapLoadResult(null, ExecutionStatus.SKIPPED, "input_oversized")
        }
        if (input.width != null && input.height != null) {
            val pixels = input.width.toLong() * input.height.toLong()
            if (pixels <= 0) {
                return BitmapLoadResult(
                    null,
                    ExecutionStatus.SKIPPED,
                    "image_dimensions_unsupported",
                )
            }
        }
        val provider = input.bitmapProvider ?: return BitmapLoadResult(
            null,
            ExecutionStatus.SKIPPED,
            "visual_decoder_unavailable",
        )
        return try {
            val bitmap = provider.decode(BuildConfig.MAX_VISION_IMAGE_PIXELS)
                ?: return BitmapLoadResult(null, ExecutionStatus.FAILED, "image_decode_failed")
            val pixels = bitmap.width.toLong() * bitmap.height.toLong()
            if (
                bitmap.isRecycled ||
                pixels <= 0 ||
                pixels > BuildConfig.MAX_VISION_IMAGE_PIXELS
            ) {
                if (!bitmap.isRecycled) bitmap.recycle()
                BitmapLoadResult(
                    null,
                    ExecutionStatus.SKIPPED,
                    "image_dimensions_unsupported",
                )
            } else {
                BitmapLoadResult(bitmap, ExecutionStatus.COMPLETED, "")
            }
        } catch (_: IllegalArgumentException) {
            BitmapLoadResult(null, ExecutionStatus.FAILED, "image_decode_failed")
        } catch (_: SecurityException) {
            BitmapLoadResult(null, ExecutionStatus.FAILED, "input_access_denied")
        } catch (_: OutOfMemoryError) {
            BitmapLoadResult(null, ExecutionStatus.FAILED, "visual_resource_exhausted")
        }
    }
}

private fun baseOptions(descriptor: ModelDescriptor): BaseOptions = BaseOptions.builder()
    .setModelAssetPath(descriptor.asset)
    .build()

private fun validFaceTensorContracts(
    detector: ModelDescriptor,
    embedder: ModelDescriptor,
): Boolean =
    detector.inputWidth == 128 &&
        detector.inputHeight == 128 &&
        detector.inputChannels == 3 &&
        detector.outputVectorSize == 0 &&
        detector.outputContract == "face_detection_v1" &&
        embedder.inputWidth == 224 &&
        embedder.inputHeight == 224 &&
        embedder.inputChannels == 3 &&
        embedder.outputVectorSize == 1024 &&
        embedder.outputContract == "normalized_float_embedding_v1"

private fun validObjectTensorContract(descriptor: ModelDescriptor): Boolean =
    descriptor.inputWidth == 320 &&
        descriptor.inputHeight == 320 &&
        descriptor.inputChannels == 3 &&
        descriptor.outputVectorSize == 0 &&
        descriptor.outputContract == "coco_detection_v1"

private fun faceIdentity(
    detector: ModelDescriptor?,
    embedder: ModelDescriptor?,
): EngineIdentity {
    if (detector == null || embedder == null) {
        return EngineIdentity("MediaPipe face crop embedding", "0.10.35")
    }
    return EngineIdentity(
        "MediaPipe face crop embedding",
        "0.10.35:${detector.version}+${embedder.version}",
        "${detector.asset},${embedder.asset}",
        combinedHash(detector.sha256, embedder.sha256),
    )
}

private fun ModelDescriptor.identity(runtimeVersion: String) = EngineIdentity(
    name,
    "$runtimeVersion:$version",
    asset,
    sha256,
)

private fun combinedHash(vararg values: String): String {
    val digest = MessageDigest.getInstance("SHA-256")
    values.forEach { value -> digest.update(value.toByteArray(Charsets.UTF_8)) }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

private fun detectionConfidence(detection: Detection): Float =
    detection.categories().maxOfOrNull { it.score() }
        ?.takeIf(Float::isFinite)
        ?.coerceIn(0f, 1f)
        ?: 0f

private fun embeddingVector(
    embedder: ImageEmbedder,
    image: com.google.mediapipe.framework.image.MPImage,
    expectedSize: Int,
): FloatArray {
    val embeddings = embedder.embed(image).embeddingResult().embeddings()
    check(embeddings.size == 1) { "embedding_head_mismatch" }
    val vector = embeddings.single().floatEmbedding()
    check(vector.size == expectedSize && vector.all(Float::isFinite)) {
        "embedding_tensor_mismatch"
    }
    return vector.copyOf()
}

private fun boundedRect(value: RectF, width: Int, height: Int): android.graphics.Rect? {
    if (!value.isFinite() || width <= 0 || height <= 0) return null
    val left = floor(value.left.toDouble()).toInt().coerceIn(0, width - 1)
    val top = floor(value.top.toDouble()).toInt().coerceIn(0, height - 1)
    val right = ceil(value.right.toDouble()).toInt().coerceIn(left + 1, width)
    val bottom = ceil(value.bottom.toDouble()).toInt().coerceIn(top + 1, height)
    if (right <= left || bottom <= top) return null
    return android.graphics.Rect(left, top, right, bottom)
}

private fun RectF.isFinite(): Boolean =
    left.isFinite() && top.isFinite() && right.isFinite() && bottom.isFinite()

private fun healthFixture(width: Int, height: Int): Bitmap {
    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    val pixels = IntArray(width * height)
    for (y in 0 until height) {
        for (x in 0 until width) {
            val red = x * 255 / width.coerceAtLeast(1)
            val green = y * 255 / height.coerceAtLeast(1)
            val blue = (x + y) * 127 / (width + height).coerceAtLeast(1)
            pixels[y * width + x] =
                0xff000000.toInt() or (red shl 16) or (green shl 8) or blue
        }
    }
    bitmap.setPixels(pixels, 0, width, 0, 0, width, height)
    return bitmap
}
