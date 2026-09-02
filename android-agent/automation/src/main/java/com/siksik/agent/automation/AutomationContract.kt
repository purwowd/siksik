package com.siksik.agent.automation

import android.util.Log

data class AutomationLimits(
    val maxScrolls: Int,
    val maxScreenshots: Int,
    val launchTimeoutMs: Long,
    val stableWaitMs: Long,
) {
    init {
        require(maxScrolls in 0..400)
        require(maxScreenshots in 0..48)
        require(launchTimeoutMs in 1_000..60_000)
        require(stableWaitMs in 250..10_000)
    }
}

enum class SocialScope(val wireName: String) {
    OWN_PROFILE("own_profile"),
    OWN_POSTS("own_posts"),
    OWN_TWEETS("own_tweets"),
    OWN_STORY_ARCHIVE("own_story_archive"),
    OWN_COMMENTS("own_comments"),
    OWN_REPLIES("own_replies"),
}

enum class SocialCaptureMode {
    TEXT_ONLY,
    VISUAL,
}

enum class ScrollResult {
    MOVED,
    EXHAUSTED,
    FAILED,
}

enum class ScopeFailureClass(val wireName: String) {
    OBSERVATION("observation"),
    ACTION("action"),
    POSTCONDITION("postcondition"),
    EMPTY_CONTENT("empty_content"),
}

data class AutomationScopeProgress(
    val targetPackage: String,
    val scope: SocialScope,
    val stage: String,
    val state: String,
    val attempt: Int,
    val failureClass: ScopeFailureClass? = null,
    val reason: String? = null,
    val diagnosis: String? = null,
    val scrollCount: Int = 0,
    val screenshotCount: Int = 0,
)

internal const val INSTAGRAM_ARCHIVE_SCROLL_LIMIT = 3
internal const val INSTAGRAM_COMMENTS_EXHAUST_SCROLL_BUDGET = 200
internal const val SOCIAL_FEED_EXHAUST_SCROLL_BUDGET = 200
internal const val INSTAGRAM_COMMENTS_SCREENSHOT_BUDGET = 24

data class ScopeCapture(
    val stored: Boolean,
    val screenshotId: String?,
    val exhausted: Boolean = false,
)

data class AutomationOutcome(
    val targetPackage: String,
    val state: String,
    val reason: String?,
    val scrollCount: Int,
    val screenshotIds: List<String>,
    val durationMs: Long,
)

private data class ScopeAttemptOutcome(
    val completed: Boolean,
    val cancelled: Boolean = false,
    val reason: String? = null,
    val scrollCount: Int = 0,
    val screenshotIds: List<String> = emptyList(),
)

interface AutomationDriver {
    fun targetExists(targetPackage: String): Boolean
    fun launch(targetPackage: String): Boolean
    fun waitVisible(targetPackage: String, timeoutMs: Long): Boolean
    fun waitStable(timeoutMs: Long)
    fun isForeground(targetPackage: String): Boolean
    fun navigateToScope(targetPackage: String, scope: SocialScope): Boolean
    fun scrollForward(): Boolean
    fun scrollForwardResult(): ScrollResult =
        if (scrollForward()) ScrollResult.MOVED else ScrollResult.EXHAUSTED
    fun captureScope(scope: SocialScope, takeScreenshot: Boolean): ScopeCapture
    fun lastFailureReason(): String? = null
    fun lastFailureDiagnosis(): String? = null
    fun recommendedAdditionalCaptures(scope: SocialScope): Int? = null
    fun requireSignedInSession(targetPackage: String): Boolean = true
    fun recoverScope(
        targetPackage: String,
        scope: SocialScope,
        failedAttempt: Int,
        reason: String,
    ): Boolean = false
    fun completedScopeCheckpoints(targetPackage: String): Set<SocialScope> = emptySet()
    fun checkpointScope(
        targetPackage: String,
        scope: SocialScope,
        state: String,
        attempt: Int,
        failureClass: ScopeFailureClass?,
        reason: String?,
        scrollCount: Int,
        screenshotCount: Int,
    ): Boolean = true
    fun returnToAgent()
}

interface TargetNavigationStrategy {
    val targetPackage: String
    val scopes: List<SocialScope>
    val captureMode: SocialCaptureMode
        get() = SocialCaptureMode.VISUAL

