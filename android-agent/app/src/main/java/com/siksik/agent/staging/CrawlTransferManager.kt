package com.siksik.agent.staging

import android.content.Context
import android.net.Uri
import android.os.StatFs
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.PreprocessingStore
import com.siksik.agent.preprocessing.TransferPreprocessedRecord
import com.siksik.agent.selection.SelectionCandidate
import com.siksik.agent.selection.SelectionStore
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.communication.CommunicationCaptureStore
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

class CrawlTransferManager(
    context: Context,
    private val preprocessingStore: PreprocessingStore,
    private val selectionStore: SelectionStore,
    private val communicationStore: CommunicationCaptureStore,
) {
    private val appContext = context.applicationContext
    private val externalBase = appContext.getExternalFilesDir(null)?.let { base ->
        File(base, TRANSFER_DIRECTORY)
    } ?: throw ApiException("storage_unavailable", "Penyimpanan transfer tidak tersedia.", 507)
    private val stateStore = CrawlTransferStateStore(
        File(appContext.filesDir, TRANSFER_STATE_DIRECTORY),
    )
    private val running = ConcurrentHashMap.newKeySet<String>()
    private val cancelled = ConcurrentHashMap.newKeySet<String>()
    private val executor = ThreadPoolExecutor(
        1,
        1,
        30,
        TimeUnit.SECONDS,
        ArrayBlockingQueue(2),
        { runnable -> Thread(runnable, "siksik-crawl-transfer").apply { isDaemon = true } },
        ThreadPoolExecutor.AbortPolicy(),
    )

    init {
        if (!externalBase.mkdirs() && !externalBase.isDirectory) {
            throw ApiException("storage_unavailable", "Root transfer tidak tersedia.", 507)
        }
    }

    @Synchronized
    fun start(
        sessionId: String,
        request: CrawlTransferRequest,
        idempotencyKey: String,
    ): CrawlTransferRecord {
        validateRequest(sessionId, request, idempotencyKey)
        val snapshot = selectionStore.selectedForTransfer(
            sessionId,
            request.crawlId,
            request.selectionRevision,
            request.selectionFingerprint,
        )
        val requestFingerprint = requestFingerprint(sessionId, request)
        val existing = stateStore.load(request.stageId)
        if (existing != null) {
            if (
                existing.sessionId != sessionId ||
                existing.crawlId != request.crawlId ||
                existing.idempotencyKey != idempotencyKey ||
                existing.requestFingerprint != requestFingerprint
            ) {
                throw ApiException("conflict", "Identitas transfer sudah digunakan.", 409)
            }
            if (existing.state in setOf("completed", "cleaned") || request.stageId in running) {
                return existing
            }
            val resumable = if (existing.state in setOf("failed", "cancelled")) {
                existing.copy(
                    state = "queued",
                    completedRecords = 0,
                    artifactCount = 0,
                    totalBytes = 0,
                    manifestRelativePath = null,
                    manifestSizeBytes = null,
                    manifestSha256 = null,
                    errorCategory = null,
                ).also(stateStore::save)
            } else {
                existing
            }
            submit(request.stageId)
            return resumable
        }
        val created = CrawlTransferRecord(
            stageId = request.stageId,
            sessionId = sessionId,
            crawlId = request.crawlId,
            selectionRevision = request.selectionRevision,
            selectionFingerprint = request.selectionFingerprint,
            idempotencyKey = idempotencyKey,
            requestFingerprint = requestFingerprint,
            state = "queued",
            completedRecords = 0,
            totalRecords = snapshot.candidates.size,
            artifactCount = 0,
            totalBytes = 0,
            manifestRelativePath = null,
            manifestSizeBytes = null,
            manifestSha256 = null,
            errorCategory = null,
            cleanupReceiptId = null,
            cleanupDeletedFiles = null,
            cleanupAlreadyAbsent = null,
            cleanupEpochMs = null,
        )
        stateStore.save(created)
        submit(created.stageId)
        return created
    }

    @Synchronized
    fun status(sessionId: String, crawlId: String, stageId: String): CrawlTransferRecord {
        validateId(sessionId, "session")
        validateId(crawlId, "crawl")
        validateId(stageId, "stage")
        val value = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Transfer tidak ditemukan.", 404)
        if (value.sessionId != sessionId || value.crawlId != crawlId) {
            throw ApiException("agent_session_mismatch", "Transfer milik sesi lain.", 409)
        }
        return value
    }

    @Synchronized
    fun cleanup(sessionId: String, crawlId: String, stageId: String): CrawlTransferRecord {
        var value = status(sessionId, crawlId, stageId)
        if (value.cleanupReceiptId != null) return value
        if (stageId in running) {
            cancelled.add(stageId)
            throw ApiException("conflict", "Pembatalan transfer masih berjalan.", 409, true)
        }
        val root = stageRoot(value)
        val alreadyAbsent = !root.exists()
        val deletedFiles = if (alreadyAbsent) 0 else root.walkTopDown().count(File::isFile)
        if (!alreadyAbsent && !root.deleteRecursively()) {
            throw ApiException("storage_unavailable", "Cleanup transfer gagal.", 507)
        }
        value = value.copy(
            state = "cleaned",
            cleanupReceiptId = "cleanup_${UUID.randomUUID()}",
            cleanupDeletedFiles = deletedFiles,
            cleanupAlreadyAbsent = alreadyAbsent,
            cleanupEpochMs = System.currentTimeMillis(),
        )
        stateStore.save(value)
        return value
    }

    fun shutdown() {
        cancelled.addAll(running)
        executor.shutdownNow()
        executor.awaitTermination(3, TimeUnit.SECONDS)
    }

    private fun submit(stageId: String) {
        if (!running.add(stageId)) return
        try {
            executor.execute {
                try {
                    process(stageId)
                } finally {
                    running.remove(stageId)
                    cancelled.remove(stageId)
                }
            }
        } catch (_: RejectedExecutionException) {
            running.remove(stageId)
            throw ApiException("stage_failed", "Antrean transfer penuh.", 503, true)
        }
    }

    private fun process(stageId: String) {
        var state = stateStore.load(stageId) ?: return
        val root = stageRoot(state)
        try {
            if (root.exists() && !root.deleteRecursively()) {
                throw ApiException("storage_unavailable", "Transfer sebelumnya tidak dapat direset.", 507)
            }
            val recordsRoot = StagePathPolicy.controlledChild(root, RECORDS_DIRECTORY)
            val artifactsRoot = StagePathPolicy.controlledChild(root, ARTIFACTS_DIRECTORY)
            if ((!recordsRoot.mkdirs() && !recordsRoot.isDirectory) ||
                (!artifactsRoot.mkdirs() && !artifactsRoot.isDirectory)
            ) {
                throw ApiException("storage_unavailable", "Direktori transfer tidak tersedia.", 507)
            }
            val snapshot = selectionStore.selectedForTransfer(
                state.sessionId,
                state.crawlId,
                state.selectionRevision,
                state.selectionFingerprint,
            )
            if (snapshot.candidates.size != state.totalRecords) {
                throw ApiException("selection_not_ready", "Selection berubah sebelum transfer.", 409)
            }
            state = state.copy(
                state = "copying",
                completedRecords = 0,
                artifactCount = 0,
                totalBytes = 0,
                manifestRelativePath = null,
                manifestSizeBytes = null,
                manifestSha256 = null,
                errorCategory = null,
            )
            stateStore.save(state)
            val entries = mutableListOf<CrawlTransferArtifact>()
            var totalBytes = 0L
            snapshot.candidates.forEachIndexed { index, candidate ->
                ensureNotCancelled(stageId)
                val source = preprocessingStore.transferRecord(
                    state.sessionId,
                    state.crawlId,
                    candidate.recordId,
                )
                val payload = JSONObject(source.payload.toString()).put(
                    "selection",
                    selectionJson(snapshot.run.policyVersion, snapshot.run.policyFingerprint, state, candidate),
                )
                val recordBytes = CanonicalJson.bytes(payload)
                if (recordBytes.size > MAX_CANONICAL_RECORD_BYTES) {
                    throw ApiException("stage_failed", "Record canonical melewati batas.", 413)
                }
                val recordArtifact = CrawlTransferArtifact(
                    artifactId = "artifact_${UUID.randomUUID()}",
                    recordId = source.recordId,
                    sourceKind = source.sourceKind,
                    role = "canonical_record",
                    attachmentId = null,
                    relativePath = relativePath(
                        state,
                        "$RECORDS_DIRECTORY/${source.recordId}$CANONICAL_RECORD_SUFFIX",
                    ),
                    mimeType = CANONICAL_RECORD_MIME,
                    sizeBytes = recordBytes.size.toLong(),
                    sha256 = sha256(recordBytes),
                )
                val recordFile = StagePathPolicy.controlledChild(
                    recordsRoot,
                    "${source.recordId}$CANONICAL_RECORD_SUFFIX",
                )
                writeBytes(recordFile, recordBytes)
                entries.add(recordArtifact)
                totalBytes = addBounded(totalBytes, recordArtifact.sizeBytes)

                totalBytes = copyOptionalBinary(state, source, artifactsRoot, entries, totalBytes)

                source.attachmentIds.distinct().forEach { attachmentId ->
                    ensureNotCancelled(stageId)
                    totalBytes = copyOptionalScreenshot(
                        state,
                        source,
                        attachmentId,
                        artifactsRoot,
                        entries,
                        totalBytes,
                    )
                }
                if (entries.size > MAX_MANIFEST_ARTIFACTS) {
                    throw ApiException("stage_failed", "Jumlah artifact transfer melewati batas.", 413)
                }
                state = state.copy(
                    completedRecords = index + 1,
                    artifactCount = entries.size,
                    totalBytes = totalBytes,
                )
                if (
                    state.completedRecords % STATE_CHECKPOINT_RECORDS == 0 ||
                    state.completedRecords == state.totalRecords
                ) {
                    stateStore.save(state)
                }
            }
            ensureNotCancelled(stageId)
            state = state.copy(state = "finalizing")
            stateStore.save(state)
            val manifestBytes = buildManifest(state, snapshot.run.policyFingerprint, entries)
            val manifest = StagePathPolicy.controlledChild(root, MANIFEST_NAME)
            writeBytes(manifest, manifestBytes, sync = true)
            state = state.copy(
                state = "completed",
                artifactCount = entries.size,
                totalBytes = totalBytes,
                manifestRelativePath = relativePath(state, MANIFEST_NAME),
                manifestSizeBytes = manifestBytes.size.toLong(),
                manifestSha256 = sha256(manifestBytes),
            )
            stateStore.save(state)
        } catch (exception: ApiException) {
            stateStore.save(
                state.copy(
                    state = if (exception.code == "stage_cancelled") "cancelled" else "failed",
                    errorCategory = exception.code,
                ),
            )
        } catch (_: SecurityException) {
            stateStore.save(state.copy(state = "failed", errorCategory = "access_denied"))
        } catch (_: IOException) {
            stateStore.save(state.copy(state = "failed", errorCategory = "storage_unavailable"))
        } catch (_: RuntimeException) {
            stateStore.save(state.copy(state = "failed", errorCategory = "stage_failed"))
        }
    }

    private fun copyOptionalBinary(
        state: CrawlTransferRecord,
        source: TransferPreprocessedRecord,
        artifactsRoot: File,
        entries: MutableList<CrawlTransferArtifact>,
        totalBytes: Long,
    ): Long {
        val uriText = source.contentUri
        if (uriText.isNullOrBlank()) {
            if (source.sourceKind in BINARY_SOURCE_KINDS) {
                Log.w(
                    LOG_TAG,
                    "event=transfer_skip_binary reason=missing_uri record=${source.recordId}",
                )
            }
            return totalBytes
        }
        val uri = Uri.parse(uriText)
        if (uri.scheme != "content") {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_binary reason=unsupported_uri record=${source.recordId}",
            )
            return totalBytes
        }
        return try {
            val artifactId = "artifact_${UUID.randomUUID()}"
            val stagedName = StagePathPolicy.safeStagedName(artifactId, source.displayName)
            val target = StagePathPolicy.controlledChild(artifactsRoot, stagedName)
            val copied = appContext.contentResolver.openInputStream(uri)?.use { input ->
                copyStream(input, target)
            }
            if (copied == null) {
                Log.w(
                    LOG_TAG,
                    "event=transfer_skip_binary reason=unreadable record=${source.recordId}",
                )
                return totalBytes
            }
            val next = addBounded(totalBytes, copied.sizeBytes)
            entries.add(
                CrawlTransferArtifact(
                    artifactId,
                    source.recordId,
                    source.sourceKind,
                    "source_binary",
                    null,
                    relativePath(state, "$ARTIFACTS_DIRECTORY/$stagedName"),
                    source.mimeType,
                    copied.sizeBytes,
                    copied.sha256,
                ),
            )
            next
        } catch (exception: ApiException) {
            if (exception.code == "stage_cancelled") throw exception
            Log.w(
                LOG_TAG,
                "event=transfer_skip_binary reason=${exception.code} record=${source.recordId}",
            )
            totalBytes
        } catch (_: SecurityException) {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_binary reason=access_denied record=${source.recordId}",
            )
            totalBytes
        } catch (_: IOException) {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_binary reason=storage_unavailable record=${source.recordId}",
            )
            totalBytes
        }
    }

    private fun copyOptionalScreenshot(
        state: CrawlTransferRecord,
        source: TransferPreprocessedRecord,
        attachmentId: String,
        artifactsRoot: File,
        entries: MutableList<CrawlTransferArtifact>,
        totalBytes: Long,
    ): Long {
        if (source.sourceKind != "visible_ui") {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_screenshot reason=unsupported_source record=${source.recordId}",
            )
            return totalBytes
        }
        return try {
            val screenshot = communicationStore.screenshotForTransfer(
                state.sessionId,
                state.crawlId,
                attachmentId,
            )
            if (screenshot == null) {
                Log.w(
                    LOG_TAG,
                    "event=transfer_skip_screenshot reason=missing record=${source.recordId} " +
                        "attachment=$attachmentId",
                )
                return totalBytes
            }
            val artifactId = "artifact_${UUID.randomUUID()}"
            val stagedName = "$artifactId.png"
            val target = StagePathPolicy.controlledChild(artifactsRoot, stagedName)
            val copied = FileInputStream(screenshot).use { input -> copyStream(input, target) }
            val next = addBounded(totalBytes, copied.sizeBytes)
            entries.add(
                CrawlTransferArtifact(
                    artifactId,
                    source.recordId,
                    source.sourceKind,
                    "screenshot",
                    attachmentId,
                    relativePath(state, "$ARTIFACTS_DIRECTORY/$stagedName"),
                    "image/png",
                    copied.sizeBytes,
                    copied.sha256,
                ),
            )
            next
        } catch (exception: ApiException) {
            if (exception.code == "stage_cancelled") throw exception
            Log.w(
                LOG_TAG,
                "event=transfer_skip_screenshot reason=${exception.code} record=${source.recordId}",
            )
            totalBytes
        } catch (_: SecurityException) {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_screenshot reason=access_denied record=${source.recordId}",
            )
            totalBytes
        } catch (_: IOException) {
            Log.w(
                LOG_TAG,
                "event=transfer_skip_screenshot reason=storage_unavailable record=${source.recordId}",
            )
            totalBytes
        }
    }

    private fun selectionJson(
        policyVersion: String,
        policyFingerprint: String,
        state: CrawlTransferRecord,
        candidate: SelectionCandidate,
    ): JSONObject = JSONObject()
        .put("policy_version", policyVersion)
        .put("policy_fingerprint", policyFingerprint)
        .put("revision", state.selectionRevision)
        .put("selection_fingerprint", state.selectionFingerprint)
        .put("score", candidate.scoreBasisPoints.toDouble() / BASIS_POINTS)
        .put("threshold", candidate.thresholdBasisPoints.toDouble() / BASIS_POINTS)
        .put("auto_selected", candidate.autoSelected)
        .put("selected", true)
        .put("matched_keywords", JSONArray(candidate.matchedKeywords))
        .put("matched_rules", JSONArray(candidate.matchedRules))
        .put(
            "model_signals",
            JSONArray().apply {
                candidate.modelSignals.forEach { signal ->
                    put(
                        JSONObject()
                            .put("signal", signal.signal)
                            .put("value", signal.value)
                            .put("weight_basis_points", signal.weightBasisPoints),
                    )
                }
            },
        )
        .put("reasons", JSONArray(candidate.reasons))
        .put("human_override", candidate.humanOverride.wireName)
        .put("operator_id", candidate.operatorId ?: JSONObject.NULL)
        .put("decided_at", Instant.ofEpochMilli(candidate.decidedAtEpochMs).toString())

    private fun buildManifest(
        state: CrawlTransferRecord,
        policyFingerprint: String,
        entries: List<CrawlTransferArtifact>,
    ): ByteArray {
        val artifacts = JSONArray()
        entries.forEach { value ->
            artifacts.put(
                JSONObject()
                    .put("artifact_id", value.artifactId)
                    .put("record_id", value.recordId)
                    .put("source_kind", value.sourceKind)
                    .put("role", value.role)
                    .put("attachment_id", value.attachmentId ?: JSONObject.NULL)
                    .put("relative_path", value.relativePath)
                    .put("mime_type", value.mimeType)
                    .put("size_bytes", value.sizeBytes)
                    .put("sha256", value.sha256),
            )
        }
        return CanonicalJson.bytes(
            JSONObject()
                .put("schema_version", 1)
                .put("bundle_format", "direct_manifest_files_v1")
                .put("stage_id", state.stageId)
                .put("siksik_session_id", state.sessionId)
                .put("crawl_id", state.crawlId)
                .put("selection_revision", state.selectionRevision)
                .put("selection_fingerprint", state.selectionFingerprint)
                .put("policy_fingerprint", policyFingerprint)
                .put("record_count", state.totalRecords)
                .put("artifact_count", entries.size)
                .put("total_bytes", entries.sumOf(CrawlTransferArtifact::sizeBytes))
                .put("created_at_epoch_ms", System.currentTimeMillis())
                .put("artifacts", artifacts),
        )
    }

    private fun copyStream(input: InputStream, target: File): CopyResult {
        ensureAvailableStorage(target.parentFile ?: externalBase)
        val partial = StagePathPolicy.controlledChild(
            target.parentFile ?: externalBase,
            "${target.name}.partial",
        )
        val digest = MessageDigest.getInstance("SHA-256")
        var size = 0L
        try {
            FileOutputStream(partial).use { output ->
                val buffer = ByteArray(COPY_BUFFER_BYTES)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (read == 0) continue
                    size += read
                    if (size > BuildConfig.MAX_STAGE_FILE_BYTES) {
                        throw ApiException("stage_failed", "Artifact melewati batas ukuran.", 413)
                    }
                    output.write(buffer, 0, read)
                    digest.update(buffer, 0, read)
                }
                output.fd.sync()
            }
            if (size == 0L) throw ApiException("stage_failed", "Artifact kosong.", 422)
            if (!partial.renameTo(target)) {
                throw ApiException("storage_unavailable", "Finalisasi artifact gagal.", 507)
            }
            return CopyResult(size, digest.digest().toHex())
        } finally {
            if (partial.exists()) partial.delete()
        }
    }

    private fun writeBytes(target: File, bytes: ByteArray, sync: Boolean = false) {
        ensureAvailableStorage(target.parentFile ?: externalBase)
        val partial = StagePathPolicy.controlledChild(
            target.parentFile ?: externalBase,
            "${target.name}.partial",
        )
        try {
            FileOutputStream(partial).use { output ->
                output.write(bytes)
                if (sync) output.fd.sync()
            }
            if (!partial.renameTo(target)) {
                throw ApiException("storage_unavailable", "Finalisasi file transfer gagal.", 507)
            }
        } finally {
            if (partial.exists()) partial.delete()
        }
    }

    private fun addBounded(current: Long, added: Long): Long {
        if (added < 0 || current > BuildConfig.MAX_STAGE_TOTAL_BYTES - added) {
            throw ApiException("stage_failed", "Total transfer melewati batas ukuran.", 413)
        }
        return current + added
    }

    private fun ensureAvailableStorage(path: File) {
        if (StatFs(path.absolutePath).availableBytes < MIN_AVAILABLE_BYTES) {
            throw ApiException("storage_unavailable", "Ruang transfer tidak mencukupi.", 507)
        }
    }

    private fun ensureNotCancelled(stageId: String) {
        if (stageId in cancelled || Thread.currentThread().isInterrupted) {
            throw ApiException("stage_cancelled", "Transfer dibatalkan.", 409)
        }
    }

    private fun validateRequest(
        sessionId: String,
        request: CrawlTransferRequest,
        idempotencyKey: String,
    ) {
        validateId(sessionId, "session")
        validateId(request.stageId, "stage")
        validateId(request.crawlId, "crawl")
        if (
            request.selectionRevision < 1 ||
            !SHA256.matches(request.selectionFingerprint) ||
            !SAFE_KEY.matches(idempotencyKey)
        ) {
            throw ApiException("validation_error", "Kontrak transfer tidak valid.", 422)
        }
    }

    private fun validateId(value: String, label: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(value)) {
            throw ApiException("validation_error", "ID $label tidak valid.", 422)
        }
    }

    private fun requestFingerprint(sessionId: String, request: CrawlTransferRequest): String =
        sha256(
            listOf(
                sessionId,
                request.stageId,
                request.crawlId,
                request.selectionRevision,
                request.selectionFingerprint,
            ).joinToString("\u001f").toByteArray(Charsets.UTF_8),
        )

    private fun stageRoot(value: CrawlTransferRecord): File = StagePathPolicy.controlledChild(
        externalBase,
        "${value.sessionId}/${value.stageId}",
    )

    private fun relativePath(value: CrawlTransferRecord, leaf: String): String =
        "${value.sessionId}/${value.stageId}/$leaf"

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .toHex()

    private fun ByteArray.toHex(): String = joinToString("") { value -> "%02x".format(value) }

    private data class CopyResult(val sizeBytes: Long, val sha256: String)

    companion object {
        const val TRANSFER_DIRECTORY = "siksik_transfer"
        const val CANONICAL_RECORD_MIME = "application/vnd.siksik.crawl-record+json"
        const val CANONICAL_RECORD_SUFFIX = ".siksik-record.json"
        private const val TRANSFER_STATE_DIRECTORY = "siksik_transfer_state"
        private const val RECORDS_DIRECTORY = "records"
        private const val ARTIFACTS_DIRECTORY = "artifacts"
        private const val MANIFEST_NAME = "manifest.json"
        private const val MAX_CANONICAL_RECORD_BYTES = 2 * 1024 * 1024
        private const val MAX_MANIFEST_ARTIFACTS = 30_000
        private const val COPY_BUFFER_BYTES = 256 * 1024
        private const val STATE_CHECKPOINT_RECORDS = 16
        private const val MIN_AVAILABLE_BYTES = 16L * 1024 * 1024
        private const val LOG_TAG = "SIKSIKTransfer"
        private const val BASIS_POINTS = 10_000.0
        private val SHA256 = Regex("^[0-9a-f]{64}$")
        private val SAFE_KEY = Regex("^[A-Za-z0-9_.:-]{8,128}$")
        private val BINARY_SOURCE_KINDS = setOf(
            "media_image",
            "media_video",
            "media_audio",
            "document",
        )
    }
}
