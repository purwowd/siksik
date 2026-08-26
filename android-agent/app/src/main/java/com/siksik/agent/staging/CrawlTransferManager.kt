package com.siksik.agent.staging

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import com.siksik.agent.BuildConfig
import com.siksik.agent.api.BoundedTaskExecutor
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
import org.json.JSONArray
import org.json.JSONObject

class CrawlTransferManager(
    context: Context,
    private val preprocessing: PreprocessingStore,
    private val selection: SelectionStore,
    private val communication: CommunicationCaptureStore,
) {
    private val appContext = context.applicationContext
    private val resolver: ContentResolver = appContext.contentResolver
    private val stateStore = CrawlTransferStateStore(File(appContext.filesDir, STATE_DIR))
    private val executor = BoundedTaskExecutor(1, 4, "siksik-transfer")
    @Volatile
    private var cancelled = false

    @Synchronized
    fun start(
        sessionId: String,
        request: CrawlTransferRequest,
        idempotencyKey: String,
    ): CrawlTransferRecord {
        validateId(sessionId, "sesi")
        validateId(request.stageId, "stage")
        validateId(request.crawlId, "crawl")
        if (!FINGERPRINT.matches(request.selectionFingerprint)) {
            throw ApiException("validation_error", "Fingerprint selection tidak valid.", 422)
        }
        if (request.selectionRevision < 1) {
            throw ApiException("validation_error", "Revision selection tidak valid.", 422)
        }
        if (idempotencyKey.isBlank() || idempotencyKey.length > 128) {
            throw ApiException("validation_error", "Idempotency key tidak valid.", 422)
        }
        val snapshot = selection.selectedForTransfer(
            sessionId,
            request.crawlId,
            request.selectionRevision,
            request.selectionFingerprint,
        )
        val fingerprint = fingerprint(request)
        val existing = stateStore.load(request.stageId)
        if (existing != null) {
            if (existing.sessionId != sessionId || existing.crawlId != request.crawlId) {
                throw ApiException("agent_session_mismatch", "Transfer bukan milik sesi aktif.", 409)
            }
            if (
                existing.idempotencyKey != idempotencyKey ||
                existing.requestFingerprint != fingerprint
            ) {
                throw ApiException("conflict", "Transfer dengan ID yang sama sudah ada.", 409)
            }
            return existing
        }
        val queued = CrawlTransferRecord(
            stageId = request.stageId,
            crawlId = request.crawlId,
            sessionId = sessionId,
            state = "queued",
            selectionRevision = request.selectionRevision,
            selectionFingerprint = request.selectionFingerprint,
            policyFingerprint = snapshot.run.policyFingerprint,
            idempotencyKey = idempotencyKey,
            requestFingerprint = fingerprint,
            totalRecords = snapshot.candidates.size,
            completedRecords = 0,
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
        stateStore.save(queued)
        if (!executor.tryExecute { copy(queued, snapshot.candidates) }) {
            val failed = queued.copy(state = "failed", errorCategory = "stage_queue_full")
            stateStore.save(failed)
            throw ApiException("stage_failed", "Antrian transfer penuh.", 503, true)
        }
        return queued
    }

    @Synchronized
    fun status(sessionId: String, crawlId: String, stageId: String): CrawlTransferRecord {
        validateId(sessionId, "sesi")
        validateId(crawlId, "crawl")
        validateId(stageId, "stage")
        val record = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Transfer tidak ditemukan.", 404)
        if (record.sessionId != sessionId || record.crawlId != crawlId) {
            throw ApiException("agent_session_mismatch", "Transfer bukan milik sesi aktif.", 409)
        }
        return record
    }

    @Synchronized
    fun cleanup(sessionId: String, crawlId: String, stageId: String): CrawlTransferRecord {
        validateId(sessionId, "sesi")
        validateId(crawlId, "crawl")
        validateId(stageId, "stage")
        val record = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Transfer tidak ditemukan.", 404)
        if (record.sessionId != sessionId || record.crawlId != crawlId) {
            throw ApiException("agent_session_mismatch", "Transfer bukan milik sesi aktif.", 409)
        }
        if (record.cleanupReceiptId != null) {
            return record
        }
        val root = stageRoot(sessionId, stageId)
        val existed = root.isDirectory
        val deleted = if (existed) countFiles(root) else 0
        if (existed) root.deleteRecursively()
        val cleaned = record.copy(
            state = "cleaned",
            cleanupReceiptId = "receipt_${UUID.randomUUID()}",
            cleanupDeletedFiles = deleted,
            cleanupAlreadyAbsent = !existed,
            cleanupEpochMs = System.currentTimeMillis(),
        )
        stateStore.save(cleaned)
        return cleaned
    }

    fun shutdown() {
        cancelled = true
        executor.shutdownNow()
    }

    private fun copy(initial: CrawlTransferRecord, candidates: List<SelectionCandidate>) {
        var current = initial.copy(state = "copying")
        persist(current)
        val artifacts = mutableListOf<DirectStagedArtifact>()
        try {
            val root = stageRoot(initial.sessionId, initial.stageId)
            if (!root.mkdirs() && !root.isDirectory) {
                throw ApiException("stage_failed", "Direktori transfer tidak dapat dibuat.", 500)
            }
            var total = 0L
            candidates.forEachIndexed { index, candidate ->
                if (cancelled) {
                    throw ApiException("stage_cancelled", "Transfer dibatalkan.", 409)
                }
                val remaining = BuildConfig.MAX_STAGE_TOTAL_BYTES - total
                if (remaining <= 0) {
                    throw ApiException("media_too_large", "Transfer melewati batas total.", 413)
                }
                val stored = preprocessing.transferRecord(
                    initial.sessionId,
                    initial.crawlId,
                    candidate.recordId,
                )
                val canonical = writeCanonical(current, candidate, stored, root)
                artifacts.add(canonical)
                total += canonical.sizeBytes
                if (stored.sourceKind in BINARY_SOURCE_KINDS && stored.contentUri != null) {
                    val binary = writeBinary(current, stored, root, remaining - canonical.sizeBytes)
                    artifacts.add(binary)
                    total += binary.sizeBytes
                }
                if (stored.sourceKind == "visible_ui") {
                    stored.attachmentIds.forEach { screenshotId ->
                        val shot = writeScreenshot(
                            current,
                            stored,
                            screenshotId,
                            root,
                            BuildConfig.MAX_STAGE_TOTAL_BYTES - total,
                        ) ?: return@forEach
                        artifacts.add(shot)
                        total += shot.sizeBytes
                    }
                }
                current = current.copy(
                    completedRecords = index + 1,
                    artifactCount = artifacts.size,
                    totalBytes = total,
                )
                persist(current)
            }
            current = current.copy(state = "finalizing", artifactCount = artifacts.size, totalBytes = total)
            persist(current)
            val created = System.currentTimeMillis()
            val manifestBytes = ManifestBuilder.buildDirect(current, artifacts, created)
            val manifestFile = File(root, "manifest.json")
            manifestFile.writeBytes(manifestBytes)
            current = current.copy(
                state = "completed",
                artifactCount = artifacts.size,
                totalBytes = total,
                manifestRelativePath = "${initial.sessionId}/${initial.stageId}/manifest.json",
                manifestSizeBytes = manifestBytes.size.toLong(),
                manifestSha256 = sha256Hex(manifestBytes),
            )
            persist(current)
        } catch (exception: ApiException) {
            persist(
                current.copy(
                    state = if (exception.code == "stage_cancelled") "cancelled" else "failed",
                    errorCategory = exception.code,
                ),
            )
        } catch (_: Exception) {
            persist(current.copy(state = "failed", errorCategory = "stage_failed"))
        }
    }

    private fun writeCanonical(
        record: CrawlTransferRecord,
        candidate: SelectionCandidate,
        stored: TransferPreprocessedRecord,
        root: File,
    ): DirectStagedArtifact {
        val payload = stored.payload
            .put("selection", selectionJson(record, candidate))
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        val relative = "${record.sessionId}/${record.stageId}/records/${stored.recordId}.siksik-record.json"
        val file = StagePathPolicy.controlledChild(root, "records/${stored.recordId}.siksik-record.json")
        file.parentFile?.mkdirs()
        file.writeBytes(bytes)
        return DirectStagedArtifact(
            artifactId = "artifact_${stored.recordId}",
            recordId = stored.recordId,
            sourceKind = stored.sourceKind,
            role = "canonical_record",
            attachmentId = null,
            relativePath = relative,
            mimeType = CANONICAL_MIME,
            sizeBytes = bytes.size.toLong(),
            sha256 = sha256Hex(bytes),
        )
    }

    private fun writeBinary(
        record: CrawlTransferRecord,
        stored: TransferPreprocessedRecord,
        root: File,
        remaining: Long,
    ): DirectStagedArtifact {
        val maxBytes = minOf(BuildConfig.MAX_STAGE_FILE_BYTES, remaining)
        if (maxBytes <= 0) {
            throw ApiException("media_too_large", "Transfer melewati batas total.", 413)
        }
        val uri = Uri.parse(stored.contentUri)
        if (uri.scheme != ContentResolver.SCHEME_CONTENT) {
            throw ApiException("stage_failed", "URI media transfer tidak valid.", 422)
        }
        val artifactId = "artifact_${stored.recordId}_bin"
        val fileName = StagePathPolicy.safeStagedName(artifactId, stored.displayName)
        val file = StagePathPolicy.controlledChild(root, "files/$fileName")
        file.parentFile?.mkdirs()
        val copied = FileOutputStream(file).use { output ->
            copyStream(openContent(uri), output, maxBytes)
        }
        return DirectStagedArtifact(
            artifactId = artifactId,
            recordId = stored.recordId,
            sourceKind = stored.sourceKind,
            role = "source_binary",
            attachmentId = null,
            relativePath = "${record.sessionId}/${record.stageId}/files/$fileName",
            mimeType = stored.mimeType,
            sizeBytes = copied.first,
            sha256 = copied.second,
        )
    }

    private fun writeScreenshot(
        record: CrawlTransferRecord,
        stored: TransferPreprocessedRecord,
        screenshotId: String,
        root: File,
        remaining: Long,
    ): DirectStagedArtifact? {
        val source = communication.screenshotForTransfer(
            record.sessionId,
            record.crawlId,
            screenshotId,
        ) ?: return null
        val maxBytes = minOf(BuildConfig.MAX_STAGE_FILE_BYTES, remaining)
        if (source.length() > maxBytes) {
            throw ApiException("media_too_large", "Screenshot melewati batas staging.", 413)
        }
        val file = StagePathPolicy.controlledChild(root, "screenshots/$screenshotId.png")
        file.parentFile?.mkdirs()
        val copied = FileOutputStream(file).use { output ->
            copyStream(FileInputStream(source), output, maxBytes)
        }
        return DirectStagedArtifact(
            artifactId = "artifact_${stored.recordId}_$screenshotId",
            recordId = stored.recordId,
            sourceKind = stored.sourceKind,
            role = "screenshot",
            attachmentId = screenshotId,
            relativePath = "${record.sessionId}/${record.stageId}/screenshots/$screenshotId.png",
            mimeType = "image/png",
            sizeBytes = copied.first,
            sha256 = copied.second,
        )
    }

    private fun openContent(uri: Uri): InputStream = try {
        resolver.openInputStream(uri)
            ?: throw ApiException("stage_failed", "Stream media tidak tersedia.", 422)
    } catch (exception: ApiException) {
        throw exception
    } catch (_: SecurityException) {
        throw ApiException("grant_revoked", "Grant tidak lagi aktif.", 410)
    } catch (_: IOException) {
        throw ApiException("stage_failed", "Media tidak dapat disalin ke staging.", 422)
    }

    private fun copyStream(
        input: InputStream,
        output: FileOutputStream,
        maxBytes: Long,
    ): Pair<Long, String> {
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        input.use { stream ->
            val buffer = ByteArray(COPY_BUFFER)
            while (true) {
                if (cancelled) {
                    throw ApiException("stage_cancelled", "Transfer dibatalkan.", 409)
                }
                val read = stream.read(buffer)
                if (read < 0) break
                if (read == 0) continue
                total += read
                if (total > maxBytes) {
                    throw ApiException("media_too_large", "Media melewati batas staging.", 413)
                }
                output.write(buffer, 0, read)
                digest.update(buffer, 0, read)
            }
        }
        if (total <= 0) {
            throw ApiException("stage_failed", "Media kosong tidak dapat diproses.", 422)
        }
        return total to digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun selectionJson(
        record: CrawlTransferRecord,
        candidate: SelectionCandidate,
    ): JSONObject = JSONObject()
        .put("policy_version", selection.getRun(record.sessionId, record.crawlId).policyVersion)
        .put("policy_fingerprint", record.policyFingerprint)
        .put("revision", record.selectionRevision)
        .put("selection_fingerprint", record.selectionFingerprint)
        .put("score", candidate.scoreBasisPoints / 10_000.0)
        .put("threshold", candidate.thresholdBasisPoints / 10_000.0)
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

    @Synchronized
    private fun persist(record: CrawlTransferRecord) {
        stateStore.save(record)
    }

    private fun stageRoot(sessionId: String, stageId: String): File {
        val external = appContext.getExternalFilesDir(null)
            ?: throw ApiException("stage_failed", "Penyimpanan transfer tidak tersedia.", 500)
        return File(external, "$ROOT_DIR/$sessionId/$stageId")
    }

    companion object {
        private const val STATE_DIR = "siksik_transfer_state"
        private const val ROOT_DIR = "siksik_transfer"
        private const val COPY_BUFFER = 64 * 1024
        private const val CANONICAL_MIME = "application/vnd.siksik.crawl-record+json"
        private val FINGERPRINT = Regex("^[0-9a-f]{64}$")
        private val BINARY_SOURCE_KINDS = setOf(
            "media_image",
            "media_video",
            "media_audio",
            "document",
        )

        private fun validateId(value: String, label: String) {
            if (!SessionAuthenticator.SAFE_ID.matches(value)) {
                throw ApiException("validation_error", "ID $label tidak valid.", 422)
            }
        }

        private fun fingerprint(request: CrawlTransferRequest): String {
            val material =
                "${request.stageId}\u001f${request.crawlId}\u001f${request.selectionRevision}\u001f${request.selectionFingerprint}"
            return sha256Hex(material.toByteArray(Charsets.UTF_8))
        }

        private fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }

        private fun countFiles(root: File): Int {
            if (!root.exists()) return 0
            return root.walkTopDown().count { it.isFile }
        }
    }
}
