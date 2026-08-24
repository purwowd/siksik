package com.siksik.agent.preprocessing

import android.graphics.Bitmap
import com.siksik.agent.BuildConfig
import kotlin.math.sqrt
import org.json.JSONArray
import org.json.JSONObject

interface PreprocessingProcessor : AutoCloseable {
    fun capabilities(): Map<String, EngineCapability>

    fun process(
        record: StoredPreprocessRecord,
        cancellation: CancellationToken,
    ): PreprocessingRecordUpdate
}

class RecordPreprocessor(
    private val inputFactory: AndroidPreprocessInputFactory,
    private val ocr: TextOcrPreprocessor,
    private val documents: DocumentTextPreprocessor,
    private val exactHash: ExactHashPreprocessor,
    private val perceptualHash: PerceptualHashPreprocessor,
    private val faces: FaceEmbeddingPreprocessor,
    private val objects: ObjectDetectionPreprocessor,
) : PreprocessingProcessor {
    private val visualResultCache = BoundedResultCache<String, VisualPreprocessing>(
        MAX_CACHED_VISUAL_RESULTS,
    )
    private val documentResultCache = BoundedResultCache<String, DocumentTextResult>(
        MAX_CACHED_DOCUMENT_RESULTS,
    )

    override fun capabilities(): Map<String, EngineCapability> = linkedMapOf(
        "ocr" to ocr.capability(),
        "document_text" to documents.capability(),
        "exact_hash" to exactHash.capability(),
        "perceptual_hash" to perceptualHash.capability(),
        "face_model" to faces.capability(),
        "object_model" to objects.capability(),
    )

    override fun process(
        record: StoredPreprocessRecord,
        cancellation: CancellationToken,
    ): PreprocessingRecordUpdate {
        val input = inputFactory.create(record)
        val executions = mutableListOf<ExecutionInfo>()
        val payload = JSONObject().put("schema_version", 1)
        var normalizedText = record.normalizedText
        var contentSha256 = record.contentSha256
        var perceptualValue: String? = null
        var faceVectors: String? = null

        if (record.contentUri != null) {
            val exact = exactHash.process(input, cancellation)
            executions.add(exact.execution)
            payload.put("exact_hash", exactJson(exact))
            if (exact.execution.status == ExecutionStatus.COMPLETED) {
                contentSha256 = exact.sha256
            }
        } else if (record.contentSha256 != null) {
            payload.put(
                "exact_hash",
                executionJson(
                    ExecutionInfo(
                        EngineIdentity("canonical content SHA-256", "1"),
                        ExecutionStatus.COMPLETED,
                        0,
                    ),
                )
                    .put("sha256", record.contentSha256)
                    .put("bytes_read", record.sizeBytes ?: 0),
            )
        }

        when (record.sourceKind) {
            "media_image", "media_video" -> {
                val runOcr = OnDeviceVisionPolicy.shouldRunOcr(
                    record.sourceKind,
                    record.displayName,
                    record.directoryHint,
                )
                val cacheKey = contentCacheKey(record, contentSha256, runOcr)
                val cached = cacheKey?.let(visualResultCache::get)
                val visual = cached?.asCacheHit() ?: processVisual(
                    input,
                    cancellation,
                    runOcr = runOcr,
                    runModels = false,
                    skippedOcrReason = "ocr_deferred_selective",
                ).also {
                    if (cacheKey != null && it.isCacheable()) {
                        visualResultCache.put(cacheKey, it)
                    }
                }
                executions.addAll(visual.executions)
                payload.put("perceptual_hash", perceptualJson(visual.perceptual))
                payload.put("ocr", ocrJson(visual.ocr))
                payload.put("face", faceJson(visual.faces))
                payload.put("objects", objectJson(visual.objects))
                if (visual.perceptual.execution.status == ExecutionStatus.COMPLETED) {
                    perceptualValue = visual.perceptual.hash
                }
                if (visual.ocr.execution.status in SUCCESS_EXECUTIONS) {
                    normalizedText = visual.ocr.normalizedText.ifBlank { normalizedText }
                }
                faceVectors = privateFaceVectors(visual.faces)
            }
            "visible_ui" -> {
                executions.add(normalizedSourceExecution())
                payload.put(
                    "normalized_source_text",
                    JSONObject()
                        .put("status", "completed")
                        .put("characters", record.normalizedText?.length ?: 0),
                )
                if (input.bitmapProvider != null) {
                    val visual = processVisual(
                        input,
                        cancellation,
                        runOcr = false,
                        runModels = false,
                        skippedOcrReason = "ocr_host_deferred",
                    )
                    executions.addAll(visual.executions)
                    payload.put("perceptual_hash", perceptualJson(visual.perceptual))
                    payload.put("ocr", ocrJson(visual.ocr))
                    payload.put("face", faceJson(visual.faces))
                    payload.put("objects", objectJson(visual.objects))
                    if (visual.perceptual.execution.status == ExecutionStatus.COMPLETED) {
                        perceptualValue = visual.perceptual.hash
                    }
                    if (visual.ocr.execution.status in SUCCESS_EXECUTIONS) {
                        normalizedText = mergeVisibleText(
                            normalizedText,
                            visual.ocr.normalizedText,
                        )
                    }
                    faceVectors = privateFaceVectors(visual.faces)
                }
            }
            "document" -> {
                val cacheKey = contentCacheKey(record, contentSha256)
                val cached = cacheKey?.let(documentResultCache::get)
                val document = cached?.asCacheHit() ?: documents.process(input, cancellation).also {
                    if (cacheKey != null && it.isCacheable()) {
                        documentResultCache.put(cacheKey, it)
                    }
                }
                executions.add(document.execution)
                payload.put("document_text", documentJson(document))
                if (document.execution.status in SUCCESS_EXECUTIONS) {
                    normalizedText = document.normalizedText
                }
            }
            else -> {
                payload.put(
                    "normalized_source_text",
                    JSONObject()
                        .put("status", "completed")
                        .put("characters", record.normalizedText?.length ?: 0),
                )
            }
        }

        val state = aggregateState(executions, cancellation)
        val warnings = executions.flatMap(ExecutionInfo::warnings).distinct().sorted()
        payload
            .put("status", state.wireName)
            .put("warnings", JSONArray(warnings))
        return PreprocessingRecordUpdate(
            state,
            payload.toString(),
            normalizedText,
            contentSha256,
            perceptualValue,
            faceVectors,
        )
    }

    override fun close() {
        visualResultCache.clear()
        documentResultCache.clear()
        inputFactory.close()
        ocr.close()
        faces.close()
        objects.close()
    }

    private fun aggregateState(
        executions: List<ExecutionInfo>,
        cancellation: CancellationToken,
    ): PreprocessingRecordState {
        if (cancellation.isCancelled() || executions.any {
                it.status == ExecutionStatus.CANCELLED
            }
        ) {
            return PreprocessingRecordState.CANCELLED
        }
        if (executions.isEmpty()) return PreprocessingRecordState.COMPLETED
        val successful = executions.count { it.status in SUCCESS_EXECUTIONS }
        return when {
            executions.any { it.status == ExecutionStatus.TRUNCATED } ->
                PreprocessingRecordState.TRUNCATED
            executions.any { it.status == ExecutionStatus.FAILED } && successful > 0 ->
                PreprocessingRecordState.TRUNCATED
            executions.any { it.status == ExecutionStatus.SKIPPED } && successful > 0 ->
                PreprocessingRecordState.TRUNCATED
            executions.all { it.status == ExecutionStatus.SKIPPED } ->
                PreprocessingRecordState.SKIPPED
            executions.all { it.status in setOf(ExecutionStatus.FAILED, ExecutionStatus.SKIPPED) } ->
                PreprocessingRecordState.FAILED
            else -> PreprocessingRecordState.COMPLETED
        }
    }

    private fun processVisual(
        input: PreprocessInput,
        cancellation: CancellationToken,
        runOcr: Boolean = true,
        runModels: Boolean = true,
        skippedOcrReason: String = "ocr_deferred_selective",
    ): VisualPreprocessing {
        val reusable = input.bitmapProvider?.let { provider ->
            ReusableBitmapProvider(provider, BuildConfig.MAX_SHARED_VISUAL_PIXELS)
        }
        val visualInput = if (reusable == null) input else input.copy(bitmapProvider = reusable)
        return try {
            val perceptual = perceptualHash.process(visualInput, cancellation)
            val ocrResult = if (runOcr) {
                ocr.process(visualInput, cancellation)
            } else {
                skippedOcrResult(skippedOcrReason)
            }
            val faceResult = if (runModels) {
                faces.process(visualInput, cancellation)
            } else {
                skippedFaceResult()
            }
            val objectResult = if (runModels) {
                objects.process(visualInput, cancellation)
            } else {
                skippedObjectResult()
            }
            val executions = buildList {
                add(perceptual.execution)
                if (runOcr) add(ocrResult.execution)
                if (runModels) {
                    add(faceResult.execution)
                    add(objectResult.execution)
                }
            }
            VisualPreprocessing(
                perceptual,
                ocrResult,
                faceResult,
                objectResult,
                executions,
            )
        } finally {
            reusable?.close()
        }
    }

    private fun contentCacheKey(
        record: StoredPreprocessRecord,
        sha256: String?,
        runOcr: Boolean = false,
    ): String? {
        val hash = sha256?.takeIf(CONTENT_SHA256::matches) ?: return null
        return "${record.sourceKind}\u001f${record.mimeType.lowercase()}\u001f$hash\u001f$runOcr"
    }

    private fun VisualPreprocessing.isCacheable(): Boolean = executions.none {
        it.status in setOf(ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)
    }

    private fun DocumentTextResult.isCacheable(): Boolean =
        execution.status !in setOf(ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)

    private fun VisualPreprocessing.asCacheHit(): VisualPreprocessing {
        val perceptualHit = perceptual.copy(execution = perceptual.execution.asCacheHit())
        val ocrHit = ocr.copy(execution = ocr.execution.asCacheHit())
        val faceHit = faces.copy(execution = faces.execution.asCacheHit())
        val objectHit = objects.copy(execution = objects.execution.asCacheHit())
        return VisualPreprocessing(
            perceptualHit,
            ocrHit,
            faceHit,
            objectHit,
            listOf(
                perceptualHit.execution,
                ocrHit.execution,
                faceHit.execution,
                objectHit.execution,
            ),
        )
    }

    private fun DocumentTextResult.asCacheHit(): DocumentTextResult =
        copy(execution = execution.asCacheHit())

    private fun ExecutionInfo.asCacheHit(): ExecutionInfo = copy(
        durationMs = 0,
        warnings = (warnings + "content_hash_cache_hit").distinct(),
    )

    private fun normalizedSourceExecution() = ExecutionInfo(
        EngineIdentity("canonical normalized source text", "1"),
        ExecutionStatus.COMPLETED,
        0,
    )

    private fun skippedOcrResult(reason: String) = TextOcrResult(
        ExecutionInfo(
            EngineIdentity("SATRIA host selective OCR", "1"),
            ExecutionStatus.SKIPPED,
            0,
            listOf(reason),
        ),
        "",
        emptyList(),
        null,
    )

    private fun skippedFaceResult() = FaceEmbeddingResult(
        ExecutionInfo(
            EngineIdentity("MediaPipe face skipped", "1"),
            ExecutionStatus.SKIPPED,
            0,
            listOf("on_device_vision_selective"),
        ),
        emptyList(),
    )

    private fun skippedObjectResult() = ObjectDetectionResult(
        ExecutionInfo(
            EngineIdentity("MediaPipe objects skipped", "1"),
            ExecutionStatus.SKIPPED,
            0,
            listOf("on_device_vision_selective"),
        ),
        emptyList(),
    )

    private fun mergeVisibleText(nodeText: String?, ocrText: String): String? {
        val merged = listOfNotNull(
            nodeText?.takeIf(String::isNotBlank),
            ocrText.takeIf(String::isNotBlank),
        ).distinct().joinToString("\n")
        return merged.takeIf(String::isNotBlank)?.let { value ->
            normalizeSearchText(value, BuildConfig.MAX_DOCUMENT_TEXT_CHARS)
        }
    }

    private fun executionJson(value: ExecutionInfo): JSONObject = JSONObject()
        .put("engine", engineJson(value.engine))
        .put("status", value.status.wireName)
        .put("duration_ms", value.durationMs)
        .put("warnings", JSONArray(value.warnings))

    private fun engineJson(value: EngineIdentity): JSONObject = JSONObject()
        .put("name", value.name)
        .put("version", value.version)
        .put("model_asset", value.modelAsset ?: JSONObject.NULL)
        .put("model_sha256", value.modelSha256 ?: JSONObject.NULL)

    private fun exactJson(value: ExactHashResult): JSONObject = executionJson(value.execution)
        .put("sha256", value.sha256 ?: JSONObject.NULL)
        .put("bytes_read", value.bytesRead)

    private fun perceptualJson(value: PerceptualHashResult): JSONObject =
        executionJson(value.execution)
            .put("algorithm", value.algorithm)
            .put("hash", value.hash ?: JSONObject.NULL)

    private fun ocrJson(value: TextOcrResult): JSONObject = executionJson(value.execution)
        .put("text", value.normalizedText)
        .put("mean_confidence", value.meanConfidence ?: JSONObject.NULL)
        .put(
            "regions",
            JSONArray().apply {
                value.regions.forEach { region ->
                    put(
                        JSONObject()
                            .put("text", region.text)
                            .put("left", region.left)
                            .put("top", region.top)
                            .put("right", region.right)
                            .put("bottom", region.bottom)
                            .put("confidence", region.confidence ?: JSONObject.NULL),
                    )
                }
            },
        )

    private fun documentJson(value: DocumentTextResult): JSONObject =
        executionJson(value.execution)
            .put("state", value.state.wireName)
            .put("extracted_characters", value.extractedCharacters)

    private fun faceJson(value: FaceEmbeddingResult): JSONObject = executionJson(value.execution)
        .put("signal_count", value.embeddings.size)
        .put(
            "signals",
            JSONArray().apply {
                value.embeddings.forEach { face ->
                    put(
                        JSONObject()
                            .put("face_index", face.faceIndex)
                            .put("confidence", face.confidence)
                            .put("left", face.left)
                            .put("top", face.top)
                            .put("right", face.right)
                            .put("bottom", face.bottom)
                            .put("vector_dimensions", face.vector.size),
                    )
                }
            },
        )

    private fun objectJson(value: ObjectDetectionResult): JSONObject =
        executionJson(value.execution)
            .put(
                "labels",
                JSONArray().apply {
                    value.labels.forEach { label ->
                        put(
                            JSONObject()
                                .put("label", label.label)
                                .put("confidence", label.confidence)
                                .put("left", label.left)
                                .put("top", label.top)
                                .put("right", label.right)
                                .put("bottom", label.bottom),
                        )
                    }
                },
            )

    private fun privateFaceVectors(value: FaceEmbeddingResult): String? {
        if (value.embeddings.isEmpty()) return null
        return JSONArray().apply {
            value.embeddings.forEach { face ->
                put(
                    JSONObject()
                        .put("face_index", face.faceIndex)
                        .put("confidence", face.confidence)
                        .put(
                            "area",
                            (face.right - face.left).toLong() * (face.bottom - face.top).toLong(),
                        )
                        .put("vector", JSONArray(face.vector.toList())),
                )
            }
        }.toString()
    }

    companion object {
        private const val MAX_CACHED_VISUAL_RESULTS = 64
        private const val MAX_CACHED_DOCUMENT_RESULTS = 32
        private val CONTENT_SHA256 = Regex("^[0-9a-f]{64}$")
        private val SUCCESS_EXECUTIONS = setOf(
            ExecutionStatus.COMPLETED,
            ExecutionStatus.TRUNCATED,
        )
    }

    private data class VisualPreprocessing(
        val perceptual: PerceptualHashResult,
        val ocr: TextOcrResult,
        val faces: FaceEmbeddingResult,
        val objects: ObjectDetectionResult,
        val executions: List<ExecutionInfo>,
    )
}

