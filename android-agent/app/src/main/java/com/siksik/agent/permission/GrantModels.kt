package com.siksik.agent.permission

enum class GrantState(val wireName: String) {
    REQUESTED("requested"),
    AWAITING_USER("awaiting_user"),
    APPROVED("approved"),
    DENIED("denied"),
    CANCELLED("cancelled"),
    REVOKED("revoked"),
}

data class GrantRecord(
    val grantId: String,
    val sessionId: String,
    val scopeType: String,
    val effectiveScope: String?,
    val state: GrantState,
    val grantRef: String?,
    val approvedItemCount: Int?,
    val updatedAtEpochMs: Long,
    val grantVersion: Int = 1,
)