    fun openScope(driver: AutomationDriver, scope: SocialScope): Boolean =
        driver.navigateToScope(targetPackage, scope)

    fun scroll(driver: AutomationDriver): ScrollResult = driver.scrollForwardResult()

    fun requiresVerifiedExhaustion(scope: SocialScope): Boolean = false

    fun scrollWeight(scope: SocialScope): Int = 1

    fun additionalCaptureCount(scope: SocialScope): Int = 0

    fun screenshotLimit(scope: SocialScope, totalLimit: Int): Int = totalLimit
}

class InstagramOwnAccountStrategy : TargetNavigationStrategy {
    override val targetPackage = "com.instagram.android"
    override val scopes = listOf(
        SocialScope.OWN_PROFILE,
        SocialScope.OWN_POSTS,
        SocialScope.OWN_STORY_ARCHIVE,
        SocialScope.OWN_COMMENTS,
    )

    override fun scrollWeight(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_POSTS -> 3
        SocialScope.OWN_STORY_ARCHIVE,
        SocialScope.OWN_COMMENTS,
        -> 1
        else -> 0
    }

    override fun additionalCaptureCount(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_STORY_ARCHIVE -> INSTAGRAM_ARCHIVE_SCROLL_LIMIT
        SocialScope.OWN_POSTS -> SOCIAL_FEED_EXHAUST_SCROLL_BUDGET
        SocialScope.OWN_COMMENTS -> INSTAGRAM_COMMENTS_EXHAUST_SCROLL_BUDGET
        else -> 0
    }

    override fun requiresVerifiedExhaustion(scope: SocialScope): Boolean =
        scope == SocialScope.OWN_COMMENTS

    override fun screenshotLimit(scope: SocialScope, totalLimit: Int): Int = when (scope) {
        SocialScope.OWN_PROFILE -> minOf(1, totalLimit)
        SocialScope.OWN_STORY_ARCHIVE -> minOf(
            INSTAGRAM_ARCHIVE_SCROLL_LIMIT + 1,
            (totalLimit - 1).coerceAtLeast(0),
        )
        SocialScope.OWN_COMMENTS -> minOf(
            INSTAGRAM_COMMENTS_SCREENSHOT_BUDGET,
            (totalLimit - 1).coerceAtLeast(0),
        )
        SocialScope.OWN_POSTS -> (totalLimit - 9).coerceAtLeast(0)
        else -> 0
    }
}

class XOwnAccountStrategy : TargetNavigationStrategy {
    override val targetPackage = "com.twitter.android"
    override val captureMode = SocialCaptureMode.TEXT_ONLY
    override val scopes = listOf(
        SocialScope.OWN_PROFILE,
        SocialScope.OWN_TWEETS,
        SocialScope.OWN_REPLIES,
    )

    override fun scrollWeight(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_TWEETS -> 3
        SocialScope.OWN_REPLIES -> 1
        else -> 0
    }

    override fun additionalCaptureCount(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_TWEETS,
        SocialScope.OWN_REPLIES,
        -> SOCIAL_FEED_EXHAUST_SCROLL_BUDGET
        else -> 0
    }

    override fun screenshotLimit(scope: SocialScope, totalLimit: Int): Int = 0

    override fun requiresVerifiedExhaustion(scope: SocialScope): Boolean =
        scope in setOf(SocialScope.OWN_TWEETS, SocialScope.OWN_REPLIES)
}

class FacebookOwnAccountStrategy : TargetNavigationStrategy {
    override val targetPackage = "com.facebook.katana"
    override val captureMode = SocialCaptureMode.TEXT_ONLY
    override val scopes = listOf(
        SocialScope.OWN_PROFILE,
        SocialScope.OWN_POSTS,
        SocialScope.OWN_COMMENTS,
    )

    override fun scrollWeight(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_POSTS -> 3
        SocialScope.OWN_COMMENTS -> 1
        else -> 0
    }

    override fun additionalCaptureCount(scope: SocialScope): Int = when (scope) {
        SocialScope.OWN_POSTS,
        SocialScope.OWN_COMMENTS,
        -> SOCIAL_FEED_EXHAUST_SCROLL_BUDGET
        else -> 0
    }

    override fun screenshotLimit(scope: SocialScope, totalLimit: Int): Int = 0

