package com.siksik.agent.automation

internal object ScopeFailurePolicy {
    private val nonRetryableReasons = setOf(
        "account_not_signed_in",
        "crawl_cancelled",
        "navigation_deadline",
        "scope_checkpoint_rejected",
        "scope_ledger_rejected",
        "snapshot_store_rejected",
        "target_not_installed",
    )

    enum class RecoveryTier {
        IN_APP,
        RELAUNCH,
        FORCE_STOP,
    }

    fun classify(reason: String): ScopeFailureClass = when {
        reason.contains("extraction_failed") ||
            reason.contains("content_not_visible") ||
            reason.contains("ui_dump_failed") ||
            reason.contains("shell_dump") ||
            reason.contains("account_marker_missing") ||
            reason.contains("not_foreground") ||
            reason.contains("capture") -> ScopeFailureClass.OBSERVATION
        reason.contains("content_empty") ||
            reason.endsWith("_empty") -> ScopeFailureClass.EMPTY_CONTENT
        reason.contains("not_verified") ||
            reason.contains("verification") ||
            reason.contains("not_ready") ||
            reason == "scope_lost" ||
            reason == "scope_exhaustion_unverified" ||
            reason == "scope_checkpoint_rejected" -> ScopeFailureClass.POSTCONDITION
        reason.contains("navigation_stalled") ||
            reason.contains("_control_missing") ||
            reason.contains("not_signed_in") -> ScopeFailureClass.ACTION
        else -> ScopeFailureClass.ACTION
    }

    fun isRetryable(reason: String): Boolean =
        reason !in nonRetryableReasons &&
            !reason.endsWith("_navigation_deadline") &&
            !reason.endsWith("_deadline")

    fun recoveryTier(reason: String, failedAttempt: Int): RecoveryTier {
        val failureClass = classify(reason)
        return when (failureClass) {
            ScopeFailureClass.OBSERVATION -> when (failedAttempt) {
                1 -> RecoveryTier.IN_APP
                2 -> RecoveryTier.RELAUNCH
                else -> RecoveryTier.FORCE_STOP
            }
            ScopeFailureClass.ACTION -> when (failedAttempt) {
                1 -> RecoveryTier.IN_APP
                2 -> RecoveryTier.RELAUNCH
                else -> RecoveryTier.FORCE_STOP
            }
            ScopeFailureClass.POSTCONDITION -> when (failedAttempt) {
                1 -> RecoveryTier.IN_APP
                2 -> RecoveryTier.RELAUNCH
                else -> RecoveryTier.FORCE_STOP
            }
            ScopeFailureClass.EMPTY_CONTENT -> RecoveryTier.RELAUNCH
        }
    }
}
