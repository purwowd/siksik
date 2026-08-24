package com.siksik.agent.staging

import android.content.Context
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.selection.SelectionSet
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.media.MediaCatalog
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

class StagingManager(
    context: Context,
    private val mediaCatalog: MediaCatalog,
    private val grants: GrantGateway,
) {
    private val appContext = context.applicationContext
    private val externalBase = appContext.getExternalFilesDir(null)?.let {
        File(it, STAGING_DIRECTORY)
    } ?: throw ApiException("storage_unavailable", "Penyimpanan staging tidak tersedia.", 507)
    private val stateStore = StageStateStore(File(appContext.filesDir, STATE_DIRECTORY))
    private val running = ConcurrentHashMap.newKeySet<String>()
    private val cancelled = ConcurrentHashMap.newKeySet<String>()
    private val executor = ThreadPoolExecutor(
        1,
        1,
        30,
        TimeUnit.SECONDS,
        ArrayBlockingQueue(2),
        ThreadFactory { runnable ->
            Thread(runnable, "siksik-staging").apply { isDaemon = true }
        },
        ThreadPoolExecutor.AbortPolicy(),
    )

    init {
        if (!externalBase.mkdirs() && !externalBase.isDirectory) {
            throw ApiException("storage_unavailable", "Root staging tidak tersedia.", 507)
        }
    }

    @Synchronized
    fun start(sessionId: String, request: StageRequest, idempotencyKey: String): StageRecord {
        val selection = validateRequest(sessionId, request, idempotencyKey)
        val grant = grants.getApproved(sessionId, request.grantId)
        if (grant.grantVersion != request.grantVersion) {
            throw ApiException("conflict", "Versi grant berubah sebelum staging.", 409)
        }
        val normalized = request.copy(itemIds = selection.itemIds)
        val requestFingerprint = requestFingerprint(sessionId, normalized)
        val existing = stateStore.load(request.stageId)
        if (existing != null) {
            if (
                existing.sessionId != sessionId ||
                existing.idempotencyKey != idempotencyKey ||
                existing.requestFingerprint != requestFingerprint
            ) {
                throw ApiException("conflict", "ID stage atau idempotency key sudah dipakai.", 409)
            }
            if (existing.state in TERMINAL_STATES || running.contains(request.stageId)) {
                return existing
            }
            submit(request.stageId)
            return existing
        }
        val created = StageRecord(
            stageId = normalized.stageId,
            sessionId = sessionId,
            grantId = normalized.grantId,
            grantVersion = normalized.grantVersion,
            catalogId = normalized.catalogId,
            sourceKind = normalized.sourceKind,
            sourceId = normalized.sourceId,
            selectionFingerprint = normalized.selectionFingerprint,
            itemIds = normalized.itemIds,
            idempotencyKey = idempotencyKey,
            requestFingerprint = requestFingerprint,
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
        stateStore.save(created)
        submit(created.stageId)
        return created
    }

    @Synchronized
    fun status(sessionId: String, stageId: String): StageRecord {
        validateId(stageId, "stage")
        val record = stateStore.load(stageId)
            ?: throw ApiException("not_found", "Stage tidak ditemukan.", 404)
        if (record.sessionId != sessionId) {
            throw ApiException("agent_session_mismatch", "Stage milik sesi lain.", 409)
        }
        return record
    }

    @Synchronized
    fun cleanup(sessionId: String, stageId: String): StageRecord {
        var record = status(sessionId, stageId)
        if (record.cleanupReceiptId != null) return record
        if (running.contains(stageId)) {
            cancelled.add(stageId)
            throw ApiException("conflict", "Pembatalan stage masih berjalan.", 409, true)
        }
        val root = stageRoot(record)
        val alreadyAbsent = !root.exists()
        val deletedFiles = if (alreadyAbsent) 0 else root.walkTopDown().count(File::isFile)
        if (!alreadyAbsent && !root.deleteRecursively()) {
            throw ApiException("storage_unavailable", "Cleanup stage gagal.", 507)
        }
        record = record.copy(
            state = "cleaned",
            cleanupReceiptId = "cleanup_${UUID.randomUUID()}",
            cleanupDeletedFiles = deletedFiles,
            cleanupAlreadyAbsent = alreadyAbsent,
            cleanupEpochMs = System.currentTimeMillis(),
        )
        stateStore.save(record)
        return record
    }

    fun shutdown() {
        cancelled.addAll(running)
        executor.shutdownNow()
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
            throw ApiException("stage_failed", "Antrean staging penuh.", 503, true)
        }
    }

    private fun process(stageId: String) {
        var record = stateStore.load(stageId) ?: return
        val root = stageRoot(record)
        try {
            if (root.exists() && !root.deleteRecursively()) {
                throw ApiException("storage_unavailable", "Staging sebelumnya tidak dapat direset.", 507)
            }
            val filesRoot = File(root, FILES_DIRECTORY)
            if (!filesRoot.mkdirs() && !filesRoot.isDirectory) {
                throw ApiException("storage_unavailable", "Direktori staging tidak tersedia.", 507)
            }
            record = record.copy(
                state = "copying",
                completedItems = 0,
                totalBytes = 0,
                manifestRelativePath = null,
                manifestSizeBytes = null,
                manifestSha256 = null,
                errorCategory = null,
            )
            stateStore.save(record)
            val artifacts = mutableListOf<StagedArtifact>()
            var totalBytes = 0L
            record.itemIds.forEachIndexed { rank, mediaId ->
                ensureNotCancelled(stageId)
                val source = mediaCatalog.resolveForStaging(
                    record.sessionId,
                    record.grantId,
                    mediaId,
                )
                val artifactId = "artifact_${UUID.randomUUID()}"
                val stagedName = StagePathPolicy.safeStagedName(artifactId, source.displayName)
                val partial = StagePathPolicy.controlledChild(filesRoot, "$stagedName.partial")
                val final = StagePathPolicy.controlledChild(filesRoot, stagedName)
                val copied = FileOutputStream(partial).use { output ->
                    val result = mediaCatalog.copyForStaging(
                        source,
                        output,
                        BuildConfig.MAX_STAGE_FILE_BYTES,
                    ) { cancelled.contains(stageId) || Thread.currentThread().isInterrupted }
                    output.fd.sync()
                    result
                }
                totalBytes += copied.sizeBytes
                if (totalBytes > BuildConfig.MAX_STAGE_TOTAL_BYTES) {
                    throw ApiException("media_too_large", "Total stage melewati batas ukuran.", 413)
                }
                if (!partial.renameTo(final)) {
                    throw ApiException("storage_unavailable", "Finalisasi file stage gagal.", 507)
                }
                artifacts.add(
                    StagedArtifact(
                        artifactId = artifactId,
                        mediaId = mediaId,
                        rank = rank,
                        displayName = source.displayName,
                        relativePath = StagePathPolicy.relativePath(
                            record,
                            "$FILES_DIRECTORY/$stagedName",
                        ),
                        mimeType = source.mimeType,
                        sizeBytes = copied.sizeBytes,
                        sha256 = copied.sha256,
                    ),
                )
                record = record.copy(completedItems = rank + 1, totalBytes = totalBytes)
                stateStore.save(record)
            }
            ensureNotCancelled(stageId)
            record = record.copy(state = "finalizing")
            stateStore.save(record)
            val bytes = ManifestBuilder.build(record, artifacts, System.currentTimeMillis())
            val partialManifest = StagePathPolicy.controlledChild(root, "$MANIFEST_NAME.partial")
            val finalManifest = StagePathPolicy.controlledChild(root, MANIFEST_NAME)
            FileOutputStream(partialManifest).use { output ->
                output.write(bytes)
                output.fd.sync()
            }
            if (!partialManifest.renameTo(finalManifest)) {
                throw ApiException("storage_unavailable", "Finalisasi manifest gagal.", 507)
            }
            record = record.copy(
                state = "completed",
                manifestRelativePath = StagePathPolicy.relativePath(record, MANIFEST_NAME),
                manifestSizeBytes = bytes.size.toLong(),
                manifestSha256 = sha256(bytes),
            )
            stateStore.save(record)
        } catch (exception: ApiException) {
            stateStore.save(
                record.copy(
                    state = if (exception.code == "stage_cancelled") "cancelled" else "failed",
                    errorCategory = exception.code,
                ),
            )
        } catch (_: IOException) {
            stateStore.save(record.copy(state = "failed", errorCategory = "storage_unavailable"))
        } catch (_: RuntimeException) {
            stateStore.save(record.copy(state = "failed", errorCategory = "stage_failed"))
        }
    }

    private fun validateRequest(
        sessionId: String,
        request: StageRequest,
        key: String,
    ): SelectionSet {
        validateId(sessionId, "session")
        validateId(request.stageId, "stage")
        validateId(request.grantId, "grant")
        validateId(request.catalogId, "catalog")
        validateId(request.sourceId, "selection")
        if (request.sourceKind !in ALLOWED_SELECTION_SOURCES) {
            throw ApiException("validation_error", "Sumber pilihan tidak valid.", 422)
        }
        if (request.grantVersion < 1 || !SHA256.matches(request.selectionFingerprint)) {
            throw ApiException("validation_error", "Provenance stage tidak valid.", 422)
        }
        if (!SAFE_KEY.matches(key)) {
            throw ApiException("validation_error", "Idempotency-Key tidak valid.", 422)
        }
        return SelectionSet.validated(request.itemIds, BuildConfig.MAX_STAGE_ITEMS)
    }

    private fun validateId(value: String, label: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(value)) {
            throw ApiException("validation_error", "ID $label tidak valid.", 422)
        }
    }

    private fun stageRoot(record: StageRecord): File = StagePathPolicy.controlledChild(
        externalBase,
        "${record.sessionId}/${record.stageId}",
    )

    private fun ensureNotCancelled(stageId: String) {
        if (cancelled.contains(stageId) || Thread.currentThread().isInterrupted) {
            throw ApiException("stage_cancelled", "Staging dibatalkan.", 409)
        }
    }

    private fun requestFingerprint(sessionId: String, request: StageRequest): String {
        val value = buildString {
            append(sessionId)
            append('|')
            append(request.stageId)
            append('|')
            append(request.grantId)
            append('|')
            append(request.grantVersion)
            append('|')
            append(request.catalogId)
            append('|')
            append(request.sourceKind)
            append('|')
            append(request.sourceId)
            append('|')
            append(request.selectionFingerprint)
            request.itemIds.forEach {
                append('|')
                append(it)
            }
        }
        return sha256(value.toByteArray(Charsets.UTF_8))
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }

    companion object {
        private const val STAGING_DIRECTORY = "siksik_agent"
        private const val STATE_DIRECTORY = "siksik_stage_state"
        private const val FILES_DIRECTORY = "files"
        private const val MANIFEST_NAME = "manifest.json"
        private val SHA256 = Regex("^[0-9a-f]{64}$")
        private val SAFE_KEY = Regex("^[A-Za-z0-9_.:-]{8,128}$")
        private val TERMINAL_STATES = setOf("completed", "failed", "cancelled", "cleaned")
        private val ALLOWED_SELECTION_SOURCES = setOf("manual_selection", "selection_preview")
    }
}
