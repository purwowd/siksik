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
    fun recommendedAdditionalCaptures(scope: SocialScope): Int? = null
    fun requireSignedInSession(targetPackage: String): Boolean = true
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
                        strategy.scopes.forEachIndexed { index, scope ->
                        if (state == "cancelled") return@forEachIndexed
                        if (!isActive()) {
                            state = "cancelled"
                            reason = "crawl_cancelled"
                            return@forEachIndexed
                        }
                        try {
                            Log.i(
                                AUTOMATION_LOG_TAG,
                                "event=social_scope stage=start target=${strategy.targetPackage} " +
                                    "scope=${scope.wireName} index=$index",
                            )
                            if (!driver.isForeground(strategy.targetPackage)) {
                                driver.waitStable(limits.stableWaitMs)
                            }
                            if (!driver.isForeground(strategy.targetPackage)) {
                                failedScopes += 1
                                logScopeFailure(
                                    strategy.targetPackage,
                                    scope,
                                    "target_not_foreground",
                                )
                                return@forEachIndexed
                            }
                            if (!strategy.openScope(driver, scope)) {
                                failedScopes += 1
                                if (reason == null) reason = driver.lastFailureReason()
                                logScopeFailure(
                                    strategy.targetPackage,
                                    scope,
                                    driver.lastFailureReason() ?: "scope_navigation_failed",
                                )
                                return@forEachIndexed
                            }
                            driver.waitStable(limits.stableWaitMs)
                            if (!driver.isForeground(strategy.targetPackage)) {
                                failedScopes += 1
                                return@forEachIndexed
                            }
                            val scopeScreenshotLimit = strategy.screenshotLimit(
                                scope,
                                limits.maxScreenshots,
                            )
                            var scopeScreenshotCount = 0
                            val initial = driver.captureScope(
                                scope,
                                strategy.captureMode == SocialCaptureMode.VISUAL &&
                                    screenshots.size < limits.maxScreenshots &&
                                    scopeScreenshotCount < scopeScreenshotLimit,
                            )
                            initial.screenshotId?.let { screenshotId ->
                                screenshots.add(screenshotId)
                                scopeScreenshotCount += 1
                            }
                            if (!initial.stored) {
                                failedScopes += 1
                                if (reason == null) reason = driver.lastFailureReason()
                                logScopeFailure(
                                    strategy.targetPackage,
                                    scope,
                                    driver.lastFailureReason() ?: "initial_capture_failed",
                                )
                                return@forEachIndexed
                            }
                            Log.i(
                                AUTOMATION_LOG_TAG,
                                "event=social_scope stage=initial_captured " +
                                    "target=${strategy.targetPackage} scope=${scope.wireName} " +
                                    "screenshot=${initial.screenshotId != null}",
                            )
                            if (initial.exhausted) {
                                completedScopes += 1
                                return@forEachIndexed
                            }
                            val requestedAdditionalCaptures = (
                                driver.recommendedAdditionalCaptures(scope)
                                    ?: strategy.additionalCaptureCount(scope)
                                ).coerceAtLeast(0)
                            var remainingForScope = requestedAdditionalCaptures.coerceAtMost(
                                limits.maxScrolls.coerceAtLeast(0),
                            )
                            var scopeExhausted = false
                            var scopeFailed = false
                            while (remainingForScope > 0) {
                                remainingForScope -= 1
                                if (!isActive()) {
                                    state = "cancelled"
                                    reason = "crawl_cancelled"
                                    break
                                }
                                if (!driver.isForeground(strategy.targetPackage)) {
                                    failedScopes += 1
                                    scopeFailed = true
                                    if (reason == null) reason = "target_not_foreground"
                                    logScopeFailure(
                                        strategy.targetPackage,
                                        scope,
                                        "target_not_foreground",
                                    )
                                    break
                                }
                                when (strategy.scroll(driver)) {
                                    ScrollResult.EXHAUSTED -> {
                                        scopeExhausted = true
                                        Log.i(
                                            AUTOMATION_LOG_TAG,
                                            "event=social_scope stage=exhausted " +
                                                "target=${strategy.targetPackage} " +
                                                "scope=${scope.wireName}",
                                        )
                                        break
                                    }
                                    ScrollResult.FAILED -> {
                                        failedScopes += 1
                                        scopeFailed = true
                                        val scrollReason = driver.lastFailureReason()
                                            ?: "scope_scroll_failed"
                                        if (reason == null) reason = scrollReason
                                        logScopeFailure(
                                            strategy.targetPackage,
                                            scope,
                                            scrollReason,
                                        )
                                        break
                                    }
                                    ScrollResult.MOVED -> Unit
                                }
                                scrollCount += 1
                                driver.waitStable(limits.stableWaitMs)
                                val capture = driver.captureScope(
                                    scope,
                                    strategy.captureMode == SocialCaptureMode.VISUAL &&
                                        screenshots.size < limits.maxScreenshots &&
                                        scopeScreenshotCount < scopeScreenshotLimit,
                                )
                                capture.screenshotId?.let { screenshotId ->
                                    screenshots.add(screenshotId)
                                    scopeScreenshotCount += 1
                                }
                                if (!capture.stored) {
                                    failedScopes += 1
                                    scopeFailed = true
                                    val captureReason = driver.lastFailureReason()
                                        ?: "scope_capture_failed"
                                    if (reason == null) reason = captureReason
                                    logScopeFailure(
                                        strategy.targetPackage,
                                        scope,
                                        captureReason,
                                    )
                                    break
                                }
                                if (capture.exhausted) {
                                    scopeExhausted = true
                                    break
                                }
                                Log.i(
                                    AUTOMATION_LOG_TAG,
                                    "event=social_scope stage=scrolled " +
                                        "target=${strategy.targetPackage} scope=${scope.wireName} " +
                                        "scroll_count=$scrollCount screenshot=${capture.screenshotId != null}",
                                )
                            }
                            if (state == "cancelled") return@forEachIndexed
                            val fixedBudgetSatisfied =
                                requestedAdditionalCaptures <= limits.maxScrolls &&
                                    remainingForScope == 0
                            val scopeCompleted = scopeExhausted ||
                                (!strategy.requiresVerifiedExhaustion(scope) && fixedBudgetSatisfied)
                            if (!scopeFailed && !scopeCompleted) {
                                failedScopes += 1
                                scopeFailed = true
                                val exhaustionReason = "scope_exhaustion_unverified"
                                if (reason == null) reason = exhaustionReason
                                logScopeFailure(
                                    strategy.targetPackage,
                                    scope,
                                    exhaustionReason,
                                )
                            }
                            if (!scopeFailed) {
                                completedScopes += 1
                            }
                        } catch (error: RuntimeException) {
                            Log.w(
                                AUTOMATION_LOG_TAG,
                                "event=social_scope stage=runtime_error " +
                                    "target=${strategy.targetPackage} " +
                                    "scope=${scope.wireName} type=${error.javaClass.simpleName} " +
                                    "message=${error.message.orEmpty().replace(' ', '_').take(160)}",
                                error,
                            )
                            failedScopes += 1
                            if (reason == null) reason = driver.lastFailureReason()
                                ?: "scope_runtime_error_${scope.wireName}"
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

    private fun logScopeFailure(targetPackage: String, scope: SocialScope, reason: String) {
        Log.i(
            AUTOMATION_LOG_TAG,
            "event=social_scope stage=failed target=$targetPackage " +
                "scope=${scope.wireName} reason=$reason",
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
    }
}