private class BoundedResultCache<K, V>(private val maximumEntries: Int) {
    private val values = object : LinkedHashMap<K, V>(maximumEntries, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<K, V>?): Boolean =
            size > maximumEntries
    }

    init {
        require(maximumEntries > 0)
    }

    @Synchronized
    fun get(key: K): V? = values[key]

    @Synchronized
    fun put(key: K, value: V) {
        values[key] = value
    }

    @Synchronized
    fun clear() {
        values.clear()
    }
}

private class ReusableBitmapProvider(
    private val source: BitmapProvider,
    private val sharedMaxPixels: Long,
) : BitmapProvider, AutoCloseable {
    private var base: Bitmap? = null
    private var loadAttempted = false
    private var closed = false

    init {
        require(sharedMaxPixels > 0)
    }

    override fun decode(maxPixels: Long): Bitmap? {
        if (closed || maxPixels <= 0) return null
        return try {
            var value = base
            if (value == null) {
                if (loadAttempted) return null
                loadAttempted = true
                val decoded = source.decode(sharedMaxPixels) ?: return null
                value = decoded.boundedForReuse(sharedMaxPixels)
                base = value
                if (value !== decoded && !decoded.isRecycled) decoded.recycle()
            }
            val reusableBase = value ?: return null
            if (reusableBase.isRecycled) return null
            reusableBase.ownedCopy(minOf(maxPixels, sharedMaxPixels))
        } catch (_: IllegalArgumentException) {
            null
        } catch (_: OutOfMemoryError) {
            null
        }
    }

    override fun close() {
        if (closed) return
        closed = true
        base?.let { bitmap ->
            if (!bitmap.isRecycled) bitmap.recycle()
        }
        base = null
    }

    private fun Bitmap.boundedForReuse(maxPixels: Long): Bitmap {
        val pixels = width.toLong() * height.toLong()
        if (pixels <= maxPixels) return this
        val scale = sqrt(maxPixels.toDouble() / pixels.toDouble())
        return Bitmap.createScaledBitmap(
            this,
            (width * scale).toInt().coerceAtLeast(1),
            (height * scale).toInt().coerceAtLeast(1),
            true,
        )
    }

    private fun Bitmap.ownedCopy(maxPixels: Long): Bitmap? {
        val pixels = width.toLong() * height.toLong()
        if (pixels <= 0) return null
        if (pixels <= maxPixels) return copy(Bitmap.Config.ARGB_8888, false)
        val scale = sqrt(maxPixels.toDouble() / pixels.toDouble())
        val scaled = Bitmap.createScaledBitmap(
            this,
            (width * scale).toInt().coerceAtLeast(1),
            (height * scale).toInt().coerceAtLeast(1),
            true,
        )
        return if (scaled === this) copy(Bitmap.Config.ARGB_8888, false) else scaled
    }
}
