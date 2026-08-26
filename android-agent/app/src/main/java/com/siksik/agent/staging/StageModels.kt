package com.siksik.agent.staging

data class StageRequest(
    val stageId: String,
    val grantId: String,
    val grantVersion: Int,
    val catalogId: String,
    val sourceKind: String,
    val sourceId: String,
    val selectionFingerprint: String,
    val itemIds: List<String>,
)

data class StageRecord(
    val stageId: String,
    val sessionId: String,
    val grantId: String,
    val grantVersion: Int,
    val catalogId: String,
    val sourceKind: String,
    val sourceId: String,
    val selectionFingerprint: String,
    val itemIds: List<String>,
    val idempotencyKey: String,
    val requestFingerprint: String,
    val state: String,
    val completedItems: Int,
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

data class StagedArtifact(
    val artifactId: String,
    val mediaId: String,
    val rank: Int,
    val displayName: String,
    val relativePath: String,
    val mimeType: String,
    val sizeBytes: Long,
    val sha256: String,
)

data class CrawlTransferRequest(
    val stageId: String,
    val crawlId: String,
    val selectionRevision: Int,
    val selectionFingerprint: String,
)

data class CrawlTransferRecord(
    val stageId: String,
    val crawlId: String,
    val sessionId: String,
    val state: String,
    val selectionRevision: Int,
    val selectionFingerprint: String,
    val policyFingerprint: String,
    val idempotencyKey: String,
    val requestFingerprint: String,
    val totalRecords: Int,
    val completedRecords: Int,
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

data class DirectStagedArtifact(
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