    override fun requiresVerifiedExhaustion(scope: SocialScope): Boolean =
        scope in setOf(SocialScope.OWN_POSTS, SocialScope.OWN_COMMENTS)
}

object TargetStrategyRegistry {
    private val strategies = listOf(
        XOwnAccountStrategy(),
        FacebookOwnAccountStrategy(),
        InstagramOwnAccountStrategy(),
    ).associateBy(TargetNavigationStrategy::targetPackage)

    val supportedPackages: Set<String> = strategies.keys

    fun resolve(targetPackage: String): TargetNavigationStrategy? = strategies[targetPackage]
}

class AutomationEngine(
    private val clock: () -> Long = System::currentTimeMillis,
) {
    fun execute(
        strategy: TargetNavigationStrategy,
        driver: AutomationDriver,
        limits: AutomationLimits,
        onProgress: (AutomationScopeProgress) -> Unit = {},
        isActive: () -> Boolean,
    ): AutomationOutcome {
        val started = clock()
        var scrollCount = 0
        val screenshots = mutableListOf<String>()
        var state = "complete"
        var reason: String? = null
        var completedScopes = 0
        var failedScopes = 0
        try {
            when {
                !driver.targetExists(strategy.targetPackage) -> {
                    state = "target_missing"
                    reason = "target_not_installed"
                }
                !isActive() -> {
                    state = "cancelled"
                    reason = "crawl_cancelled"
                }
                !driver.launch(strategy.targetPackage) -> {
                    state = "failed"
                    reason = "target_launch_failed"
                }
                !driver.waitVisible(strategy.targetPackage, limits.launchTimeoutMs) -> {
                    val launchReason = driver.lastFailureReason()
                    if (launchReason == "account_not_signed_in") {
                        state = "failed"
                        reason = launchReason
                    } else {
                        state = "timeout"
                        reason = launchReason ?: "target_launch_timeout"
                    }
                }
                else -> {
                    driver.waitStable(limits.stableWaitMs)
                    if (!driver.requireSignedInSession(strategy.targetPackage)) {
                        state = "failed"
                        reason = driver.lastFailureReason() ?: "account_not_signed_in"
                    } else {
                        val checkpointedScopes = try {
                            driver.completedScopeCheckpoints(strategy.targetPackage)
                        } catch (error: RuntimeException) {
                            Log.w(
                                AUTOMATION_LOG_TAG,
                                "event=social_checkpoint stage=load_failed " +
                                    "target=${strategy.targetPackage} " +
                                    "type=${error.javaClass.simpleName}",
                            )
                            emptySet()
                        }
                        strategy.scopes.forEachIndexed { index, scope ->
                            if (state == "cancelled") return@forEachIndexed
                            if (!isActive()) {
                                state = "cancelled"
                                reason = "crawl_cancelled"
                                return@forEachIndexed
                            }
                            if (scope in checkpointedScopes) {
                                completedScopes += 1
                                emitProgress(
                                    onProgress,
                                    AutomationScopeProgress(
                                        strategy.targetPackage,
                                        scope,
                                        stage = "checkpoint_restored",
                                        state = "complete",
                                        attempt = 0,
                                    ),
                                )
                                return@forEachIndexed
                            }
                            val scopeScreenshotLimit = strategy.screenshotLimit(
                                scope,
                                limits.maxScreenshots,
                            )
                            var scopeScreenshotCount = 0
                            var scopeScrollCount = 0
                            var scopeCompleted = false
                            var scopeFailureReason: String? = null
                            var attempt = 1
                            while (attempt <= MAX_SCOPE_ATTEMPTS && !scopeCompleted) {
                                Log.i(
                                    AUTOMATION_LOG_TAG,
                                    "event=social_scope stage=start target=${strategy.targetPackage} " +
                                        "scope=${scope.wireName} index=$index attempt=$attempt",
                                )
                                emitProgress(
                                    onProgress,
                                    AutomationScopeProgress(
                                        strategy.targetPackage,
                                        scope,
                                        stage = "attempt_started",
                                        state = "running",
                                        attempt = attempt,
                                        scrollCount = scopeScrollCount,
                                        screenshotCount = scopeScreenshotCount,
                                    ),
                                )
                                val attemptOutcome = executeScopeAttempt(
                                    strategy = strategy,
                                    driver = driver,
                                    scope = scope,
                                    limits = limits,
                                    isActive = isActive,
                                    remainingScreenshotLimit = (
                                        limits.maxScreenshots - screenshots.size
                                        ).coerceAtLeast(0),
                                    remainingScopeScreenshotLimit = (
                                        scopeScreenshotLimit - scopeScreenshotCount
                                        ).coerceAtLeast(0),
                                    attempt = attempt,
                                    onProgress = onProgress,
                                )
                                scrollCount += attemptOutcome.scrollCount
                                scopeScrollCount += attemptOutcome.scrollCount
                                screenshots.addAll(attemptOutcome.screenshotIds)
                                scopeScreenshotCount += attemptOutcome.screenshotIds.size
                                if (attemptOutcome.cancelled) {
                                    state = "cancelled"
                                    reason = "crawl_cancelled"
                                    saveScopeCheckpoint(
                                        driver,
                                        strategy.targetPackage,
                                        scope,
                                        "cancelled",
                                        attempt,
                                        ScopeFailureClass.ACTION,
                                        reason,
                                        scopeScrollCount,
                                        scopeScreenshotCount,
                                    )
                                    break
                                }
                                if (attemptOutcome.completed) {
                                    val checkpointSaved = saveScopeCheckpoint(
                                        driver,
                                        strategy.targetPackage,
                                        scope,
                                        "complete",
                                        attempt,
                                        null,
                                        null,
                                        scopeScrollCount,
                                        scopeScreenshotCount,
                                    )
                                    if (!checkpointSaved) {
                                        scopeFailureReason = "scope_checkpoint_rejected"
                                        emitProgress(
                                            onProgress,
                                            AutomationScopeProgress(
                                                strategy.targetPackage,
                                                scope,
                                                stage = "checkpoint_failed",
                                                state = "failed",
                                                attempt = attempt,
                                                failureClass = ScopeFailureClass.POSTCONDITION,
                                                reason = scopeFailureReason,
                                                scrollCount = scopeScrollCount,
                                                screenshotCount = scopeScreenshotCount,
                                            ),
                                        )
                                        break
                                    }
                                    scopeCompleted = true
                                    emitProgress(
                                        onProgress,
                                        AutomationScopeProgress(
                                            strategy.targetPackage,
                                            scope,
                                            stage = "checkpoint_saved",
                                            state = "complete",
                                            attempt = attempt,
                                            scrollCount = scopeScrollCount,
                                            screenshotCount = scopeScreenshotCount,
                                        ),
                                    )
                                    break
                                }
                                scopeFailureReason = attemptOutcome.reason
                                    ?: "scope_navigation_failed"
                                val failureClass = ScopeFailurePolicy.classify(scopeFailureReason)
                                logScopeFailure(
                                    strategy.targetPackage,
                                    scope,
                                    scopeFailureReason,
                                    attempt,
                                )
                                failureDiagnosis(driver)?.let { diagnosis ->
                                    Log.i(
                                        AUTOMATION_LOG_TAG,
                                        "event=social_scope stage=diagnosis target=${strategy.targetPackage} " +
                                            "scope=${scope.wireName} attempt=$attempt " +
                                            "reason=$scopeFailureReason diagnosis=$diagnosis",
                                    )
                                    emitProgress(
                                        onProgress,
                                        AutomationScopeProgress(
                                            strategy.targetPackage,
                                            scope,
                                            stage = "diagnosis",
                                            state = "running",
                                            attempt = attempt,
                                            failureClass = failureClass,
                                            reason = scopeFailureReason,
                                            diagnosis = diagnosis,
                                            scrollCount = scopeScrollCount,
                                            screenshotCount = scopeScreenshotCount,
                                        ),
                                    )
                                }
                                emitProgress(
                                    onProgress,
                                    AutomationScopeProgress(
                                        strategy.targetPackage,
                                        scope,
                                        stage = "attempt_failed",
                                        state = "failed",
                                        attempt = attempt,
                                        failureClass = failureClass,
                                        reason = scopeFailureReason,
                                        diagnosis = failureDiagnosis(driver),
                                        scrollCount = scopeScrollCount,
                                        screenshotCount = scopeScreenshotCount,
                                    ),
                                )
                                if (
                                    attempt >= MAX_SCOPE_ATTEMPTS ||
                                    !isActive() ||
                                    !ScopeFailurePolicy.isRetryable(scopeFailureReason)
                                ) {
                                    break
                                }
                                saveScopeCheckpoint(
                                    driver,
                                    strategy.targetPackage,
                                    scope,
                                    "retrying",
                                    attempt,
                                    failureClass,
                                    scopeFailureReason,
                                    scopeScrollCount,
                                    scopeScreenshotCount,
                                )
                                val recovered = try {
                                    driver.recoverScope(
                                        strategy.targetPackage,
                                        scope,
                                        attempt,
                                        scopeFailureReason,
                                    )
                                } catch (error: RuntimeException) {
                                    Log.w(
                                        AUTOMATION_LOG_TAG,
                                        "event=social_scope stage=recovery_error " +
                                            "target=${strategy.targetPackage} " +
                                            "scope=${scope.wireName} attempt=$attempt " +
                                            "type=${error.javaClass.simpleName}",
                                        error,
                                    )
                                    false
                                }
                                if (!recovered) {
                                    val recoveryReason = driver.lastFailureReason()
                                        ?: "scope_recovery_failed"
                                    Log.w(
                                        AUTOMATION_LOG_TAG,
                                        "event=social_scope stage=recovery_failed " +
                                            "target=${strategy.targetPackage} " +
                                            "scope=${scope.wireName} attempt=$attempt " +
                                            "reason=$recoveryReason",
                                    )
                                    emitProgress(
                                        onProgress,
                                        AutomationScopeProgress(
                                            strategy.targetPackage,
                                            scope,
                                            stage = "recovery_failed",
                                            state = "retrying",
                                            attempt = attempt,
                                            failureClass = ScopeFailureClass.ACTION,
                                            reason = recoveryReason,
                                            scrollCount = scopeScrollCount,
                                            screenshotCount = scopeScreenshotCount,
                                        ),
                                    )
                                    attempt += 1
                                    continue
                                }
                                Log.i(
                                    AUTOMATION_LOG_TAG,
                                    "event=social_scope stage=retry target=${strategy.targetPackage} " +
                                        "scope=${scope.wireName} next_attempt=${attempt + 1} " +
                                        "reason=$scopeFailureReason",
                                )
                                emitProgress(
                                    onProgress,
                                    AutomationScopeProgress(
                                        strategy.targetPackage,
                                        scope,
                                        stage = "state_recovered",
                                        state = "retrying",
                                        attempt = attempt,
                                        failureClass = failureClass,
                                        reason = scopeFailureReason,
                                        scrollCount = scopeScrollCount,
                                        screenshotCount = scopeScreenshotCount,
                                    ),
                                )
                                driver.waitStable(limits.stableWaitMs)
                                attempt += 1
                            }
                            if (state == "cancelled") return@forEachIndexed
                            if (scopeCompleted) {
                                completedScopes += 1
                            } else {
                                failedScopes += 1
                                val finalReason = scopeFailureReason ?: "scope_navigation_failed"
                                saveScopeCheckpoint(
                                    driver,
                                    strategy.targetPackage,
                                    scope,
                                    "failed",
                                    attempt.coerceAtMost(MAX_SCOPE_ATTEMPTS),
                                    ScopeFailurePolicy.classify(finalReason),
                                    finalReason,
                                    scopeScrollCount,
                                    scopeScreenshotCount,
                                )
                                if (reason == null) {
                                    reason = finalReason
                                }
                            }
                        }
                        if (state != "cancelled") {
                            when {
                                completedScopes == 0 -> {
                                    state = "failed"
                                    if (reason == null) reason = "scope_navigation_failed"
                                }
                                failedScopes > 0 || completedScopes != strategy.scopes.size -> {
                                    state = "partial"
                                    if (reason == null) reason = "scope_navigation_incomplete"
                                }
                            }
                        }
                    }
                }
            }
        } catch (_: SecurityException) {
            state = "failed"
            reason = "target_launch_denied"
        } catch (_: RuntimeException) {
            state = "failed"
            reason = "automation_runtime_failure"
        } finally {
            try {
                driver.returnToAgent()
            } catch (_: SecurityException) {
                if (state == "complete") state = "partial"
                reason = "agent_return_denied"
            } catch (_: RuntimeException) {
                if (state == "complete") state = "partial"
                reason = "agent_return_failed"
            }
        }
        return AutomationOutcome(
            strategy.targetPackage,
            state,
            reason,
            scrollCount,
            screenshots,
            (clock() - started).coerceAtLeast(0),
        )
    }

    private fun executeScopeAttempt(
        strategy: TargetNavigationStrategy,
        driver: AutomationDriver,
        scope: SocialScope,
        limits: AutomationLimits,
        isActive: () -> Boolean,
        remainingScreenshotLimit: Int,
        remainingScopeScreenshotLimit: Int,
        attempt: Int,
        onProgress: (AutomationScopeProgress) -> Unit,
    ): ScopeAttemptOutcome {
        var scrollCount = 0
        val screenshots = mutableListOf<String>()
        fun outcome(
            completed: Boolean,
            cancelled: Boolean = false,
            reason: String? = null,
        ) = ScopeAttemptOutcome(
            completed = completed,
            cancelled = cancelled,
            reason = reason,
            scrollCount = scrollCount,
            screenshotIds = screenshots,
        )
        fun shouldTakeScreenshot(): Boolean =
            strategy.captureMode == SocialCaptureMode.VISUAL &&
                screenshots.size < remainingScreenshotLimit &&
                screenshots.size < remainingScopeScreenshotLimit

        return try {
            if (!driver.isForeground(strategy.targetPackage)) {
                driver.waitStable(limits.stableWaitMs)
            }
            if (!driver.isForeground(strategy.targetPackage)) {
                return outcome(false, reason = "target_not_foreground")
            }
            if (!strategy.openScope(driver, scope)) {
                return outcome(
                    false,
                    reason = driver.lastFailureReason() ?: "scope_navigation_failed",
                )
            }
            driver.waitStable(limits.stableWaitMs)
            if (!driver.isForeground(strategy.targetPackage)) {
                return outcome(false, reason = "target_not_foreground")
            }
            val initial = driver.captureScope(scope, shouldTakeScreenshot())
            initial.screenshotId?.let(screenshots::add)
            if (!initial.stored) {
                return outcome(
                    false,
                    reason = driver.lastFailureReason() ?: "initial_capture_failed",
                )
            }
            Log.i(
                AUTOMATION_LOG_TAG,
                "event=social_scope stage=initial_captured " +
                    "target=${strategy.targetPackage} scope=${scope.wireName} " +
                    "screenshot=${initial.screenshotId != null}",
            )
            emitProgress(
                onProgress,
                AutomationScopeProgress(
                    strategy.targetPackage,
                    scope,
                    stage = "initial_captured",
                    state = "running",
                    attempt = attempt,
                    screenshotCount = screenshots.size,
                ),
            )
            if (initial.exhausted) return outcome(true)

            val requestedAdditionalCaptures = (
                driver.recommendedAdditionalCaptures(scope)
                    ?: strategy.additionalCaptureCount(scope)
                ).coerceAtLeast(0)
            var remainingForScope = requestedAdditionalCaptures.coerceAtMost(
                limits.maxScrolls.coerceAtLeast(0),
            )
            var scopeExhausted = false
            while (remainingForScope > 0) {
                remainingForScope -= 1
                if (!isActive()) return outcome(false, cancelled = true)
                if (!driver.isForeground(strategy.targetPackage)) {
                    return outcome(false, reason = "target_not_foreground")
                }
                when (strategy.scroll(driver)) {
                    ScrollResult.EXHAUSTED -> {
                        scopeExhausted = true
                        Log.i(
                            AUTOMATION_LOG_TAG,
                            "event=social_scope stage=exhausted " +
                                "target=${strategy.targetPackage} scope=${scope.wireName}",
                        )
                        break
                    }
                    ScrollResult.FAILED -> {
                        return outcome(
                            false,
                            reason = driver.lastFailureReason() ?: "scope_scroll_failed",
                        )
                    }
                    ScrollResult.MOVED -> Unit
                }
                scrollCount += 1
                driver.waitStable(limits.stableWaitMs)
                val capture = driver.captureScope(scope, shouldTakeScreenshot())
                capture.screenshotId?.let(screenshots::add)
                if (!capture.stored) {
                    return outcome(
                        false,
                        reason = driver.lastFailureReason() ?: "scope_capture_failed",
                    )
                }
                if (capture.exhausted) {
                    scopeExhausted = true
                    break
                }
                Log.i(
                    AUTOMATION_LOG_TAG,
                    "event=social_scope stage=scrolled target=${strategy.targetPackage} " +
                        "scope=${scope.wireName} attempt_scroll_count=$scrollCount " +
                        "screenshot=${capture.screenshotId != null}",
                )
                emitProgress(
                    onProgress,
                    AutomationScopeProgress(
                        strategy.targetPackage,
                        scope,
                        stage = "capture_scrolled",
                        state = "running",
                        attempt = attempt,
                        scrollCount = scrollCount,
                        screenshotCount = screenshots.size,
                    ),
                )
            }
            val fixedBudgetSatisfied = requestedAdditionalCaptures <= limits.maxScrolls &&
                remainingForScope == 0
            if (
                scopeExhausted ||
                (!strategy.requiresVerifiedExhaustion(scope) && fixedBudgetSatisfied)
            ) {
                outcome(true)
            } else {
                outcome(false, reason = "scope_exhaustion_unverified")
            }
        } catch (error: RuntimeException) {
            Log.w(
                AUTOMATION_LOG_TAG,
                "event=social_scope stage=runtime_error target=${strategy.targetPackage} " +
                    "scope=${scope.wireName} type=${error.javaClass.simpleName} " +
                    "message=${error.message.orEmpty().replace(' ', '_').take(160)}",
                error,
            )
            outcome(
                false,
                reason = driver.lastFailureReason() ?: "scope_runtime_error_${scope.wireName}",
            )
        }
    }

    private fun failureDiagnosis(driver: AutomationDriver): String? =
        driver.lastFailureDiagnosis()?.takeIf { it.isNotBlank() }

    private fun emitProgress(
        callback: (AutomationScopeProgress) -> Unit,
        progress: AutomationScopeProgress,
    ) {
        try {
            callback(progress)
        } catch (error: RuntimeException) {
            Log.w(
                AUTOMATION_LOG_TAG,
                "event=social_progress stage=emit_failed " +
                    "target=${progress.targetPackage} scope=${progress.scope.wireName} " +
                    "type=${error.javaClass.simpleName}",
            )
        }
    }

    private fun saveScopeCheckpoint(
        driver: AutomationDriver,
        targetPackage: String,
        scope: SocialScope,
        state: String,
        attempt: Int,
        failureClass: ScopeFailureClass?,
        reason: String?,
        scrollCount: Int,
        screenshotCount: Int,
    ): Boolean = try {
        driver.checkpointScope(
            targetPackage,
            scope,
            state,
            attempt,
            failureClass,
            reason,
            scrollCount,
            screenshotCount,
        )
    } catch (error: RuntimeException) {
        Log.w(
            AUTOMATION_LOG_TAG,
            "event=social_checkpoint stage=save_error target=$targetPackage " +
                "scope=${scope.wireName} state=$state type=${error.javaClass.simpleName}",
        )
        false
    }

    private fun logScopeFailure(
        targetPackage: String,
        scope: SocialScope,
        reason: String,
        attempt: Int,
    ) {
        Log.i(
            AUTOMATION_LOG_TAG,
            "event=social_scope stage=failed target=$targetPackage " +
                "scope=${scope.wireName} attempt=$attempt reason=$reason",
        )
    }

    private fun scrollBudget(
        total: Int,
        scopes: List<SocialScope>,
        target: SocialScope,
        strategy: TargetNavigationStrategy,
    ): Int {
        if (total <= 0 || scopes.isEmpty()) return 0
        val weights = scopes.map { strategy.scrollWeight(it).coerceAtLeast(0) }
        val totalWeight = weights.sum()
        if (totalWeight <= 0) return 0
        val targetIndex = scopes.indexOf(target)
        if (targetIndex < 0) return 0
        val base = (total * weights[targetIndex]) / totalWeight
        val allocated = weights.map { (total * it) / totalWeight }.sum()
        val remainder = total - allocated
        if (remainder <= 0) return base
        val order = scopes.indices.sortedWith(
            compareByDescending<Int> { (total * weights[it]) % totalWeight }
                .thenBy { it },
        )
        return base + if (targetIndex in order.take(remainder)) 1 else 0
    }

    companion object {
        private const val AUTOMATION_LOG_TAG = "SIKSIKAutomation"
        internal const val MAX_SCOPE_ATTEMPTS = 4
    }
}
