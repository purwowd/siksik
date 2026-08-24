package com.siksik.agent.staging

data class CrawlTransferRequest(
    val stageId: String,
    val crawlId: String,
    val selectionRevision: Int,
    val selectionFingerprint: String,
)

data class CrawlTransferRecord(
    val stageId: String,
    val sessionId: String,
    val crawlId: String,
    val selectionRevision: Int,
    val selectionFingerprint: String,
    val idempotencyKey: String,
    val requestFingerprint: String,
    val state: String,
    val completedRecords: Int,
    val totalRecords: Int,
    val artifactCount: Int,
    val totalBytes: Long,
    val manifestRelativePath: String?,
    val manifestSizeBytes: Long?,
    val manifestSha256: String?,
    val errorCategory: String?,
    val cleanupReceiptId: String?,
    val cleanupDeletedFiles: Int?,
    val cleanupAlreadyAbsent: Boolean?,
    val cleanupEpochMs: Long?,
)

data class CrawlTransferArtifact(
    val artifactId: String,
    val recordId: String,
    val sourceKind: String,
    val role: String,
    val attachmentId: String?,
    val relativePath: String,
    val mimeType: String,
    val sizeBytes: Long,
    val sha256: String,
)
