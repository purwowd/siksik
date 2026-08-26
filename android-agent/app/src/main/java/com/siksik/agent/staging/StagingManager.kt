package com.siksik.agent.staging

import android.content.Context
import com.siksik.agent.BuildConfig
import com.siksik.agent.api.BoundedTaskExecutor
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.media.MediaCatalog
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID

class StagingManager(
    context: Context,
    private val catalog: MediaCatalog,
    private val grants: GrantGateway,
) {
    private val appContext = context.applicationContext
    private val stateStore = StageStateStore(File(appContext.filesDir, STATE_DIR))
    private val executor = BoundedTaskExecutor(1, 4, "siksik-stage")
    @Volatile
    private var cancelled = false

    @Synchronized
    fun start(sessionId: String, request: StageRequest, idempotencyKey: String): StageRecord {
        validateId(sessionId, "sesi")
        validateId(request.stageId, "stage")
        validateId(request.grantId, "grant")
        validateId(request.catalogId, "katalog")
        validateId(request.sourceId, "sumber")
        if (!FINGERPRINT.matches(request.selectionFingerprint)) {
            throw ApiException("validation_error", "Fingerprint selection tidak valid.", 422)
        }
        if (request.itemIds.isEmpty() || request.itemIds.size > BuildConfig.MAX_STAGE_ITEMS) {
            throw ApiException("validation_error", "Jumlah item staging tidak valid.", 422)
        }
        request.itemIds.forEach { validateId(it, "media") }
        if (idempotencyKey.isBlank() || idempotencyKey.length > 128) {
            throw ApiException("validation_error", "Idempotency key tidak valid.", 422)
        }
        val grant = grants.getApproved(sessionId, request.grantId)
        if (grant.grantVersion != request.grantVersion) {
            throw ApiException("grant_version_mismatch", "Versi grant tidak sesuai.", 409)
        }
        val fingerprint = fingerprint(request)
        val existing = stateStore.load(request.stageId)
        if (existing != null) {
            if (existing.sessionId != sessionId) {
                throw ApiException("agent_session_mismatch", "Stage bukan milik sesi aktif.", 409)
            }
            if (
                existing.idempotencyKey != idempotencyKey ||
                existing.requestFingerprint != fingerprint
            ) {
                throw ApiException("conflict", "Stage dengan ID yang sama sudah ada.", 409)
            }
            return existing
        }
        val queued = StageRecord(
            stageId = request.stageId,
            sessionId = sessionId,
            grantId = request.grantId,
            grantVersion = request.grantVersion,
            catalogId = request.catalogId,
            sourceKind = request.sourceKind,
            sourceId = request.sourceId,
            selectionFingerprint = request.selectionFingerprint,
            itemIds = request.itemIds,
            idempotencyKey = idempotencyKey,
            requestFingerprint = fingerprint,
            state = "queued",
            completedItems = 0,
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
        if (!executor.tryExecute { copy(queued) }) {
            val failed = queued.copy(state = "failed", errorCategory = "stage_queue_full")
            stateStore.save(failed)
            throw ApiException("stage_failed", "Antrian staging penuh.", 503, true)
        }
        return queued
    }

    @Synchronized
    fun status(sessionId: String, stageId: String): StageRecord {
        validateId(sessionId, "sesi")
        validateId(stageId, "stage")
        val record = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Stage tidak ditemukan.", 404)
        if (record.sessionId != sessionId) {
            throw ApiException("agent_session_mismatch", "Stage bukan milik sesi aktif.", 409)
        }
        return record
    }

    @Synchronized
    fun cleanup(sessionId: String, stageId: String): StageRecord {
        validateId(sessionId, "sesi")
        validateId(stageId, "stage")
        val record = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Stage tidak ditemukan.", 404)
        if (record.sessionId != sessionId) {
            throw ApiException("agent_session_mismatch", "Stage bukan milik sesi aktif.", 409)
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

    private fun copy(initial: StageRecord) {
        var current = initial.copy(state = "copying")
        persist(current)
        val artifacts = mutableListOf<StagedArtifact>()
        try {
            val filesRoot = File(stageRoot(initial.sessionId, initial.stageId), "files")
            if (!filesRoot.mkdirs() && !filesRoot.isDirectory) {
                throw ApiException("stage_failed", "Direktori staging tidak dapat dibuat.", 500)
            }
            var total = 0L
            initial.itemIds.forEachIndexed { index, mediaId ->
                if (cancelled) {
                    throw ApiException("stage_cancelled", "Staging dibatalkan.", 409)
                }
                val remaining = BuildConfig.MAX_STAGE_TOTAL_BYTES - total
                val maxBytes = minOf(BuildConfig.MAX_STAGE_FILE_BYTES, remaining)
                if (maxBytes <= 0) {
                    throw ApiException("media_too_large", "Staging melewati batas total.", 413)
                }
                val source = catalog.resolveForStaging(initial.sessionId, initial.grantId, mediaId)
                val artifactId = "artifact_$mediaId"
                val fileName = StagePathPolicy.safeStagedName(artifactId, source.displayName)
                val output = File(filesRoot, fileName)
                val copied = FileOutputStream(output).use { stream ->
                    catalog.copyForStaging(source, stream, maxBytes) { cancelled }
                }
                total += copied.sizeBytes
                artifacts.add(
                    StagedArtifact(
                        artifactId = artifactId,
                        mediaId = mediaId,
                        rank = index,
                        displayName = source.displayName,
                        relativePath = "${initial.sessionId}/${initial.stageId}/files/$fileName",
                        mimeType = source.mimeType,
                        sizeBytes = copied.sizeBytes,
                        sha256 = copied.sha256,
                    ),
                )
                current = current.copy(
                    completedItems = index + 1,
                    totalBytes = total,
                )
                persist(current)
            }
            current = current.copy(state = "finalizing")
            persist(current)
            val created = System.currentTimeMillis()
            val manifestBytes = ManifestBuilder.build(current, artifacts, created)
            val digest = sha256Hex(manifestBytes)
            val manifestFile = File(stageRoot(initial.sessionId, initial.stageId), "manifest.json")
            manifestFile.writeBytes(manifestBytes)
            current = current.copy(
                state = "completed",
                totalBytes = total,
                manifestRelativePath = "${initial.sessionId}/${initial.stageId}/manifest.json",
                manifestSizeBytes = manifestBytes.size.toLong(),
                manifestSha256 = digest,
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

    @Synchronized
    private fun persist(record: StageRecord) {
        stateStore.save(record)
    }

    private fun stageRoot(sessionId: String, stageId: String): File {
        val external = appContext.getExternalFilesDir(null)
            ?: throw ApiException("stage_failed", "Penyimpanan staging tidak tersedia.", 500)
        return File(external, "$ROOT_DIR/$sessionId/$stageId")
    }

    companion object {
        private const val STATE_DIR = "siksik_stage_state"
        private const val ROOT_DIR = "siksik_agent"
        private val FINGERPRINT = Regex("^[0-9a-f]{64}$")

        private fun validateId(value: String, label: String) {
            if (!SessionAuthenticator.SAFE_ID.matches(value)) {
                throw ApiException("validation_error", "ID $label tidak valid.", 422)
            }
        }

        private fun fingerprint(request: StageRequest): String {
            val material = buildString {
                append(request.stageId)
                append('\u001f')
                append(request.grantId)
                append('\u001f')
                append(request.grantVersion)
                append('\u001f')
                append(request.catalogId)
                append('\u001f')
                append(request.sourceKind)
                append('\u001f')
                append(request.sourceId)
                append('\u001f')
                append(request.selectionFingerprint)
                append('\u001f')
                append(request.itemIds.joinToString("\u001f"))
            }
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
