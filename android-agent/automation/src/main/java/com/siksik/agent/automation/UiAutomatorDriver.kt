package com.siksik.agent.automation

import android.app.UiAutomation
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Rect
import android.os.SystemClock
import android.util.Log
import android.view.InputDevice
import android.view.MotionEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.Configurator
import androidx.test.uiautomator.Direction
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import com.siksik.agent.BuildConfig
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.VisibleNodeRecord
import com.siksik.agent.source.communication.VisibleUiSnapshotter
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.ArrayDeque
import java.util.Locale
import java.util.UUID
private data class XTimelineRow(
    val nodes: List<VisibleNodeRecord>,
    val normalizedText: String,
    val contentHash: String,
)

private data class FbTimelineRow(
    val nodes: List<VisibleNodeRecord>,
    val normalizedText: String,
    val contentHash: String,
)

private enum class FacebookActivityPhase {
    NONE,
    COMMENTS,
    REACTIONS,
    COMBINED,
}

private data class InstagramProbeNode(
    val labels: List<String>,
    val resourceName: String,
    val bounds: Rect,
    val className: String,
    val clickable: Boolean,
    val scrollable: Boolean,
)

private data class InstagramSurfaceProbe(
    val ownProfile: Boolean,
    val profileSurface: Boolean,
    val otherProfile: Boolean,
    val profileTab: Rect?,
    val dismissTarget: Rect?,
    val accountMarker: String?,
    val nodeCount: Int,
    val signalScore: Int,
    val metricKinds: Int,
    val editVisible: Boolean,
    val shareVisible: Boolean,
) {
    companion object {
        val EMPTY = InstagramSurfaceProbe(
            false,
            false,
            false,
            null,
            null,
            null,
            0,
            0,
            0,
            false,
            false,
        )
    }
}

class UiAutomatorDriver(
    private val context: Context,
    private val device: UiDevice,
    private val uiAutomation: UiAutomation,
    private val sessionId: String,
    private val crawlId: String,
    private val notBeforeEpochMs: Long,
    debugSnapshotsEnabled: Boolean = false,
    private val navigationDeadlineAtMs: Long =
        System.currentTimeMillis() + DEFAULT_NAVIGATION_BUDGET_MS,
) : AutomationDriver, AutoCloseable {
    private val store = CommunicationCaptureStore(context)
    private val debugMapper = AutomationDebugMapper(
        context,
        device,
        sessionId,
        crawlId,
        debugSnapshotsEnabled,
    )
    private val verifiedOwnAccountPackages = mutableSetOf<String>()
    private var activePackage: String? = null
    private var activeScope: SocialScope? = null
    private var instagramOwnPostActive = false
    private var instagramArchiveListActive = false
    private var instagramSubpageActive = false
    private var xTimelineActive = false
    private var fbFeedActive = false
    private var fbActivityPhase = FacebookActivityPhase.NONE
    private var fbCommentsBoundaryReached = false
    private var instagramOwnAccountMarker: String? = null
    private var xOwnAccountMarker: String? = null
    private var fbOwnAccountMarker: String? = null
    private var instagramGridScrollBudget: Int? = null
    private var instagramArchiveScrollBudget: Int? = null
    private var instagramPostCountKnown = false
    private var instagramResolvedPostCount: Int? = null
    private var instagramPostEndReached = false
    private var instagramArchiveEndReached = false
    private var instagramLastPostCaptureSignature: String? = null
    private var instagramLastArchiveCaptureSignature: String? = null
    private var instagramCommentsViewportIndex: Int = 0
    private var instagramCommentsContentSignature: String? = null
    private var instagramCommentsStagnantScrolls: Int = 0
    private var instagramArchiveScrollsCompleted: Int = 0
    private val xStagnantCaptures = mutableMapOf<SocialScope, Int>()
    private val fbStagnantCaptures = mutableMapOf<SocialScope, Int>()
    private val xVisitedViewportSignatures = mutableMapOf<SocialScope, MutableSet<String>>()
    private val xCurrentViewportSignatures = mutableMapOf<SocialScope, String>()
    private val xStoredItemSignatures = mutableMapOf<SocialScope, MutableSet<String>>()
    private val fbVisitedViewportSignatures = mutableMapOf<SocialScope, MutableSet<String>>()
    private val fbCurrentViewportSignatures = mutableMapOf<SocialScope, String>()
    private val fbStoredItemSignatures = mutableMapOf<SocialScope, MutableSet<String>>()
    private val temporalBoundaryScopes = mutableSetOf<SocialScope>()
    private var failureReason: String? = null
    private var cachedShellDump: String? = null
    private var cachedShellDumpAtMs: Long = 0L
    /** After a hung dump, skip further dumps briefly so Infinix does not wedge the crawl. */
    private var shellDumpDisabledUntilMs: Long = 0L

    init {
        require(notBeforeEpochMs in 1 until System.currentTimeMillis()) {
            "social_time_scope_invalid"
        }
        Configurator.getInstance()
            .setWaitForIdleTimeout(UI_AUTOMATOR_IDLE_TIMEOUT_MS)
            .setWaitForSelectorTimeout(UI_AUTOMATOR_SELECTOR_TIMEOUT_MS)
    }

    override fun targetExists(targetPackage: String): Boolean =
        context.packageManager.getLaunchIntentForPackage(targetPackage) != null

    override fun launch(targetPackage: String): Boolean {
        deactivateScope(forceLedgerClear = true)
        resetInstagramCaptureProgress()
        instagramSubpageActive = false
        xVisitedViewportSignatures.clear()
        xCurrentViewportSignatures.clear()
        xStoredItemSignatures.clear()
        xStagnantCaptures.clear()
        fbVisitedViewportSignatures.clear()
        fbCurrentViewportSignatures.clear()
        fbStoredItemSignatures.clear()
        fbStagnantCaptures.clear()
        temporalBoundaryScopes.clear()
        fbOwnAccountMarker = null
        fbFeedActive = false
        fbActivityPhase = FacebookActivityPhase.NONE
        fbCommentsBoundaryReached = false
        failureReason = null
        shellDumpDisabledUntilMs = 0L
        invalidateShellDumpCache()
        debugMapper.startTarget(targetPackage)
        if (CommunicationPolicy.usesTextOnlyCrawlCover(targetPackage)) {
            if (!ensureTextOnlyCoverVisible(targetPackage)) {
                debugMapper.capture("text_only_cover_failed", status = "failed")
                return false
            }
        }
        return try {
            val intent = context.packageManager.getLaunchIntentForPackage(targetPackage)
                ?: return false
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            context.startActivity(intent)
            SystemClock.sleep(LAUNCH_VERIFY_MS)
            if (foregroundPackageName() != targetPackage && !shellLaunchTarget(targetPackage)) {
                debugMapper.capture("target_launch_failed", status = "failed")
                return false
            }
            debugMapper.capture("target_launch_requested")
            true
        } catch (_: Exception) {
            if (shellLaunchTarget(targetPackage)) {
                debugMapper.capture("target_launch_requested")
                true
            } else {
                debugMapper.capture("target_launch_failed", status = "failed")
                false
            }
        }
    }

    private fun shellLaunchTarget(targetPackage: String): Boolean {
        val component = launchComponentName(targetPackage) ?: return false
        return try {
            device.executeShellCommand(
                "am start -W -n $component " +
                    "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER",
            )
            true
        } catch (_: RuntimeException) {
            false
        }
    }

    private fun launchComponentName(targetPackage: String): String? {
        val component = context.packageManager.getLaunchIntentForPackage(targetPackage)?.component
            ?: return null
        return "${component.packageName}/${component.className}"
    }

    override fun waitVisible(targetPackage: String, timeoutMs: Long): Boolean {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < deadline) {
            dismissBlockingSystemPrompts()
            dismissCredentialOverlays()
            val foreground = foregroundPackageName()
            if (foreground == targetPackage) {
                SystemClock.sleep(350L)
                dismissBlockingSystemPrompts()
                dismissCredentialOverlays()
                if (foregroundPackageName() == targetPackage) {
                    if (looksLikeSignedOutSession(targetPackage)) {
                        failureReason = "account_not_signed_in"
                        debugMapper.capture("account_not_signed_in", status = "failed")
                        return false
                    }
                    debugMapper.capture("target_launch_visible")
                    return true
                }
            }
            SystemClock.sleep(FOREGROUND_POLL_MS)
        }
        dismissBlockingSystemPrompts()
        dismissCredentialOverlays()
        val foreground = foregroundPackageName()
        if (foreground == targetPackage && looksLikeSignedOutSession(targetPackage)) {
            failureReason = "account_not_signed_in"
            debugMapper.capture("account_not_signed_in", status = "failed")
            return false
        }
        if (
            foregroundLooksLikeCredentialManager() ||
            (foreground == targetPackage && looksLikeSignedOutSession(targetPackage))
        ) {
            failureReason = "account_not_signed_in"
            debugMapper.capture("account_not_signed_in", status = "failed")
            return false
        }
        val visible = foreground == targetPackage
        debugMapper.capture(
            if (visible) "target_launch_visible_late" else "target_launch_timeout",
            status = if (visible) "observed" else "failed",
        )
        return visible
    }

    override fun requireSignedInSession(targetPackage: String): Boolean {
        dismissBlockingSystemPrompts()
        dismissCredentialOverlays()
        if (foregroundLooksLikeCredentialManager()) {
            dismissCredentialOverlays()
        }
        if (!isForeground(targetPackage)) {
            SystemClock.sleep(400L)
            dismissCredentialOverlays()
        }
        if (foregroundLooksLikeCredentialManager()) {
            failureReason = "account_not_signed_in"
            debugMapper.capture("account_not_signed_in", status = "failed")
            return false
        }
        if (!isForeground(targetPackage)) {
            failureReason = "target_not_foreground"
            return false
        }
        if (looksLikeSignedOutSession(targetPackage)) {
            failureReason = "account_not_signed_in"
            debugMapper.capture("account_not_signed_in", status = "failed")
            return false
        }
        return true
    }

    override fun waitStable(timeoutMs: Long) {
        dismissBlockingSystemPrompts()
        SystemClock.sleep(timeoutMs)
        dismissBlockingSystemPrompts()
    }

    override fun isForeground(targetPackage: String): Boolean =
        foregroundPackageName() == targetPackage

    override fun navigateToScope(targetPackage: String, scope: SocialScope): Boolean {
        deactivateScope()
        failureReason = null
        ensureTextOnlyCoverVisible(targetPackage) || run {
            failureReason = failureReason ?: "text_only_cover_required"
            debugMapper.capture("scope_${scope.wireName}_cover_failed", scope, "failed")
            return false
        }
        debugMapper.capture("scope_${scope.wireName}_start", scope)
        if (!isForeground(targetPackage)) {
            failureReason = "target_not_foreground"
            debugMapper.capture("scope_${scope.wireName}_not_foreground", scope, "failed")
            return false
        }
        val navigated = when (targetPackage) {
            INSTAGRAM_PACKAGE -> navigateInstagram(scope)
            X_PACKAGE -> navigateX(scope)
            FACEBOOK_PACKAGE -> navigateFacebook(scope)
            else -> false
        }
        if (!navigated || !isForeground(targetPackage) || !scopeStillVisible(targetPackage, scope)) {
            if (failureReason == null) failureReason = "scope_verification_failed"
            debugMapper.capture(
                "scope_${scope.wireName}_${failureReason ?: "verification_failed"}",
                scope,
                "failed",
            )
            return false
        }
        if (!store.setVerifiedSocialScope(
                crawlId,
                targetPackage,
                scope.wireName,
                System.currentTimeMillis(),
            )
        ) {
            failureReason = "scope_ledger_rejected"
            return false
        }
        activePackage = targetPackage
        activeScope = scope
        debugMapper.capture("scope_${scope.wireName}_verified", scope, "verified")
        return true
    }

    override fun scrollForward(): Boolean = scrollForwardResult() == ScrollResult.MOVED

    override fun scrollForwardResult(): ScrollResult {
        val feedScroll = activeScope in setOf(
            SocialScope.OWN_POSTS,
            SocialScope.OWN_TWEETS,
            SocialScope.OWN_REPLIES,
            SocialScope.OWN_COMMENTS,
            SocialScope.OWN_STORY_ARCHIVE,
        )
        if (!feedScroll && navigationExpired()) return failScroll("navigation_deadline")
        if (activeScope == SocialScope.OWN_PROFILE) return ScrollResult.EXHAUSTED
        if (activeScope in temporalBoundaryScopes) return ScrollResult.EXHAUSTED
        if (activePackage == INSTAGRAM_PACKAGE) {
            when (activeScope) {
                SocialScope.OWN_POSTS -> {
                    if (instagramPostEndReached) return ScrollResult.EXHAUSTED
                    if (advanceInstagramOwnPost()) return ScrollResult.MOVED
                    return if (
                        instagramPostEndReached ||
                        (instagramOwnPostActive && scopeStillVisible(
                            INSTAGRAM_PACKAGE,
                            SocialScope.OWN_POSTS,
                        ))
                    ) {
                        ScrollResult.EXHAUSTED
                    } else {
                        failScroll("instagram_posts_scroll_failed")
                    }
                }
                SocialScope.OWN_STORY_ARCHIVE -> {
                    if (instagramArchiveScrollsCompleted >= INSTAGRAM_ARCHIVE_SCROLL_LIMIT) {
                        return ScrollResult.EXHAUSTED
                    }
                    return if (advanceInstagramArchiveList()) {
                        ScrollResult.MOVED
                    } else {
                        failScroll("instagram_archive_scroll_failed")
                    }
                }
                SocialScope.OWN_COMMENTS -> {
                    if (advanceInstagramComments()) return ScrollResult.MOVED
                    return if (isInstagramCommentsListSurface()) {
                        ScrollResult.EXHAUSTED
                    } else {
                        failScroll("instagram_comments_scroll_failed")
                    }
                }
                else -> Unit
            }
        }
        if (
            activePackage == X_PACKAGE &&
            activeScope in setOf(SocialScope.OWN_TWEETS, SocialScope.OWN_REPLIES)
        ) {
            return advanceXTimeline(requireNotNull(activeScope))
        }
        if (
            activePackage == FACEBOOK_PACKAGE &&
            activeScope in setOf(SocialScope.OWN_POSTS, SocialScope.OWN_COMMENTS)
        ) {
            return advanceFacebookFeed(requireNotNull(activeScope))
        }
        val width = device.displayWidth
        val height = device.displayHeight
        if (width <= 0 || height <= 0) return failScroll("display_bounds_invalid")
        return if (safeSwipe(
            width / 2,
            (height * 3) / 4,
            width / 2,
            height / 4,
            SWIPE_STEPS,
        )) {
            ScrollResult.MOVED
        } else {
            failScroll("scope_scroll_failed")
        }
    }

    override fun recommendedAdditionalCaptures(scope: SocialScope): Int? =
        when {
            scope == SocialScope.OWN_POSTS && activePackage == INSTAGRAM_PACKAGE ->
                instagramGridScrollBudget
            scope == SocialScope.OWN_STORY_ARCHIVE && activePackage == INSTAGRAM_PACKAGE ->
                INSTAGRAM_ARCHIVE_SCROLL_LIMIT
            scope == SocialScope.OWN_COMMENTS && activePackage == INSTAGRAM_PACKAGE ->
                INSTAGRAM_COMMENTS_EXHAUST_SCROLL_BUDGET
            scope in setOf(SocialScope.OWN_TWEETS, SocialScope.OWN_REPLIES) &&
                activePackage == X_PACKAGE ->
                SOCIAL_FEED_EXHAUST_SCROLL_BUDGET
            scope in setOf(SocialScope.OWN_POSTS, SocialScope.OWN_COMMENTS) &&
                activePackage == FACEBOOK_PACKAGE ->
                SOCIAL_FEED_EXHAUST_SCROLL_BUDGET
            else -> null
        }

    override fun captureScope(scope: SocialScope, takeScreenshot: Boolean): ScopeCapture {
        val feedCapture = scope in setOf(
            SocialScope.OWN_POSTS,
            SocialScope.OWN_TWEETS,
            SocialScope.OWN_REPLIES,
            SocialScope.OWN_COMMENTS,
            SocialScope.OWN_STORY_ARCHIVE,
        )
        if (!feedCapture && navigationExpired()) {
            failureReason = "navigation_deadline"
            return ScopeCapture(false, null)
        }
        activePackage?.let { pkg -> ensureTextOnlyCoverVisible(pkg) }
        val packageName = foregroundPackageName() ?: run {
            failureReason = "active_package_missing"
            return ScopeCapture(false, null)
        }
        if (
            packageName != activePackage ||
            scope != activeScope ||
            packageName !in CommunicationPolicy.supportedSocialTargets ||
            !scopeStillVisible(packageName, scope)
        ) {
            deactivateScope()
            failureReason = "scope_lost"
            return ScopeCapture(false, null)
        }
        debugMapper.capture("scope_${scope.wireName}_capture", scope, "capturing")
        if (
            packageName == INSTAGRAM_PACKAGE &&
            scope == SocialScope.OWN_COMMENTS
        ) {
            dismissInstagramCommentsFilterSheet()
        }
        val root = try {
            uiAutomation.rootInActiveWindow
        } catch (_: Exception) {
            null
        } ?: return ScopeCapture(false, null)
        if (root.packageName?.toString() != packageName) return ScopeCapture(false, null)
        val visibleNodes = snapshotVisibleNodes(root)
        if (
            packageName == X_PACKAGE &&
            scope in setOf(SocialScope.OWN_TWEETS, SocialScope.OWN_REPLIES)
        ) {
            return captureXTimelineScope(scope, visibleNodes, takeScreenshot)
        }
        if (
            packageName == FACEBOOK_PACKAGE &&
            scope in setOf(SocialScope.OWN_POSTS, SocialScope.OWN_COMMENTS)
        ) {
            return captureFacebookTimelineScope(scope, visibleNodes)
        }
        val nodes = scopedNodes(
            packageName,
            scope,
            visibleNodes,
        )
        val baseCaptureNodes = when {
            nodes.isNotEmpty() -> nodes
            packageName == INSTAGRAM_PACKAGE &&
                scope in setOf(SocialScope.OWN_PROFILE, SocialScope.OWN_POSTS) ->
                visibleNodes.filterNot(::instagramNavigationNoise)
            else -> nodes
        }
        val captureNodes = if (
            packageName == INSTAGRAM_PACKAGE && scope == SocialScope.OWN_PROFILE
        ) {
            mergeVisibleNodes(
                mergeVisibleNodes(baseCaptureNodes, instagramProfileEvidenceNodes(visibleNodes)),
                instagramProfileEvidenceFromUiDevice(),
            )
        } else if (
            packageName == INSTAGRAM_PACKAGE && scope == SocialScope.OWN_COMMENTS
        ) {
            mergeVisibleNodes(
                baseCaptureNodes.ifEmpty { visibleNodes },
                instagramCommentsEvidenceFromUiDevice(),
            )
        } else if (packageName == X_PACKAGE && scope == SocialScope.OWN_PROFILE) {
            mergeVisibleNodes(baseCaptureNodes, xProfileEvidenceFromUiDevice())
        } else if (packageName == FACEBOOK_PACKAGE && scope == SocialScope.OWN_PROFILE) {
            val evidence = facebookProfileEvidenceFromUiDevice()
            if (evidence.any { node ->
                    node.viewId?.endsWith("profile_display_name") == true ||
                        node.viewId?.endsWith("_stat") == true
                }
            ) {
                evidence
            } else {
                mergeVisibleNodes(baseCaptureNodes, evidence)
            }
        } else {
            baseCaptureNodes
        }
        if (
            packageName == FACEBOOK_PACKAGE &&
            scope == SocialScope.OWN_PROFILE &&
            !hasMeaningfulFacebookProfileCapture(captureNodes)
        ) {
            failureReason = "facebook_profile_content_missing"
            return ScopeCapture(false, null)
        }
        if (
            captureNodes.isEmpty() &&
            !(
                takeScreenshot &&
                    packageName == INSTAGRAM_PACKAGE &&
                    scope in setOf(
                        SocialScope.OWN_PROFILE,
                        SocialScope.OWN_POSTS,
                        SocialScope.OWN_STORY_ARCHIVE,
                        SocialScope.OWN_COMMENTS,
                    )
                )
        ) {
            failureReason = "owned_content_not_visible"
            return ScopeCapture(false, null)
        }
        val joinedCaptureText = CommunicationPolicy.joinedText(
            captureNodes.flatMap { node -> listOf(node.text, node.contentDescription) },
            BuildConfig.MAX_SMS_TEXT_LENGTH,
        )
        val commentsBodyText =
            if (packageName == INSTAGRAM_PACKAGE && scope == SocialScope.OWN_COMMENTS) {
                instagramCommentsBodyText(captureNodes, joinedCaptureText)
            } else {
                null
            }
        val normalizedText = when {
            !commentsBodyText.isNullOrBlank() && !joinedCaptureText.isNullOrBlank() ->
                CommunicationPolicy.joinedText(
                    listOf(commentsBodyText, joinedCaptureText),
                    BuildConfig.MAX_SMS_TEXT_LENGTH,
                )
            !commentsBodyText.isNullOrBlank() -> commentsBodyText
            else -> joinedCaptureText
        }
        val activityContext = captureNodes.firstNotNullOfOrNull(VisibleNodeRecord::className)
        val contentHash = if (
            packageName == INSTAGRAM_PACKAGE &&
            scope == SocialScope.OWN_COMMENTS
        ) {
            if (instagramCommentsFilterSheetVisible() ||
                (normalizedText?.contains("Filter by date", ignoreCase = true) == true)
            ) {
                dismissInstagramCommentsFilterSheet()
                Log.w(LOG_TAG, "event=instagram_comments_filter_sheet_skipped")
                return ScopeCapture(true, null)
            }
            val bodyForSignature = commentsBodyText ?: normalizedText
            val contentSignature = CommunicationPolicy.contentHash(
                packageName,
                scope.wireName,
                "comments_body",
                bodyForSignature,
            )
            if (contentSignature == instagramCommentsContentSignature) {
                instagramCommentsStagnantScrolls += 1
                if (instagramCommentsStagnantScrolls >= 2) {
                    return ScopeCapture(true, null, exhausted = true)
                }
            } else {
                instagramCommentsContentSignature = contentSignature
                instagramCommentsStagnantScrolls = 0
            }
            instagramCommentsViewportIndex += 1
            CommunicationPolicy.contentHash(
                packageName,
                scope.wireName,
                "comments_viewport_$instagramCommentsViewportIndex",
                bodyForSignature,
            )
        } else {
            CommunicationPolicy.visibleUiContentHash(
                packageName,
                scope.wireName,
                captureNodes,
            )
        }
        val temporal = temporalDecision(scope, captureNodes, normalizedText)
        if (temporal.outOfScope) {
            temporalBoundaryScopes.add(scope)
            return ScopeCapture(true, null)
        }
        val profileLinks = if (scope == SocialScope.OWN_PROFILE) {
            CommunicationPolicy.profileLinks(captureNodes)
        } else {
            emptyList()
        }
        // Empty IG grid: count is already on profile metrics; don't invent a "Postingan 1" item.
        if (
            packageName == INSTAGRAM_PACKAGE &&
            scope == SocialScope.OWN_POSTS &&
            instagramResolvedPostCount == 0
        ) {
            return ScopeCapture(true, null)
        }
        val screenshotId = if (takeScreenshot) {
            "shot_${UUID.randomUUID()}"
        } else {
            null
        }
        val screenshotFile = screenshotId?.let { id ->
            store.screenshotDirectory(sessionId, crawlId).resolve("$id.png")
        }
        val savedScreenshot = screenshotFile?.let { target ->
            takeScopedScreenshot(target, packageName, scope, captureNodes)
        } ?: false
        val retainedScreenshotId = screenshotId.takeIf { savedScreenshot }
        if (packageName == INSTAGRAM_PACKAGE) {
            updateInstagramCaptureProgress(
                scope,
                screenshotFile?.takeIf { savedScreenshot }?.let(::fileSha256) ?: contentHash,
            )
        }
        val now = System.currentTimeMillis()
        val stored = store.recordVisibleSnapshot(
            packageName = packageName,
            windowId = -1,
            activityContext = activityContext,
            eventType = AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
            eventTime = temporal.sourceTimeEpochMs ?: now,
            nodes = captureNodes.ifEmpty {
                listOf(
                    VisibleNodeRecord(
                        sequence = 0,
                        depth = 0,
                        text = normalizedText?.lineSequence()?.firstOrNull(),
                        contentDescription = null,
                        className = activityContext,
                        viewId = null,
                        left = 0,
                        top = 0,
                        right = device.displayWidth.coerceAtLeast(1),
                        bottom = device.displayHeight.coerceAtLeast(1),
                        clickable = false,
                        scrollable = false,
                    ),
                )
            },
            normalizedText = normalizedText,
            contentHash = contentHash,
            socialScope = scope.wireName,
            screenshotIds = listOfNotNull(retainedScreenshotId),
            now = now,
            profileLinks = profileLinks,
        )
        if (!stored) screenshotFile?.delete()
        if (!stored) failureReason = "snapshot_store_rejected"
        return ScopeCapture(stored, retainedScreenshotId.takeIf { stored })
    }

    private fun temporalDecision(
        scope: SocialScope,
        nodes: List<VisibleNodeRecord>,
        normalizedText: String?,
    ): SocialTimeDecision {
        if (scope == SocialScope.OWN_PROFILE) {
            return SocialTimeDecision(sourceTimeEpochMs = null, outOfScope = false)
        }
        return SocialTimeScope.evaluate(
            labels = buildList {
                nodes.forEach { node ->
                    add(node.text)
                    add(node.contentDescription)
                }
                add(normalizedText)
            },
            notBeforeEpochMs = notBeforeEpochMs,
            nowEpochMs = System.currentTimeMillis(),
        )
    }

    private fun captureXTimelineScope(
        scope: SocialScope,
        visibleNodes: List<VisibleNodeRecord>,
        takeScreenshot: Boolean,
    ): ScopeCapture {
        val marker = xOwnAccountMarker ?: run {
            failureReason = "x_account_marker_missing"
            return ScopeCapture(false, null)
        }
        var nodes = visibleNodes
        // UiDevice row first — a11y BFS often misses Compose tweet bodies; no shell dump.
        var rows = xTimelineRowsFromUiDevice(marker, scope)
        if (rows.isEmpty()) {
            rows = xOwnedTimelineRows(nodes, marker, scope)
        }
        if (rows.isEmpty()) {
            rows = xTimelineRowsFromShellDump(marker, scope)
        }
        repeat(X_CONTENT_WAIT_ATTEMPTS) {
            if (rows.isNotEmpty()) return@repeat
            if (hasLabelContaining(X_EMPTY_TIMELINE_LABELS)) {
                return ScopeCapture(stored = true, screenshotId = null, exhausted = true)
            }
            if (navigationExpired()) return@repeat
            SystemClock.sleep(X_SCROLL_IDLE_MS)
            rows = xTimelineRowsFromUiDevice(marker, scope)
            if (rows.isNotEmpty()) return@repeat
            rows = xTimelineRowsFromShellDump(marker, scope)
            if (rows.isNotEmpty()) return@repeat
            val root = try {
                uiAutomation.rootInActiveWindow
            } catch (_: Exception) {
                null
            } ?: return@repeat
            if (root.packageName?.toString() != X_PACKAGE) return@repeat
            nodes = snapshotVisibleNodes(root)
            rows = xOwnedTimelineRows(nodes, marker, scope)
        }
        if (rows.isEmpty() && hasLabelContaining(X_EMPTY_TIMELINE_LABELS)) {
            return ScopeCapture(stored = true, screenshotId = null, exhausted = true)
        }
        if (rows.isEmpty()) {
            buildXTimelineRow(nodes, marker, scope)?.let { rows = listOf(it) }
        }
        if (rows.isEmpty()) {
            failureReason = "x_own_content_not_visible"
            Log.w(
                LOG_TAG,
                "event=x_timeline_empty scope=${scope.wireName} marker=$marker " +
                    "a11y_nodes=${nodes.size}",
            )
            return ScopeCapture(false, null)
        }
        // TEXT_ONLY: never attach screenshots even if caller asks.
        val evaluatedRows = rows.map { row ->
            row to temporalDecision(scope, row.nodes, row.normalizedText)
        }
        val eligibleRows = evaluatedRows.filterNot { (_, temporal) -> temporal.outOfScope }
        if (evaluatedRows.isNotEmpty() && eligibleRows.isEmpty()) {
            temporalBoundaryScopes.add(scope)
            return ScopeCapture(true, null)
        }
        val known = xStoredItemSignatures.getOrPut(scope) { mutableSetOf() }
        var storedAny = false
        var rejected = false
        val now = System.currentTimeMillis()
        eligibleRows.forEachIndexed { index, (row, temporal) ->
            if (row.contentHash in known) return@forEachIndexed
            val stored = store.recordVisibleSnapshot(
                packageName = X_PACKAGE,
                windowId = -1,
                activityContext = row.nodes.firstNotNullOfOrNull(VisibleNodeRecord::className),
                eventType = AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
                eventTime = temporal.sourceTimeEpochMs ?: now + index,
                nodes = row.nodes.take(BuildConfig.MAX_UI_NODES),
                normalizedText = row.normalizedText,
                contentHash = row.contentHash,
                socialScope = scope.wireName,
                screenshotIds = emptyList(),
                now = now + index,
            )
            if (stored) {
                known.add(row.contentHash)
                storedAny = true
            } else {
                rejected = true
                Log.w(
                    LOG_TAG,
                    "event=x_timeline_store_rejected scope=${scope.wireName} " +
                        "hash=${row.contentHash.take(12)} text_len=${row.normalizedText.length}",
                )
            }
        }
        val alreadyKnown = eligibleRows.all { (row, _) -> row.contentHash in known }
        val accepted = storedAny || alreadyKnown
        if (!accepted) {
            failureReason = if (rejected) "snapshot_store_rejected" else "x_own_content_not_visible"
        } else {
            Log.i(
                LOG_TAG,
                "event=x_timeline_stored scope=${scope.wireName} rows=${rows.size} " +
                    "new=$storedAny mode=text_only",
            )
        }
        return ScopeCapture(
            accepted,
            null,
            exhausted = accepted && timelineStagnantExhausted(xStagnantCaptures, scope, storedAny),
        )
    }

    private fun timelineStagnantExhausted(
        stagnantByScope: MutableMap<SocialScope, Int>,
        scope: SocialScope,
        storedAny: Boolean,
    ): Boolean {
        if (storedAny) {
            stagnantByScope[scope] = 0
            return false
        }
        val count = (stagnantByScope[scope] ?: 0) + 1
        stagnantByScope[scope] = count
        return count >= TIMELINE_STAGNANT_SCROLL_LIMIT
    }

    override fun lastFailureReason(): String? = failureReason

    private fun navigateInstagram(scope: SocialScope): Boolean {
        if (scope == SocialScope.OWN_COMMENTS) {
            return navigateInstagramComments()
        }
        if (scope == SocialScope.OWN_STORY_ARCHIVE) {
            // Infinix/Samsung still open Archive from own profile + hamburger.
            // Xiaomi may fail the profile tab; settings deeplink remains the fallback.
            if (!openInstagramOwnProfile() && !instagramOptionsMenuAlreadyOpen()) {
                Log.i(LOG_TAG, "event=instagram_archive_skip_profile_try_settings")
            }
            return openInstagramStoryArchive()
        }
        if (!openInstagramOwnProfile()) return false
        return when (scope) {
            SocialScope.OWN_PROFILE -> true
            SocialScope.OWN_POSTS -> openInstagramOwnPost()
            else -> false
        }
    }

    private fun navigateInstagramComments(): Boolean {
        shellDumpDisabledUntilMs = 0L
        invalidateShellDumpCache()
        if (instagramOnStoriesArchive()) {
            if (!leaveInstagramArchiveForComments()) return false
        } else if (instagramCommentsListChromeVisible()) {
            markInstagramOwnAccountVerified()
            return true
        }
        if (instagramOptionsMenuAlreadyOpen()) {
            markInstagramOwnAccountVerified()
            return openInstagramComments()
        }
        // Xiaomi: profile tab inject is blocked; comments still open via settings deeplink.
        if (!openInstagramOwnProfile() && !instagramOptionsMenuAlreadyOpen()) {
            Log.i(LOG_TAG, "event=instagram_comments_skip_profile_try_settings")
        }
        return openInstagramComments()
    }

    private fun snapshotVisibleNodes(root: android.view.accessibility.AccessibilityNodeInfo): List<VisibleNodeRecord> =
        safeUi(emptyList()) { VisibleUiSnapshotter.snapshot(root) }

    private fun mergeVisibleNodes(
        primary: List<VisibleNodeRecord>,
        evidence: List<VisibleNodeRecord>,
    ): List<VisibleNodeRecord> = (primary + evidence)
        .distinctBy { node ->
            listOf(
                node.text,
                node.contentDescription,
                node.viewId,
                node.left,
                node.top,
                node.right,
                node.bottom,
            )
        }
        .take(BuildConfig.MAX_UI_NODES)
        .mapIndexed { index, node -> node.copy(sequence = index) }

    private fun instagramProfileEvidenceNodes(
        nodes: List<VisibleNodeRecord>,
    ): List<VisibleNodeRecord> = nodes.filter { node ->
        node.viewId.orEmpty().substringAfterLast('/') in INSTAGRAM_PROFILE_EVIDENCE_RESOURCES
    }

    private fun instagramCommentsEvidenceFromUiDevice(): List<VisibleNodeRecord> = safeUi(emptyList()) {
        val window = activeWindowBounds()
        val minimumTop = (window.top + window.height() / 8).coerceAtLeast(window.top)
        val maximumBottom = (window.bottom - window.height() / 10).coerceAtMost(window.bottom)
        val out = ArrayList<VisibleNodeRecord>(64)
        var seq = 0
        val seen = HashSet<String>()
        fun consider(obj: UiObject2) {
            val text = obj.text?.toString()?.trim().orEmpty()
            val desc = obj.contentDescription?.toString()?.trim().orEmpty()
            if (text.isBlank() && desc.isBlank()) return
            if (instagramCommentsChromeLabel(text) || instagramCommentsChromeLabel(desc)) return
            val b = safeBounds(obj) ?: return
            if (b.bottom < minimumTop || b.top > maximumBottom) return
            if (b.height() <= 0 || b.width() <= 0) return
            val key = listOf(text, desc, b.left, b.top, b.right, b.bottom).joinToString("|")
            if (!seen.add(key)) return
            out += VisibleNodeRecord(
                sequence = seq++,
                depth = 0,
                text = text.takeIf { it.isNotBlank() },
                contentDescription = desc.takeIf { it.isNotBlank() },
                className = obj.className?.toString() ?: "android.widget.TextView",
                viewId = obj.resourceName,
                left = b.left,
                top = b.top,
                right = b.right,
                bottom = b.bottom,
                clickable = obj.isClickable,
                scrollable = obj.isScrollable,
            )
        }
        for (obj in device.findObjects(By.clazz("android.widget.TextView"))) {
            consider(obj)
            if (out.size >= 80) break
        }
        if (out.size < 8) {
            for (obj in device.findObjects(By.clickable(true))) {
                consider(obj)
                if (out.size >= 80) break
            }
        }
        out
    }

    private fun instagramCommentsBodyText(
        nodes: List<VisibleNodeRecord>,
        fallback: String?,
    ): String? {
        val parts = nodes.asSequence()
            .flatMap { node -> sequenceOf(node.text, node.contentDescription) }
            .mapNotNull { value -> value?.trim()?.takeIf { it.isNotEmpty() } }
            .filterNot(::instagramCommentsChromeLabel)
            .distinct()
            .toList()
        if (parts.isNotEmpty()) {
            return CommunicationPolicy.joinedText(parts, BuildConfig.MAX_SMS_TEXT_LENGTH)
        }
        if (fallback.isNullOrBlank()) return null
        val filtered = fallback.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() && !instagramCommentsChromeLabel(it) }
            .distinct()
            .toList()
        return CommunicationPolicy.joinedText(filtered, BuildConfig.MAX_SMS_TEXT_LENGTH)
    }

    private fun instagramCommentsChromeLabel(value: String): Boolean {
        val normalized = value.trim().lowercase(Locale.ROOT)
        if (normalized.isEmpty()) return true
        if (normalized in INSTAGRAM_COMMENTS_CHROME_LABELS) return true
        return INSTAGRAM_COMMENTS_CHROME_FRAGMENTS.any { fragment ->
            normalized == fragment || normalized.startsWith("$fragment ")
        }
    }

    private fun instagramProfileEvidenceFromUiDevice(): List<VisibleNodeRecord> = safeUi(emptyList()) {
        val out = ArrayList<VisibleNodeRecord>(12)
        var seq = 0
        fun add(res: String, obj: UiObject2) {
            val text = obj.text?.toString()?.trim()
            val desc = obj.contentDescription?.toString()?.trim()
            if (text.isNullOrBlank() && desc.isNullOrBlank()) return
            val b = safeBounds(obj) ?: return
            out += VisibleNodeRecord(
                sequence = seq++,
                depth = 0,
                text = text?.takeIf { it.isNotBlank() },
                contentDescription = desc?.takeIf { it.isNotBlank() },
                className = "android.widget.TextView",
                viewId = "$INSTAGRAM_PACKAGE:id/$res",
                left = b.left,
                top = b.top,
                right = b.right,
                bottom = b.bottom,
                clickable = false,
                scrollable = false,
            )
        }
        for (res in INSTAGRAM_USERNAME_RESOURCES) {
            device.findObject(By.res(INSTAGRAM_PACKAGE, res))?.let { add(res, it) }
        }
        for (res in listOf(
            "profile_header_familiar_post_count_value",
            "profile_header_post_count_front_familiar",
            "profile_header_familiar_followers_value",
            "profile_header_followers_stacked_familiar",
            "profile_header_familiar_following_value",
            "profile_header_following_stacked_familiar",
            "profile_header_website",
            "profile_header_link",
            "profile_header_bio",
        )) {
            device.findObject(By.res(INSTAGRAM_PACKAGE, res))?.let { add(res, it) }
        }
        out
    }

    /** Pull X following/followers counts via UiDevice — a11y BFS often drops value_text_1. */
    private fun xProfileEvidenceFromUiDevice(): List<VisibleNodeRecord> = safeUi(emptyList()) {
        val out = ArrayList<VisibleNodeRecord>(8)
        var seq = 0
        fun addLabeled(res: String, text: String?, desc: String?, bounds: Rect?) {
            if (text.isNullOrBlank() && desc.isNullOrBlank()) return
            val b = bounds ?: return
            out += VisibleNodeRecord(
                sequence = seq++,
                depth = 0,
                text = text?.takeIf { it.isNotBlank() },
                contentDescription = desc?.takeIf { it.isNotBlank() },
                className = "android.widget.TextView",
                viewId = "$X_PACKAGE:id/$res",
                left = b.left,
                top = b.top,
                right = b.right,
                bottom = b.bottom,
                clickable = false,
                scrollable = false,
            )
        }
        for (stat in listOf("following_stat", "followers_stat")) {
            val container = device.findObject(By.res(X_PACKAGE, stat)) ?: continue
            val value = container.findObject(By.res(X_PACKAGE, "value_text_1"))
                ?: container.findObject(By.res(X_PACKAGE, "value"))
            val label = container.findObject(By.res(X_PACKAGE, "name"))
            if (value != null) {
                addLabeled(
                    "value_text_1",
                    value.text?.toString(),
                    value.contentDescription?.toString(),
                    safeBounds(value),
                )
            }
            if (label != null) {
                val labelRes = if (stat.startsWith("following")) "following_stat" else "followers_stat"
                addLabeled(
                    labelRes,
                    label.text?.toString(),
                    label.contentDescription?.toString(),
                    safeBounds(label),
                )
            }
        }
        out
    }

    private fun openInstagramOwnProfile(): Boolean {
        val started = SystemClock.elapsedRealtime()
        val localDeadline = minOf(
            navigationDeadlineAtMs,
            System.currentTimeMillis() + PROFILE_NAVIGATION_BUDGET_MS,
        )
        logInstagramProfileNavigation("start", started)
        debugMapper.capture("instagram_profile_before_navigation", activeScope)
        dismissInstagramProfileCoachmarks()
        if (isInstagramArchiveListSurface()) {
            instagramSubpageActive = true
        }
        if (instagramOptionsMenuAlreadyOpen()) {
            markInstagramOwnAccountVerified()
            debugMapper.capture("instagram_profile_settings_already_open", activeScope)
            repeat(3) {
                clickInstagramSubpageBack()
                SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
                if (instagramOwnProfileVisibleOnDevice()) {
                    return acceptInstagramOwnProfile(started, "restored_from_settings")
                }
                if (!instagramOptionsMenuAlreadyOpen()) return@repeat
            }
        }
        scrollInstagramProfileToHeaderIfNeeded()
        // Already on own profile — UiDevice selectors first (probe-proven).
        if (instagramOwnProfileVisibleOnDevice()) {
            return acceptInstagramOwnProfile(started, "already_on_own_profile")
        }
        if (instagramSubpageActive) {
            var restored = false
            for (step in 0 until MAX_INSTAGRAM_PROFILE_BACK_STEPS) {
                dismissInstagramCommentsFilterSheet()
                clickInstagramSubpageBack()
                SystemClock.sleep(PROFILE_PROBE_INTERVAL_MS)
                if (instagramOwnProfileVisibleOnDevice()) {
                    return acceptInstagramOwnProfile(started, "restored_from_subpage")
                }
                // Reached main tabs but not own profile yet — continue via profile tab.
                if (instagramBottomTabsVisible()) {
                    logInstagramProfileNavigation("subpage_back_to_tabs", started)
                    restored = true
                    break
                }
            }
            // Never hard-fail here: Comments/Your activity needs >2 backs; fall through
            // to profile-tab recovery (same path as cold start).
            instagramSubpageActive = false
            if (!restored) {
                logInstagramProfileNavigation("subpage_restore_fallback_tab", started)
            }
        }
        val initialClicked = clickInstagramProfileTab()
        logInstagramProfileNavigation(
            if (initialClicked) "profile_tab_clicked" else "profile_tab_rejected",
            started,
        )
        SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
        debugMapper.capture(
            if (initialClicked) "instagram_profile_after_tab_click" else
                "instagram_profile_tab_click_failed",
            activeScope,
            if (initialClicked) "observed" else "failed",
        )
        repeat(PROFILE_PROOF_ATTEMPTS) { attempt ->
            if (System.currentTimeMillis() >= localDeadline || navigationExpired()) {
                // Infinix often finishes loading right as the budget ends — one last proof.
                return finalizeInstagramOwnProfileAfterWait(started)
            }
            SystemClock.sleep(PROFILE_PROBE_INTERVAL_MS)
            if (attempt % 3 == 1) {
                dismissInstagramProfileCoachmarks()
                scrollInstagramProfileToHeaderIfNeeded()
            }
            val deviceProof = instagramOwnProfileVisibleOnDevice()
            val probe = instagramSurfaceProbe()
            Log.i(
                LOG_TAG,
                "event=instagram_profile_probe attempt=${attempt + 1} " +
                    "nodes=${probe.nodeCount} own=${probe.ownProfile} " +
                    "other=${probe.otherProfile} signals=${probe.signalScore} " +
                    "metrics=${probe.metricKinds} edit=${probe.editVisible} " +
                    "share=${probe.shareVisible} uidevice=$deviceProof",
            )
            if (deviceProof || probe.ownProfile) {
                return acceptInstagramOwnProfile(started, "verified", probe)
            }
            if (attempt == PROFILE_PROOF_ATTEMPTS - 1) return@repeat
            val dismissTarget = probe.dismissTarget
            if (dismissTarget != null) {
                val dismissed = safeClickPoint(dismissTarget.centerX(), dismissTarget.centerY())
                logInstagramProfileNavigation(
                    if (dismissed) "dialog_dismissed" else "dialog_dismiss_failed",
                    started,
                )
                if (dismissed) {
                    SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
                    clickInstagramProfileTab()
                    debugMapper.capture("instagram_profile_after_dialog_recovery", activeScope)
                    return@repeat
                }
            }
            if (attempt in PROFILE_RECOVERY_ATTEMPTS) {
                val profileTab = probe.profileTab
                val recovered = if (profileTab != null) {
                    clickInstagramProfileTab() ||
                        safeClickPoint(profileTab.centerX(), profileTab.centerY())
                } else {
                    safePressBack()
                    SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
                    clickInstagramProfileTab()
                }
                logInstagramProfileNavigation(
                    if (recovered && profileTab != null) {
                        "profile_tab_retried"
                    } else if (recovered) {
                        "back_recovered"
                    } else {
                        "profile_recovery_failed"
                    },
                    started,
                )
                SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
                debugMapper.capture(
                    "instagram_profile_recovery_${attempt + 1}",
                    activeScope,
                    if (recovered) "observed" else "failed",
                )
            }
        }
        return finalizeInstagramOwnProfileAfterWait(started, notVerifiedFallback = true)
    }

    /** Same Edit/Share/metrics proof — only used after wait so late-loading OEM UI can pass. */
    private fun finalizeInstagramOwnProfileAfterWait(
        started: Long,
        notVerifiedFallback: Boolean = false,
    ): Boolean {
        dismissInstagramProfileCoachmarks()
        scrollInstagramProfileToHeaderIfNeeded()
        SystemClock.sleep(PROFILE_LATE_SETTLE_MS)
        val deviceProof = instagramOwnProfileVisibleOnDevice()
        val probe = instagramSurfaceProbe()
        if (deviceProof || probe.ownProfile) {
            return acceptInstagramOwnProfile(started, "verified_after_wait", probe)
        }
        if (notVerifiedFallback) {
            logInstagramProfileNavigation("not_verified", started)
            debugMapper.capture("instagram_profile_not_verified", activeScope, "failed")
            return fail("instagram_profile_not_verified")
        }
        logInstagramProfileNavigation("navigation_timeout", started)
        debugMapper.capture(
            "instagram_profile_navigation_timeout",
            activeScope,
            "failed",
        )
        return fail("instagram_profile_navigation_timeout")
    }

    private fun acceptInstagramOwnProfile(
        started: Long,
        event: String,
        probe: InstagramSurfaceProbe = InstagramSurfaceProbe.EMPTY,
    ): Boolean {
        instagramSubpageActive = false
        markInstagramOwnAccountVerified()
        instagramOwnAccountMarker = probe.accountMarker
            ?.takeIf { it.isNotBlank() }
            ?: shellDumpAccountMarker()
            ?: instagramSurfaceProbe().accountMarker
            ?: instagramOwnAccountMarker
        logInstagramProfileNavigation(event, started)
        val captureName = when (event) {
            "restored_from_subpage" -> "instagram_profile_restored"
            else -> "instagram_profile_verified"
        }
        debugMapper.capture(captureName, activeScope, "verified")
        return true
    }

    /** Settings sheet (Arsip / Aktivitas Anda) is own-account proof without the profile tab. */
    private fun markInstagramOwnAccountVerified() {
        verifiedOwnAccountPackages.add(INSTAGRAM_PACKAGE)
    }

    /** Coachmark/tooltips (e.g. "Try sharing a song…") — tap header chrome, not avatar. */
    private fun dismissInstagramProfileCoachmarks(): Boolean = safeUi(false) {
        val visible = INSTAGRAM_PROFILE_COACHMARK_FRAGMENTS.any { fragment ->
            device.hasObject(By.textContains(fragment)) ||
                device.hasObject(By.descContains(fragment))
        }
        if (!visible) return@safeUi false
        val x = device.displayWidth / 2
        val y = (device.displayHeight * 0.07).toInt().coerceAtLeast(1)
        if (!safeClickPoint(x, y)) return@safeUi false
        SystemClock.sleep(PROFILE_ACTION_INTERVAL_MS)
        Log.i(LOG_TAG, "event=instagram_profile_coachmark_dismissed")
        true
    }

    /**
     * After posts scroll, IG may sit on "Complete your profile" cards with Edit/Share off-screen.
     * One upward swipe restores the header without hurting Samsung (no-op when already proven).
     */
    private fun scrollInstagramProfileToHeaderIfNeeded(): Boolean {
        if (instagramOwnProfileVisibleOnDevice()) return false
        val needsScroll = safeUi(false) {
            INSTAGRAM_PROFILE_SCROLLED_AWAY_FRAGMENTS.any { fragment ->
                device.hasObject(By.textContains(fragment)) ||
                    device.hasObject(By.descContains(fragment))
            }
        }
        if (!needsScroll) return false
        val width = device.displayWidth
        val height = device.displayHeight
        if (width <= 0 || height <= 0) return false
        // Finger down → bring header back into view.
        safeSwipe(
            width / 2,
            height / 3,
            width / 2,
            (height * 3) / 4,
            SWIPE_STEPS,
        )
        SystemClock.sleep(INSTAGRAM_SCROLL_SETTLE_MS)
        Log.i(LOG_TAG, "event=instagram_profile_scroll_to_header")
        return true
    }

    private fun clickInstagramProfileTab(): Boolean =
        performAccessibilityClick { node, bounds ->
            val resource = node.viewIdResourceName.orEmpty().substringAfterLast('/')
            resource in INSTAGRAM_PROFILE_RESOURCES &&
                bounds.top >= (device.displayHeight * 2) / 3 &&
                bounds.right >= (device.displayWidth * 3) / 4
        } || clickInstagramProfileTabCoordinate() || clickInstagramProfileTabShellTap()

    private fun clickInstagramProfileTabShellTap(): Boolean {
        val obj = safeUi(null as UiObject2?) {
            INSTAGRAM_PROFILE_RESOURCES.firstNotNullOfOrNull { res ->
                device.findObject(By.res(INSTAGRAM_PACKAGE, res))
            } ?: device.findObject(By.desc("Profile"))
                ?: device.findObject(By.desc("Profil"))
        } ?: return false
        val bounds = safeBounds(obj) ?: return false
        if (bounds.top < (device.displayHeight * 2) / 3) return false
        val tapped = shellTap(bounds.centerX(), bounds.centerY())
        Log.i(
            LOG_TAG,
            "event=instagram_profile_tab_shell_tap success=$tapped " +
                "x=${bounds.centerX()} y=${bounds.centerY()}",
        )
        return tapped
    }

    private fun clickInstagramProfileTabCoordinate(): Boolean {
        val width = device.displayWidth
        val height = device.displayHeight
        val navigationInset = systemBarInset("navigation_bar_height")
        val contentBottom = (height - navigationInset).coerceIn(1, height)
        if (width <= 0 || contentBottom <= 0) return false
        val density = context.resources.displayMetrics.density
        val x = (width * 9) / 10
        val bottomInset = (24 * density).toInt().coerceIn(32, contentBottom / 10)
        val y = (contentBottom - bottomInset).coerceAtLeast(contentBottom * 4 / 5)
        return safeClickPoint(x, y)
    }

    private fun hasInstagramOwnProfileProof(): Boolean =
        instagramOwnProfileVisibleOnDevice() || instagramSurfaceProbe().ownProfile

    /**
     * Own-profile proof via UiDevice selectors (archive-scroll-probe approach).
     * AccessibilityNodeInfo walks miss Compose profile headers on this IG build.
     */
    private fun instagramOwnProfileVisibleOnDevice(): Boolean = safeUi(false) {
        if (!isForeground(INSTAGRAM_PACKAGE)) return@safeUi false
        val edit = EDIT_PROFILE_LABELS.any { label ->
            device.hasObject(By.text(label)) || device.hasObject(By.desc(label))
        }
        val share = SHARE_PROFILE_LABELS.any { label ->
            device.hasObject(By.text(label)) || device.hasObject(By.desc(label))
        }
        if (edit || share) {
            Log.i(LOG_TAG, "event=instagram_profile_uidevice_proof edit=$edit share=$share")
            return@safeUi true
        }
        val postValue = device.hasObject(
            By.res(INSTAGRAM_PACKAGE, "profile_header_familiar_post_count_value"),
        ) || device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_header_post_count_front_familiar"))
        val followersValue = device.hasObject(
            By.res(INSTAGRAM_PACKAGE, "profile_header_familiar_followers_value"),
        ) || device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_header_followers_stacked_familiar"))
        val followingValue = device.hasObject(
            By.res(INSTAGRAM_PACKAGE, "profile_header_familiar_following_value"),
        ) || device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_header_following_stacked_familiar"))
        val metricsOk = listOf(postValue, followersValue, followingValue).count { it } >= 2
        val header = device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_header_container")) ||
            device.hasObject(By.res(INSTAGRAM_PACKAGE, "row_profile_header")) ||
            device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_header_fixed_list"))
        val ok = metricsOk && header
        if (ok) {
            Log.i(
                LOG_TAG,
                "event=instagram_profile_uidevice_proof metrics posts=$postValue " +
                    "followers=$followersValue following=$followingValue header=$header",
            )
        }
        ok
    }

    private fun instagramSurfaceProbe(
        nodes: List<InstagramProbeNode> = instagramProbeNodes(),
    ): InstagramSurfaceProbe = safeUi(InstagramSurfaceProbe.EMPTY) {
        // Compose profile header often invisible to UiAutomation; fall back to shell dump
        // (same technique that unlocked Archive clicks in the probe).
        val baseNodes = if (nodes.isNotEmpty()) {
            nodes
        } else {
            shellDumpProbeNodes(
                EDIT_PROFILE_LABELS + SHARE_PROFILE_LABELS + listOf(
                    "posts",
                    "followers",
                    "following",
                    "postingan",
                    "pengikut",
                ),
            )
        }

        fun hasLabel(values: List<String>, contains: Boolean = true): Boolean {
            val expected = values.map { value -> value.lowercase(Locale.ROOT) }
            val inNodes = baseNodes.any { node ->
                node.labels.any { observed ->
                    expected.any { candidate ->
                        observed == candidate || (contains && observed.contains(candidate))
                    }
                }
            }
            return inNodes || shellDumpHasAnyLabel(values)
        }

        fun hasResource(values: List<String>): Boolean {
            if (baseNodes.any { node -> node.resourceName in values }) return true
            val xml = readShellUiDump()
            return values.any { resource ->
                xml.contains(":id/$resource\"") || xml.contains("/$resource\"")
            }
        }

        val editVisible = hasLabel(EDIT_PROFILE_LABELS)
        val shareVisible = hasLabel(SHARE_PROFILE_LABELS)
        val metricKinds = INSTAGRAM_PROFILE_METRIC_GROUPS.count { group ->
            group.any { metric ->
                baseNodes.any { node -> node.labels.any { label -> label.contains(metric) } } ||
                    shellDumpHasAnyLabel(listOf(metric))
            }
        }.coerceAtLeast(
            listOf("post_count", "followers", "following").count { fragment ->
                baseNodes.any { node -> node.resourceName.contains(fragment) } ||
                    readShellUiDump().contains(fragment)
            },
        )
        val headerVisible = hasResource(INSTAGRAM_PROFILE_HEADER_RESOURCES)
        val gridVisible = hasResource(INSTAGRAM_GRID_TAB_RESOURCES)
        val ownProfileLabelVisible = hasLabel(INSTAGRAM_OWN_PROFILE_LABELS)
        val profileSurface = editVisible || shareVisible || metricKinds >= 2 || headerVisible
        val otherProfile = profileSurface &&
            (
                hasResource(listOf("profile_header_follow_button")) ||
                    hasLabel(INSTAGRAM_OTHER_PROFILE_LABELS, contains = false)
                ) &&
            !editVisible &&
            !shareVisible
        var signals = 0
        if (editVisible) signals += 3
        if (shareVisible) signals += 3
        if (ownProfileLabelVisible) signals += 3
        if (hasResource(INSTAGRAM_USERNAME_RESOURCES)) signals += 1
        if (metricKinds >= 2) signals += 2
        if (headerVisible) signals += 1
        if (gridVisible) signals += 1
        val ownProfile = !otherProfile &&
            (
                editVisible ||
                    shareVisible ||
                    ownProfileLabelVisible ||
                    signals >= MIN_INSTAGRAM_OWN_PROFILE_SIGNALS
                )

        val profileLabels = INSTAGRAM_PROFILE_LABELS
            .map { value -> value.lowercase(Locale.ROOT) }
        val profileDescriptions = INSTAGRAM_PROFILE_DESC_FRAGMENTS
            .map { value -> value.lowercase(Locale.ROOT) }
        val profileTab = baseNodes.asSequence()
            .filter { node ->
                node.bounds.top >= (device.displayHeight * 2) / 3 &&
                    node.bounds.right >= (device.displayWidth * 3) / 4
            }
            .filter { node ->
                node.resourceName in INSTAGRAM_PROFILE_RESOURCES ||
                    node.labels.any { label ->
                        label in profileLabels ||
                            profileDescriptions.any { fragment -> label.contains(fragment) }
                    }
            }
            .sortedWith(
                compareBy<InstagramProbeNode> { node ->
                    INSTAGRAM_PROFILE_RESOURCES.indexOf(node.resourceName)
                        .takeIf { index -> index >= 0 } ?: Int.MAX_VALUE
                }
                    .thenByDescending { node -> node.bounds.right },
            )
            .firstOrNull()
            ?.bounds

        val dismissLabels = INSTAGRAM_BLOCKING_DIALOG_DISMISS_LABELS
            .map { value -> value.lowercase(Locale.ROOT) }
            .toSet()
        val dismissTarget = baseNodes.firstOrNull { node ->
            node.labels.any(dismissLabels::contains)
        }?.bounds
        val accountMarker = INSTAGRAM_USERNAME_RESOURCES.asSequence()
            .mapNotNull { resource ->
                baseNodes.asSequence()
                    .filter { node -> node.resourceName == resource }
                    .flatMap { node -> node.labels.asSequence() }
                    .mapNotNull(::normalizeAccountMarker)
                    .firstOrNull()
            }
            .firstOrNull()
            ?: shellDumpAccountMarker()

        InstagramSurfaceProbe(
            ownProfile,
            profileSurface,
            otherProfile,
            profileTab,
            dismissTarget,
            accountMarker,
            baseNodes.size,
            signals,
            metricKinds,
            editVisible,
            shareVisible,
        )
    }

    private fun shellDumpAccountMarker(): String? {
        val xml = readShellUiDump()
        // Prefer action_bar / username resource text, then @handles.
        val resourceHints = INSTAGRAM_USERNAME_RESOURCES
        val nodeRe = Regex("""<node\b[^>]*>""")
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val id = shellDumpAttr(tag, "resource-id").substringAfterLast('/')
            if (id !in resourceHints) continue
            sequenceOf(shellDumpAttr(tag, "text"), shellDumpAttr(tag, "content-desc"))
                .mapNotNull(::normalizeAccountMarker)
                .firstOrNull()
                ?.let { return it }
        }
        Regex("""(?:text|content-desc)="@([A-Za-z0-9._]{2,30})"""").find(xml)
            ?.groupValues
            ?.getOrNull(1)
            ?.let { candidate -> return normalizeAccountMarker("@$candidate") }
        return null
    }

    private fun instagramProbeNodes(): List<InstagramProbeNode> {
        val root = try {
            uiAutomation.rootInActiveWindow
        } catch (_: Exception) {
            null
        } ?: return emptyList()
        if (root.packageName?.toString() != INSTAGRAM_PACKAGE) return emptyList()
        return snapshotVisibleNodes(root)
            .asSequence()
            .take(MAX_INSTAGRAM_PROBE_NODES)
            .map { node ->
                InstagramProbeNode(
                    labels = sequenceOf(node.text, node.contentDescription)
                        .filterNotNull()
                        .map { label -> label.trim().lowercase(Locale.ROOT) }
                        .filter(String::isNotEmpty)
                        .distinct()
                        .toList(),
                    resourceName = node.viewId.orEmpty().substringAfterLast('/'),
                    bounds = Rect(node.left, node.top, node.right, node.bottom),
                    className = node.className.orEmpty(),
                    clickable = node.clickable,
                    scrollable = node.scrollable,
                )
            }
            .toList()
    }

    private fun instagramExactNode(
        labels: List<String>,
        nodes: List<InstagramProbeNode>,
        minimumTop: Int,
        maximumTop: Int,
    ): InstagramProbeNode? {
        val expected = labels.map { value -> value.trim().lowercase(Locale.ROOT) }.toSet()
        return nodes.asSequence()
            .filter { node ->
                node.bounds.top in minimumTop..maximumTop &&
                    node.bounds.left >= 0 &&
                    node.bounds.right <= device.displayWidth &&
                    node.labels.any(expected::contains)
            }
            .minWithOrNull(
                compareByDescending<InstagramProbeNode> { node -> node.clickable }
                    .thenBy { node -> node.bounds.top }
                    .thenBy { node -> node.bounds.left },
            )
    }

    private fun instagramHasHeaderLabel(
        labels: List<String>,
        nodes: List<InstagramProbeNode>,
    ): Boolean {
        val bounds = activeWindowBounds()
        if (instagramExactNode(
                labels,
                nodes,
                bounds.top,
                bounds.top + bounds.height() / 3,
            ) != null
        ) {
            return true
        }
        // Compose archive headers often miss a11y; UiDevice / shell dump still see them.
        return labels.any { label ->
            device.hasObject(By.text(label)) || device.hasObject(By.desc(label))
        } || shellDumpHasAnyLabel(labels)
    }

    private fun logInstagramProfileNavigation(stage: String, started: Long) {
        Log.i(
            LOG_TAG,
            "event=instagram_profile_navigation stage=$stage " +
                "elapsed_ms=${(SystemClock.elapsedRealtime() - started).coerceAtLeast(0)}",
        )
    }

    private fun logInstagramArchiveNavigation(stage: String, started: Long) {
        Log.i(
            LOG_TAG,
            "event=instagram_archive_navigation stage=$stage " +
                "elapsed_ms=${(SystemClock.elapsedRealtime() - started).coerceAtLeast(0)}",
        )
    }

    private fun instagramLabelNoise(label: String): Boolean {
        val normalized = label.trim().lowercase()
        if (normalized.isEmpty()) return true
        return normalized in INSTAGRAM_NAV_NOISE ||
            INSTAGRAM_NAV_NOISE.any { noise -> normalized.contains(noise) }
    }

    private fun openInstagramStoryArchive(): Boolean {
        val started = SystemClock.elapsedRealtime()
        logInstagramArchiveNavigation("start", started)
        // Probe: scroll back to header so Options is on-screen after posts.
        ensureInstagramProfileHeaderForMenu()
        debugMapper.capture("instagram_archive_profile_ready", SocialScope.OWN_STORY_ARCHIVE)
        if (!clickInstagramOptions()) return fail("instagram_options_not_found")
        instagramSubpageActive = true
        debugMapper.capture("instagram_archive_settings_open", SocialScope.OWN_STORY_ARCHIVE)
        revealInstagramSettingsRow(ARCHIVE_LABELS)
        if (!clickInstagramArchiveMenuEntry()) {
            revealInstagramSettingsRow(ARCHIVE_LABELS)
            if (!clickInstagramArchiveMenuEntry()) {
                debugMapper.capture(
                    "instagram_archive_entry_not_found",
                    SocialScope.OWN_STORY_ARCHIVE,
                    "failed",
                )
                return fail("instagram_archive_not_found")
            }
        }
        logInstagramArchiveNavigation("archive_clicked", started)
        debugMapper.capture("instagram_archive_entry_clicked", SocialScope.OWN_STORY_ARCHIVE)
        val archiveNodes = waitForInstagramArchivePageNodes()
        if (archiveNodes == null) {
            debugMapper.capture(
                "instagram_archive_page_not_ready",
                SocialScope.OWN_STORY_ARCHIVE,
                "failed",
            )
            return fail("instagram_archive_not_ready")
        }
        val archiveReady = if (instagramHasHeaderLabel(STORY_ARCHIVE_LABELS, archiveNodes)) {
            true
        } else {
            switchInstagramArchiveToStories(archiveNodes)
        }
        if (!archiveReady) {
            debugMapper.capture(
                "instagram_story_archive_mode_failed",
                SocialScope.OWN_STORY_ARCHIVE,
                "failed",
            )
            return fail("instagram_story_archive_not_ready")
        }
        val storyNodes = instagramProbeNodes()
        if (!isForeground(INSTAGRAM_PACKAGE) || !instagramHasHeaderLabel(
                STORY_ARCHIVE_LABELS,
                storyNodes,
            )
        ) {
            debugMapper.capture(
                "instagram_story_archive_verification_failed",
                SocialScope.OWN_STORY_ARCHIVE,
                "failed",
            )
            return fail("instagram_story_archive_empty")
        }
        instagramArchiveEndReached = false
        instagramLastArchiveCaptureSignature = null
        instagramArchiveScrollBudget = INSTAGRAM_ARCHIVE_SCROLL_LIMIT
        instagramArchiveScrollsCompleted = 0
        instagramArchiveListActive = true
        logInstagramArchiveNavigation("stories_verified", started)
        debugMapper.capture(
            "instagram_story_archive_initial",
            SocialScope.OWN_STORY_ARCHIVE,
            "verified",
        )
        return true
    }

    private fun openInstagramOwnPost(): Boolean {
        val started = SystemClock.elapsedRealtime()
        // UiDevice/shell proof — do not re-gate on a11y surfaceProbe (Compose misses Edit profile).
        if (!hasInstagramOwnProfileProof()) {
            return fail("instagram_profile_not_verified")
        }
        // Empty grid: finish immediately. Do not probe/dump — that hung Infinix ~165s.
        if (instagramEmptyPostsVisible()) {
            instagramResolvedPostCount = 0
            instagramPostCountKnown = true
            instagramPostEndReached = true
            instagramLastPostCaptureSignature = null
            instagramGridScrollBudget = 0
            instagramOwnPostActive = true
            Log.i(
                LOG_TAG,
                "event=instagram_posts_ready post_count=0 via=empty_state " +
                    "elapsed_ms=${SystemClock.elapsedRealtime() - started}",
            )
            debugMapper.capture("instagram_posts_initial", SocialScope.OWN_POSTS, "verified")
            return true
        }
        val profileNodes = instagramProbeNodes()
        val profilePostCount = resolveInstagramPostCount(profileNodes)
        val gridNodes = if (profilePostCount == 0) {
            profileNodes
        } else {
            ensureInstagramGridTabSelected(profileNodes)
                ?: ensureInstagramGridTabViaUiDevice()
                ?: profileNodes.takeIf { hasInstagramOwnProfileProof() }
                ?: return fail("instagram_grid_tab_not_found")
        }
        // Grid thumbs often invisible to a11y; own-profile proof is enough to capture the viewport.
        if (profilePostCount != 0 &&
            !instagramGridContentVisible(gridNodes) &&
            !instagramGridVisibleOnDevice()
        ) {
            Log.i(
                LOG_TAG,
                "event=instagram_posts_grid_a11y_thin continuing_with_profile_proof=true " +
                    "post_count=${profilePostCount ?: -1}",
            )
        }
        instagramOwnAccountMarker = instagramAccountMarker(gridNodes) ?: instagramOwnAccountMarker
        val postCount = profilePostCount ?: resolveInstagramPostCount(gridNodes)
        instagramResolvedPostCount = postCount
        instagramPostCountKnown = postCount != null
        instagramPostEndReached = false
        instagramLastPostCaptureSignature = null
        instagramGridScrollBudget = estimateInstagramGridScrolls(postCount)
        instagramOwnPostActive = true
        Log.i(
            LOG_TAG,
            "event=instagram_posts_ready post_count=${postCount ?: -1} " +
                "scroll_budget=$instagramGridScrollBudget " +
                "elapsed_ms=${SystemClock.elapsedRealtime() - started}",
        )
        debugMapper.capture("instagram_posts_initial", SocialScope.OWN_POSTS, "verified")
        return true
    }

    private fun instagramGridContentVisible(
        nodes: List<InstagramProbeNode> = instagramProbeNodes(),
    ): Boolean {
        val gridTop = (device.displayHeight * 2) / 5
        val gridBottom = (device.displayHeight * 9) / 10
        return nodes.any { node ->
            node.bounds.top >= gridTop &&
                node.bounds.bottom <= gridBottom &&
                node.bounds.width() >= device.displayWidth / 6 &&
                node.bounds.height() >= device.displayWidth / 6 &&
                (
                    node.resourceName in INSTAGRAM_POST_ITEM_RESOURCES ||
                        node.className.endsWith("ImageView") ||
                        INSTAGRAM_POST_DESCRIPTION_FRAGMENTS.any { fragment ->
                            node.labels.any { label ->
                                label.contains(fragment.lowercase(Locale.ROOT))
                            }
                        }
                    )
        }
    }

    private fun instagramPostCount(nodes: List<InstagramProbeNode>): Int? {
        nodes.asSequence()
            .filter { node -> node.resourceName.contains("post_count") }
            .flatMap { node -> node.labels.asSequence() }
            .mapNotNull(::parseCountLabel)
            .firstOrNull()
            ?.let { return it }
        nodes.asSequence()
            .flatMap { node -> node.labels.asSequence() }
            .mapNotNull { label -> POST_COUNT_INLINE.find(label) }
            .mapNotNull { match -> parseCountLabel(match.groupValues[1]) }
            .firstOrNull()
            ?.let { return it }
        val postLabelNodes = nodes.filter { node ->
            node.labels.any { label -> label in POST_COUNT_LABELS }
        }
        return postLabelNodes.asSequence()
            .flatMap { labelNode ->
                nodes.asSequence()
                    .filter { candidate ->
                        candidate.bounds.top < (device.displayHeight * 2) / 3 &&
                            kotlin.math.abs(
                                candidate.bounds.centerX() - labelNode.bounds.centerX(),
                            ) <= device.displayWidth / 5 &&
                            kotlin.math.abs(
                                candidate.bounds.centerY() - labelNode.bounds.centerY(),
                            ) <= device.displayHeight / 10
                    }
                    .flatMap { candidate -> candidate.labels.asSequence() }
                    .mapNotNull(::parseCountLabel)
            }
            .firstOrNull()
    }

    private fun parseCountLabel(value: String): Int? {
        val trimmed = value.trim().lowercase()
        val suffixMatch = Regex("^([0-9]+(?:[.,][0-9]+)?)\\s*(k|m|b|rb|jt)?$")
            .matchEntire(trimmed)
        if (suffixMatch != null) {
            val suffix = suffixMatch.groupValues.getOrNull(2).orEmpty()
            if (suffix.isEmpty()) {
                return suffixMatch.groupValues[1]
                    .replace(Regex("[.,]"), "")
                    .toIntOrNull()
            }
            val number = suffixMatch.groupValues[1].replace(",", ".").toDoubleOrNull() ?: return null
            val multiplier = when (suffix) {
                "k", "rb" -> 1_000.0
                "m", "jt" -> 1_000_000.0
                "b" -> 1_000_000_000.0
                else -> return null
            }
            return (number * multiplier).toInt().takeIf { it >= 0 }
        }
        val digits = trimmed.replace(Regex("[\\s,]"), "").replace(".", "")
        return digits.toIntOrNull()?.takeIf { it >= 0 }
    }

    private fun resolveInstagramPostCount(
        nodes: List<InstagramProbeNode>,
    ): Int? {
        // Empty-state and resource-id first — never shell-dump on this hot path (Infinix hang).
        if (instagramEmptyPostsVisible()) return 0
        instagramPostCountFromUiDevice()?.let { return it }
        instagramPostCount(nodes)?.let { return it }
        return null
    }

    private fun instagramPostCountFromShellDump(): Int? {
        invalidateShellDumpCache()
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return null
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        val postsLabels = setOf("posts", "postingan", "kiriman")
        val nodes = mutableListOf<InstagramProbeNode>()
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val text = shellDumpAttr(tag, "text").trim()
            val desc = shellDumpAttr(tag, "content-desc").trim()
            val combined = listOf(text, desc).filter { it.isNotEmpty() }
            if (combined.isEmpty()) continue
            val interesting = combined.any { label ->
                val lower = label.lowercase(Locale.ROOT)
                lower in postsLabels || parseCountLabel(label) != null
            }
            if (!interesting) continue
            val b = boundsRe.find(tag) ?: continue
            nodes += InstagramProbeNode(
                labels = combined.map { it.lowercase(Locale.ROOT) },
                resourceName = shellDumpAttr(tag, "resource-id").substringAfterLast('/'),
                bounds = Rect(
                    b.groupValues[1].toInt(),
                    b.groupValues[2].toInt(),
                    b.groupValues[3].toInt(),
                    b.groupValues[4].toInt(),
                ),
                className = shellDumpAttr(tag, "class"),
                clickable = shellDumpAttr(tag, "clickable") == "true",
                scrollable = shellDumpAttr(tag, "scrollable") == "true",
            )
        }
        return instagramPostCount(nodes)
    }

    private fun instagramPostCountFromUiDevice(): Int? = safeUi(null as Int?) {
        // Resource-id only — broad findObjects/nearby scans can stall on Compose idle.
        val resources = listOf(
            "profile_header_familiar_post_count_value",
            "profile_header_post_count_front_familiar",
            "profile_header_post_count",
            "row_profile_header_textview_post_count",
        )
        for (res in resources) {
            val obj = device.findObject(By.res(INSTAGRAM_PACKAGE, res)) ?: continue
            parseCountLabel(obj.text?.toString().orEmpty())?.let { return@safeUi it }
            parseCountLabel(obj.contentDescription?.toString().orEmpty())?.let { return@safeUi it }
        }
        null
    }

    private fun instagramEmptyPostsVisible(): Boolean = safeUi(false) {
        device.hasObject(By.text("Create your first post")) ||
            device.hasObject(By.textContains("Create your first post")) ||
            device.hasObject(By.text("Buat postingan pertama Anda")) ||
            device.hasObject(By.textContains("Buat postingan pertama")) ||
            device.hasObject(By.text("Share photos and videos")) ||
            device.hasObject(By.textContains("Share your point of view"))
    }

    private fun estimateInstagramGridScrolls(postCount: Int?): Int {
        // <3 posts: no scroll. Unknown after UiDevice resolve: do not thrash.
        if (postCount == null) return 0
        if (postCount < GRID_SCROLL_MIN_POSTS) return 0
        if (postCount <= VISIBLE_GRID_POSTS) return 0
        val pages = (postCount + VISIBLE_GRID_POSTS - 1) / VISIBLE_GRID_POSTS
        return (pages - 1).coerceIn(1, MAX_GRID_SCROLLS)
    }

    /** Probe-proven: Settings Search → type Archive/Arsip when Compose list is invisible. */
    private fun clickInstagramArchiveViaSettingsSearch(): Boolean =
        clickInstagramLabelViaSettingsSearch(
            queries = listOf("Archive", "Arsip"),
            labels = ARCHIVE_LABELS,
            eventPrefix = "instagram_archive",
            settleMs = INSTAGRAM_ARCHIVE_LOAD_SETTLE_MS,
            preferUiDeviceArchiveClick = true,
        )

    /**
     * Settings Search is always on the Settings header — use it when list rows are
     * Compose-invisible or the list scrolled away from Archive / Your activity.
     */
    private fun clickInstagramLabelViaSettingsSearch(
        queries: List<String>,
        labels: List<String>,
        eventPrefix: String,
        settleMs: Long = INSTAGRAM_ACTION_SETTLE_MS,
        preferUiDeviceArchiveClick: Boolean = false,
    ): Boolean {
        val normalized = labels.map { it.lowercase(Locale.ROOT) }.toSet()
        val editAlready = safeUi(null) {
            device.findObject(By.clazz("android.widget.EditText"))
                ?: device.findObject(By.focused(true))
        }
        if (editAlready == null) {
            if (!clickInstagramSettingsSearchControl()) {
                Log.i(LOG_TAG, "event=${eventPrefix}_settings_search_missing")
                return false
            }
            SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        }
        val edit = safeUi(null) {
            device.findObject(By.clazz("android.widget.EditText"))
                ?: device.findObject(By.focused(true))
        } ?: run {
            Log.i(LOG_TAG, "event=${eventPrefix}_settings_search_no_edit")
            return false
        }
        for (query in queries) {
            val typed = safeUi(false) {
                try {
                    edit.text = query
                    true
                } catch (_: Throwable) {
                    false
                }
            }
            if (!typed) continue
            SystemClock.sleep(INSTAGRAM_SETTINGS_SEARCH_SETTLE_MS)
            if (preferUiDeviceArchiveClick && clickInstagramArchiveUiDevice()) {
                SystemClock.sleep(settleMs)
                Log.i(LOG_TAG, "event=${eventPrefix}_settings_search success=true query=$query")
                return true
            }
            if (clickInstagramSettingsLabel(labels)) {
                SystemClock.sleep(settleMs)
                Log.i(LOG_TAG, "event=${eventPrefix}_settings_search success=true query=$query")
                return true
            }
            if (clickInstagramLabelViaShellDump(labels)) {
                SystemClock.sleep(settleMs)
                Log.i(LOG_TAG, "event=${eventPrefix}_settings_search_shell success=true query=$query")
                return true
            }
            val a11yHit = performAccessibilityClick { node, _ ->
                accessibilityLabels(node).any { label -> label in normalized }
            }
            if (a11yHit) {
                SystemClock.sleep(settleMs)
                Log.i(LOG_TAG, "event=${eventPrefix}_settings_search_a11y success=true query=$query")
                return true
            }
        }
        Log.i(LOG_TAG, "event=${eventPrefix}_settings_search success=false")
        return false
    }

    private fun clickInstagramSettingsSearchControl(): Boolean {
        val clicked = safeUi(false) {
            val selectors = listOf(
                By.text("Search"),
                By.text("Cari"),
                By.desc("Search"),
                By.desc("Cari"),
                By.descContains("Search"),
                By.descContains("Cari"),
            )
            for (selector in selectors) {
                val obj = device.findObject(selector) ?: continue
                if (safeClick(obj)) return@safeUi true
                val bounds = safeBounds(obj) ?: continue
                if (safeClickPoint(bounds.centerX(), bounds.centerY())) return@safeUi true
            }
            false
        }
        if (clicked) return true
        return clickInstagramLabelViaShellDump(listOf("Search", "Cari"))
    }

    private fun ensureInstagramGridTabSelected(
        initialNodes: List<InstagramProbeNode>,
    ): List<InstagramProbeNode>? {
        if (instagramGridContentVisible(initialNodes) || instagramGridVisibleOnDevice()) {
            return initialNodes
        }
        val window = activeWindowBounds()
        val minimumTop = window.top + window.height() / 4
        val maximumTop = window.top + (window.height() * 3) / 4
        val labels = setOf("posts", "postingan", "kiriman", "grid")
        val target = initialNodes.asSequence()
            .filter { node ->
                node.bounds.centerX() < device.displayWidth / 2 &&
                    node.bounds.top in minimumTop..maximumTop &&
                    (
                        node.resourceName in INSTAGRAM_GRID_TAB_RESOURCES ||
                            node.labels.any(labels::contains)
                        )
            }
            .minByOrNull { node -> node.bounds.left }
            ?: return null
        if (!safeClickPoint(target.bounds.centerX(), target.bounds.centerY())) return null
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        val refreshed = instagramProbeNodes()
        return refreshed.takeIf { nodes ->
            instagramGridContentVisible(nodes) || instagramGridVisibleOnDevice()
        }
    }

    private fun ensureInstagramGridTabViaUiDevice(): List<InstagramProbeNode>? =
        safeUi(null as List<InstagramProbeNode>?) {
            val tab = device.findObject(By.desc("Grid view"))
                ?: device.findObject(By.desc("Tampilan kisi"))
                ?: device.findObject(By.descContains("Grid"))
                ?: return@safeUi null
            if (!safeClick(tab)) return@safeUi null
            SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
            instagramProbeNodes()
        }

    private fun instagramGridVisibleOnDevice(): Boolean = safeUi(false) {
        device.hasObject(By.desc("Grid view")) ||
            device.hasObject(By.desc("Tampilan kisi")) ||
            device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_tab_layout")) ||
            INSTAGRAM_GRID_TAB_RESOURCES.any { res ->
                device.hasObject(By.res(INSTAGRAM_PACKAGE, res))
            }
    }

    private fun advanceInstagramOwnPost(): Boolean {
        if (!instagramOwnPostActive || instagramPostEndReached) return false
        if (INSTAGRAM_PACKAGE !in verifiedOwnAccountPackages) return false
        if (!isForeground(INSTAGRAM_PACKAGE)) return false
        if (!swipeInstagramGrid()) return false
        SystemClock.sleep(INSTAGRAM_SCROLL_SETTLE_MS)
        val visible = isForeground(INSTAGRAM_PACKAGE)
        debugMapper.capture(
            "instagram_posts_after_scroll",
            SocialScope.OWN_POSTS,
            if (visible) "observed" else "failed",
        )
        return visible
    }

    private fun advanceInstagramArchiveList(): Boolean {
        if (!instagramArchiveListActive || instagramArchiveEndReached) return false
        if (!isForeground(INSTAGRAM_PACKAGE)) return false
        // Page-sized list scroll only — never ViewPager ACTION_SCROLL (flips tabs).
        if (!swipeInstagramArchivePage()) return false
        SystemClock.sleep(INSTAGRAM_ARCHIVE_SCROLL_SETTLE_MS)
        ensureInstagramStoriesArchiveTabSelected()
        instagramArchiveScrollsCompleted += 1
        val visible = isForeground(INSTAGRAM_PACKAGE) && isInstagramArchiveListSurface()
        debugMapper.capture(
            "instagram_archive_after_scroll",
            SocialScope.OWN_STORY_ARCHIVE,
            if (visible) "observed" else "failed",
        )
        return visible
    }

    /**
     * Stories archive intercepts pressBack on some OEM builds. Click the header
     * Back control, then open Comments from Settings if that sheet is still up.
     */
    private fun leaveInstagramArchiveForComments(): Boolean {
        if (!instagramOnStoriesArchive()) {
            return true
        }
        Log.i(LOG_TAG, "event=instagram_archive_back stage=start")
        debugMapper.capture("instagram_archive_back_start", SocialScope.OWN_COMMENTS)
        var previousVia: String? = null
        repeat(MAX_INSTAGRAM_PROFILE_BACK_STEPS) { step ->
            invalidateShellDumpCache()
            if (instagramOptionsMenuAlreadyOpen()) {
                markInstagramArchiveLeft()
                debugMapper.capture(
                    "instagram_archive_back_cleared",
                    SocialScope.OWN_COMMENTS,
                    "observed",
                )
                return true
            }
            val dumpShowsArchive = shellDumpHasAnyLabel(STORY_ARCHIVE_LABELS)
            if (!dumpShowsArchive && previousVia != null && previousVia != "geometry") {
                markInstagramArchiveLeft()
                debugMapper.capture(
                    "instagram_archive_back_cleared",
                    SocialScope.OWN_COMMENTS,
                    previousVia,
                )
                return true
            }
            if (!dumpShowsArchive && !isInstagramArchiveListSurface()) {
                markInstagramArchiveLeft()
                return true
            }
            val via = clickInstagramHeaderBack()
            previousVia = via
            invalidateShellDumpCache()
            SystemClock.sleep(INSTAGRAM_ARCHIVE_LOAD_SETTLE_MS)
            debugMapper.capture(
                "instagram_archive_back_$step",
                SocialScope.OWN_COMMENTS,
                via,
            )
        }
        invalidateShellDumpCache()
        if (instagramOptionsMenuAlreadyOpen()) {
            markInstagramArchiveLeft()
            debugMapper.capture(
                "instagram_archive_back_cleared",
                SocialScope.OWN_COMMENTS,
                "observed",
            )
            return true
        }
        if (!shellDumpHasAnyLabel(STORY_ARCHIVE_LABELS) &&
            previousVia != null &&
            previousVia != "geometry"
        ) {
            markInstagramArchiveLeft()
            debugMapper.capture(
                "instagram_archive_back_cleared",
                SocialScope.OWN_COMMENTS,
                previousVia,
            )
            return true
        }
        debugMapper.capture("instagram_archive_back_failed", SocialScope.OWN_COMMENTS, "failed")
        return fail("instagram_archive_back_failed")
    }

    /** Flag is cleared on scope switch; dump title is the live signal. */
    private fun instagramOnStoriesArchive(): Boolean {
        if (shellDumpHasAnyLabel(STORY_ARCHIVE_LABELS)) return true
        return isInstagramArchiveListSurface()
    }

    private fun markInstagramArchiveLeft() {
        instagramArchiveListActive = false
        instagramSubpageActive = instagramOptionsMenuAlreadyOpen()
    }

    private fun clickInstagramSubpageBack(): Boolean {
        clickInstagramHeaderBack()
        return true
    }

    /**
     * Portable header Back: accessibility and bounded UiDevice first.
     * Compressed dump + `input tap` is fallback when those miss or hang
     * (Infinix/IG Compose). Live bounds from the dump, not a device pixel map.
     */
    private fun clickInstagramHeaderBack(): String {
        if (clickInstagramHeaderBackAccessibility()) return "a11y"
        if (clickInstagramHeaderBackUiDevice()) return "uidevice"
        if (clickInstagramHeaderBackViaShellDump()) return "dump"
        val x = (device.displayWidth * 8 / 100).coerceAtLeast(24)
        val y = (device.displayHeight * 7 / 100).coerceAtLeast(48)
        Log.i(LOG_TAG, "event=instagram_header_back via=geometry x=$x y=$y")
        shellTap(x, y)
        return "geometry"
    }

    private fun clickInstagramHeaderBackAccessibility(): Boolean {
        val maxTop = (device.displayHeight / 5).coerceAtLeast(1)
        val maxRight = (device.displayWidth / 3).coerceAtLeast(1)
        val backLabels = INSTAGRAM_HEADER_BACK_LABELS
            .map { value -> value.lowercase(Locale.ROOT) }
            .toSet()
        return performAccessibilityClick { node, bounds ->
            if (bounds.top >= maxTop || bounds.left >= maxRight) return@performAccessibilityClick false
            val resourceId = node.viewIdResourceName.orEmpty()
            resourceId.endsWith("/$INSTAGRAM_HEADER_BACK_RESOURCE") ||
                accessibilityLabels(node).any(backLabels::contains)
        }
    }

    private fun clickInstagramHeaderBackUiDevice(): Boolean = uiQueryBounded(false) {
        val byRes = device.wait(
            Until.findObject(By.res(INSTAGRAM_PACKAGE, INSTAGRAM_HEADER_BACK_RESOURCE)),
            UI_DEVICE_SELECTOR_WAIT_MS,
        )
        if (byRes != null && safeClick(byRes)) return@uiQueryBounded true
        val maxTop = (device.displayHeight / 5).coerceAtLeast(1)
        val maxRight = (device.displayWidth / 3).coerceAtLeast(1)
        for (label in INSTAGRAM_HEADER_BACK_LABELS) {
            val obj = device.wait(Until.findObject(By.desc(label)), UI_DEVICE_SELECTOR_WAIT_MS)
                ?: continue
            val bounds = safeBounds(obj) ?: continue
            if (bounds.top < maxTop && bounds.left < maxRight && safeClick(obj)) {
                return@uiQueryBounded true
            }
        }
        false
    }

    private fun clickInstagramHeaderBackViaShellDump(): Boolean {
        invalidateShellDumpCache()
        val fromResource = shellDumpProbeByResourceSuffix(INSTAGRAM_HEADER_BACK_RESOURCE)
        if (fromResource != null && shellTap(fromResource.centerX(), fromResource.centerY())) {
            Log.i(
                LOG_TAG,
                "event=instagram_header_back via=dump_resource " +
                    "bounds=${fromResource.toShortString()}",
            )
            return true
        }
        val maxTop = (device.displayHeight / 5).coerceAtLeast(1)
        val maxRight = (device.displayWidth / 3).coerceAtLeast(1)
        val fromLabel = shellDumpProbeNodes(INSTAGRAM_HEADER_BACK_LABELS)
            .map { node -> node.bounds }
            .filter { bounds -> bounds.top < maxTop && bounds.left < maxRight }
            .minByOrNull { bounds -> bounds.left }
        if (fromLabel != null && shellTap(fromLabel.centerX(), fromLabel.centerY())) {
            Log.i(
                LOG_TAG,
                "event=instagram_header_back via=dump_label bounds=${fromLabel.toShortString()}",
            )
            return true
        }
        return false
    }

    private fun openInstagramComments(): Boolean {
        if (!instagramOptionsMenuAlreadyOpen() && !clickInstagramOptions()) {
            return fail("instagram_options_not_found")
        }
        instagramSubpageActive = true
        shellDumpDisabledUntilMs = 0L
        invalidateShellDumpCache()
        // Compose Settings rows are clickable=false. Dump+input tap first; a11y can
        // return true without leaving Settings (Samsung d1beba46, Infinix 726a823a).
        if (!clickInstagramActivitySubpageRow(YOUR_ACTIVITY_LABELS) &&
            !clickInstagramLabeledRow(YOUR_ACTIVITY_LABELS) &&
            !clickInstagramCommentsActivityFallback()
        ) {
            return fail("instagram_activity_not_found")
        }
        debugMapper.capture("instagram_your_activity_opening", SocialScope.OWN_COMMENTS)
        if (!waitForInstagramYourActivityScreen()) {
            debugMapper.capture(
                "instagram_your_activity_not_ready",
                SocialScope.OWN_COMMENTS,
                "failed",
            )
            return fail("instagram_activity_not_ready")
        }
        debugMapper.capture("instagram_your_activity_ready", SocialScope.OWN_COMMENTS, "observed")
        invalidateShellDumpCache()
        if (clickInstagramActivitySubpageRow(COMMENTS_LABELS) ||
            clickInstagramLabeledRow(COMMENTS_LABELS)
        ) {
            return finalizeInstagramCommentsSurface()
        }
        if (clickInstagramActivitySubpageRow(INTERACTIONS_LABELS) ||
            clickInstagramLabeledRow(INTERACTIONS_LABELS)
        ) {
            if (!waitForInstagramAnyLabel(COMMENTS_LABELS, INSTAGRAM_YOUR_ACTIVITY_WAIT_MS)) {
                return fail("instagram_comments_not_found")
            }
            if (clickInstagramActivitySubpageRow(COMMENTS_LABELS) ||
                clickInstagramLabeledRow(COMMENTS_LABELS)
            ) {
                return finalizeInstagramCommentsSurface()
            }
        }
        return fail("instagram_interactions_not_found")
    }

    /**
     * Your activity is ready when Comments / Interactions is visible via UiDevice,
     * accessibility, or dump. Dump-only wait failed on Samsung while Comments was
     * already on screen (d1beba46 `your_activity_not_ready`).
     */
    private fun waitForInstagramYourActivityScreen(): Boolean {
        if (instagramYourActivityPageVisible()) return true
        val deadline = SystemClock.elapsedRealtime() + INSTAGRAM_YOUR_ACTIVITY_WAIT_MS
        var retriedActivity = false
        while (SystemClock.elapsedRealtime() < deadline) {
            if (instagramYourActivityPageVisible()) return true
            if (!retriedActivity && instagramOptionsMenuAlreadyOpen()) {
                clickInstagramActivitySubpageRow(YOUR_ACTIVITY_LABELS)
                retriedActivity = true
            }
            SystemClock.sleep(INSTAGRAM_SHELL_POLL_MS)
        }
        return instagramYourActivityPageVisible()
    }

    private fun instagramYourActivityPageVisible(): Boolean {
        if (instagramCommentsListChromeVisible()) return true
        return instagramAnyLabelVisible(COMMENTS_LABELS + INTERACTIONS_LABELS)
    }

    private fun waitForInstagramAnyLabel(labels: List<String>, timeoutMs: Long): Boolean {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (SystemClock.elapsedRealtime() < deadline) {
            if (instagramAnyLabelVisible(labels)) return true
            SystemClock.sleep(INSTAGRAM_SHELL_POLL_MS)
        }
        return instagramAnyLabelVisible(labels)
    }

    private fun instagramAnyLabelVisible(labels: List<String>): Boolean {
        if (uiQueryBounded(false) { hasExactLabel(labels) }) return true
        if (accessibilityHasAnyLabel(labels)) return true
        return shellDumpHasAnyLabel(labels)
    }

    private fun clickInstagramActivitySubpageRow(labels: List<String>): Boolean {
        invalidateShellDumpCache()
        val hits = shellDumpProbeNodes(labels)
        val target = pickInstagramShellRowTarget(hits)
        if (target != null) {
            Log.i(
                LOG_TAG,
                "event=instagram_activity_row_click via=dump label=${labels.firstOrNull()} " +
                    "bounds=${target.bounds.toShortString()} class=${target.className}",
            )
            debugMapper.capture(
                "instagram_activity_row_" +
                    labels.firstOrNull().orEmpty().lowercase(Locale.ROOT).replace(' ', '_'),
                SocialScope.OWN_COMMENTS,
                "dump",
            )
            val clicked = shellTap(target.bounds.centerX(), target.bounds.centerY()) ||
                safeClickPoint(target.bounds.centerX(), target.bounds.centerY()) ||
                a11yServiceTap(target.bounds.centerX(), target.bounds.centerY())
            if (clicked) return true
            Log.i(
                LOG_TAG,
                "event=instagram_activity_row_click_failed via=dump " +
                    "bounds=${target.bounds.toShortString()}",
            )
            // Do not return a false success/failure from the shell coordinate
            // path. Continue with UiDevice and accessibility node actions.
        }
        if (uiQueryBounded(false) { clickInstagramVisibleSettingsRow(labels) }) {
            debugMapper.capture(
                "instagram_activity_row_" +
                    labels.firstOrNull().orEmpty().lowercase(Locale.ROOT).replace(' ', '_'),
                SocialScope.OWN_COMMENTS,
                "uidevice",
            )
            return true
        }
        val normalized = labels.map { value -> value.trim().lowercase(Locale.ROOT) }.toSet()
        if (performAccessibilityClick { node, _ ->
                accessibilityLabels(node).any(normalized::contains)
            }
        ) {
            debugMapper.capture(
                "instagram_activity_row_" +
                    labels.firstOrNull().orEmpty().lowercase(Locale.ROOT).replace(' ', '_'),
                SocialScope.OWN_COMMENTS,
                "a11y",
            )
            return true
        }
        return clickInstagramLabelViaShellDump(labels)
    }

    private fun pickInstagramShellRowTarget(
        hits: List<InstagramProbeNode>,
    ): InstagramProbeNode? {
        if (hits.isEmpty()) return null
        val screenArea = device.displayWidth.toLong() * device.displayHeight.coerceAtLeast(1)
        val rowCandidates = hits.filter { node ->
            val area = node.bounds.width().toLong() * node.bounds.height()
            area <= screenArea / 3
        }
        val pool = rowCandidates.ifEmpty { hits }
        return pool.sortedWith(
            compareByDescending<InstagramProbeNode> { node ->
                if (node.className.contains("Button", ignoreCase = true)) 1 else 0
            }.thenBy { node ->
                node.bounds.width().toLong() * node.bounds.height()
            },
        ).firstOrNull()
    }

    /** Accessibility / bounded UiDevice first; dump+input tap only if those miss. */
    private fun clickInstagramLabeledRow(labels: List<String>): Boolean {
        val normalized = labels.map { value -> value.trim().lowercase(Locale.ROOT) }.toSet()
        if (performAccessibilityClick { node, _ ->
                accessibilityLabels(node).any(normalized::contains)
            }
        ) {
            return true
        }
        if (uiQueryBounded(false) { clickInstagramVisibleSettingsRow(labels) }) {
            return true
        }
        return clickInstagramLabelViaShellDump(labels)
    }

    private fun finalizeInstagramCommentsSurface(): Boolean {
        val deadline = SystemClock.elapsedRealtime() + INSTAGRAM_COMMENTS_LIST_WAIT_MS
        while (SystemClock.elapsedRealtime() < deadline) {
            SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
            invalidateShellDumpCache()
            if (instagramCommentsListChromeVisible()) {
                markInstagramOwnAccountVerified()
                debugMapper.capture(
                    "instagram_comments_list_ready",
                    SocialScope.OWN_COMMENTS,
                    "verified",
                )
                return true
            }
        }
        return fail("instagram_comments_not_ready")
    }

    private fun advanceInstagramComments(): Boolean {
        if (!isForeground(INSTAGRAM_PACKAGE)) return false
        dismissInstagramCommentsCoachmark()
        dismissInstagramCommentsFilterSheet()
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        // Keep swipe in the comment rows — top ~30% has "All dates" chips that open
        // the Filter-by-date sheet (session 4b42ad0f false-positive captures).
        val swiped = try {
            safeSwipe(
                bounds.centerX(),
                bounds.top + (bounds.height() * 78) / 100,
                bounds.centerX(),
                bounds.top + (bounds.height() * 42) / 100,
                SWIPE_STEPS,
            )
        } catch (_: RuntimeException) {
            false
        }
        SystemClock.sleep(INSTAGRAM_SCROLL_SETTLE_MS)
        dismissInstagramCommentsCoachmark()
        dismissInstagramCommentsFilterSheet()
        val onList = isInstagramCommentsListSurface()
        debugMapper.capture(
            "instagram_comments_after_scroll",
            SocialScope.OWN_COMMENTS,
            when {
                swiped && onList -> "observed"
                swiped && !onList -> "filter_sheet"
                else -> "failed"
            },
        )
        return swiped && onList
    }

    /** True when Comments list (not Filter-by-date sheet) is showing. */
    private fun isInstagramCommentsListSurface(): Boolean {
        if (instagramCommentsFilterSheetVisible()) return false
        return instagramCommentsListChromeVisible()
    }

    /** Comments list chrome: UiDevice first, dump fallback. */
    private fun instagramCommentsListChromeVisible(): Boolean {
        val viaDevice = safeUi(false) {
            hasExactLabel(COMMENTS_LABELS) &&
                hasExactLabel(INSTAGRAM_COMMENTS_LIST_CHROME_LABELS)
        }
        if (viaDevice) return true
        invalidateShellDumpCache()
        if (!shellDumpHasAnyLabel(COMMENTS_LABELS)) return false
        return shellDumpHasAnyLabel(INSTAGRAM_COMMENTS_LIST_CHROME_LABELS)
    }

    private fun instagramCommentsFilterSheetVisible(): Boolean {
        val labels = listOf(
            "Filter by date",
            "Filter by Date",
            "Filter tanggal",
            "Past week",
            "Past month",
            "Past year",
            "Date range",
            "Minggu lalu",
            "Bulan lalu",
        )
        if (safeUi(false) { hasExactLabel(labels) }) return true
        return shellDumpHasAnyLabel(labels)
    }

    private fun dismissInstagramCommentsFilterSheet(): Boolean {
        if (!instagramCommentsFilterSheetVisible()) return false
        Log.i(LOG_TAG, "event=instagram_comments_filter_sheet_dismiss")
        safePressBack()
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        invalidateShellDumpCache()
        if (!instagramCommentsFilterSheetVisible()) return true
        val bounds = activeWindowBounds()
        safeClickPoint(bounds.centerX(), bounds.top + bounds.height() / 8)
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        invalidateShellDumpCache()
        return !instagramCommentsFilterSheetVisible()
    }

    private fun instagramBottomTabsVisible(): Boolean = safeUi(false) {
        device.hasObject(By.res(INSTAGRAM_PACKAGE, "profile_tab")) ||
            device.hasObject(By.res(INSTAGRAM_PACKAGE, "feed_tab")) ||
            device.hasObject(By.desc("Profile")) ||
            device.hasObject(By.desc("Profil")) ||
            device.hasObject(By.desc("Home")) ||
            device.hasObject(By.desc("Beranda"))
    }

    private fun dismissInstagramCommentsCoachmark(): Boolean {
        return safeUi(false) {
            val tip = device.findObject(By.text("Delete multiple comments."))
                ?: device.findObject(By.textContains("Delete multiple comments"))
                ?: device.findObject(By.textContains("Hapus beberapa komentar"))
            if (tip != null) {
                val bounds = safeBounds(tip)
                if (bounds != null) {
                    safeClickPoint(
                        bounds.centerX(),
                        (bounds.bottom + 48).coerceAtMost(device.displayHeight - 2),
                    )
                    SystemClock.sleep(150L)
                    return@safeUi true
                }
            }
            false
        }
    }

    /** Prefer UiDevice / shell dump for Compose Settings rows (same idea as Archive). */
    private fun clickInstagramSettingsLabel(labels: List<String>): Boolean {
        for (label in labels) {
            val obj = device.findObject(By.text(label))
                ?: device.findObject(By.desc(label))
                ?: continue
            if (safeClick(obj)) return true
            val bounds = safeBounds(obj) ?: continue
            if (safeClickPoint(bounds.centerX(), bounds.centerY())) return true
        }
        // Compose Settings: shell dump before any scroll. Session bdfcb21f had Archive
        // visible, then clickExactTextWithScroll (DOWN) hid it → archive_not_found.
        if (clickInstagramLabelViaShellDump(labels)) return true
        // Scroll upward only — Archive / Your activity sit near the top.
        if (clickExactTextWithScrollBackward(labels, MENU_SCROLL_LIMIT)) return true
        invalidateShellDumpCache()
        return clickInstagramLabelViaShellDump(labels)
    }

    /**
     * Settings list may open scrolled to Login / Also from Meta. Archive sits near
     * the top under "How you use Instagram" — swipe finger-down to reveal it
     * (same recovery used by archive-scroll-probe when mapping Settings).
     */
    private fun ensureInstagramSettingsNearTop() {
        val topSignals = ARCHIVE_LABELS + YOUR_ACTIVITY_LABELS + listOf(
            "How you use Instagram",
            "Cara Anda menggunakan Instagram",
            "Saved",
            "Disimpan",
            "Close Friends",
            "Teman Dekat",
        )
        val bottomSignals = listOf(
            "Log out",
            "Keluar",
            "Also from Meta",
            "Juga dari Meta",
            "Add account",
            "Tambahkan akun",
            "Account Status",
            "Status akun",
        )
        repeat(8) { attempt ->
            val atBottom = hasExactLabel(bottomSignals) || shellDumpHasAnyLabel(bottomSignals)
            if (atBottom) {
                invalidateShellDumpCache()
                if (!swipeBackward()) return
                SystemClock.sleep(280L)
                return@repeat
            }
            if (hasExactLabel(topSignals) || shellDumpHasAnyLabel(ARCHIVE_LABELS)) {
                Log.i(LOG_TAG, "event=instagram_settings_near_top attempt=$attempt")
                return
            }
            if (attempt >= 2) {
                Log.i(LOG_TAG, "event=instagram_settings_scroll_stop attempt=$attempt")
                return
            }
            invalidateShellDumpCache()
            if (!swipeBackward()) return
            SystemClock.sleep(280L)
        }
    }

    private fun instagramOptionsMenuAlreadyOpen(): Boolean =
        uiQueryBounded(false) { instagramOptionsMenuReadyUiDevice() } ||
            instagramOptionsMenuReadyShell()

    /**
     * Probe-aligned Options open:
     * click top-band Options → wait ≤12s for Archive/Saved → never treat profile Search as Settings.
     */
    private fun clickInstagramOptions(): Boolean {
        if (instagramOptionsMenuAlreadyOpen()) {
            Log.i(LOG_TAG, "event=instagram_options_already_open success=true")
            markInstagramOwnAccountVerified()
            return true
        }
        if (!hasInstagramOwnProfileProof()) {
            Log.i(LOG_TAG, "event=instagram_options_not_on_profile_try_deeplink")
            return openInstagramSettingsViaDeepLink()
        }
        ensureInstagramProfileHeaderForMenu()
        repeat(INSTAGRAM_MENU_OPEN_ATTEMPTS) { attempt ->
            if (instagramOptionsMenuAlreadyOpen()) {
                markInstagramOwnAccountVerified()
                return true
            }
            debugMapper.capture("instagram_options_before_click", activeScope)
            val clicked = clickInstagramOptionsByResource() ||
                clickInstagramOptionsUiDevice() ||
                clickInstagramOptionsAccessibility() ||
                clickInstagramProfileMenuCoordinate() ||
                clickInstagramOptionsShellTap()
            if (!clicked) {
                Log.i(LOG_TAG, "event=instagram_options_click_missed attempt=$attempt")
                return@repeat
            }
            SystemClock.sleep(INSTAGRAM_SETTINGS_LOAD_SETTLE_MS)
            debugMapper.capture("instagram_options_after_click", activeScope)
            // Probe waitUntilA11yHasAny(~12s) for Archive / Saved / Settings labels.
            if (waitForInstagramOptionsMenuReady()) {
                Log.i(LOG_TAG, "event=instagram_options_menu_ready attempt=$attempt")
                markInstagramOwnAccountVerified()
                return true
            }
            // Still on profile → miss; do not Back-thrash Settings skeleton.
            if (hasInstagramOwnProfileProof()) {
                Log.i(LOG_TAG, "event=instagram_options_still_on_profile attempt=$attempt")
                return@repeat
            }
            // Left profile but labels still loading — Search fallback can proceed.
            if (instagramSettingsHasSearchControl() || !hasInstagramOwnProfileProof()) {
                Log.i(LOG_TAG, "event=instagram_options_off_profile_ready_for_search attempt=$attempt")
                return true
            }
        }
        // Xiaomi MIUI blocks UiObject2/UiDevice/a11y clicks on the Opsi node.
        // instagram://settings opens the same "Pengaturan dan aktivitas" sheet
        // (Arsip / Tersimpan / Aktivitas Anda). Infinix never reaches this.
        if (openInstagramSettingsViaDeepLink()) return true
        return instagramOptionsMenuAlreadyOpen()
    }

    private fun openInstagramSettingsViaDeepLink(): Boolean {
        debugMapper.capture("instagram_settings_deeplink_start", activeScope)
        val launched = try {
            device.executeShellCommand(
                "am start -a android.intent.action.VIEW " +
                    "-d instagram://settings -p $INSTAGRAM_PACKAGE",
            )
            true
        } catch (_: RuntimeException) {
            false
        }
        Log.i(LOG_TAG, "event=instagram_settings_deeplink launched=$launched")
        if (!launched) return false
        SystemClock.sleep(INSTAGRAM_SETTINGS_LOAD_SETTLE_MS + 1_200L)
        debugMapper.capture("instagram_settings_deeplink_after", activeScope)
        if (waitForInstagramOptionsMenuReady() || instagramOptionsMenuAlreadyOpen()) {
            Log.i(LOG_TAG, "event=instagram_settings_deeplink success=true")
            markInstagramOwnAccountVerified()
            return true
        }
        Log.i(LOG_TAG, "event=instagram_settings_deeplink success=false")
        return false
    }

    /** Probe: after posts scroll, swipe back so Options hamburger is reachable. */
    private fun ensureInstagramProfileHeaderForMenu() {
        if (instagramOptionsVisibleOnDevice()) return
        repeat(3) {
            val bounds = activeWindowBounds()
            if (bounds.height() <= 0) return
            safeSwipe(
                bounds.centerX(),
                bounds.top + (bounds.height() * 28) / 100,
                bounds.centerX(),
                bounds.top + (bounds.height() * 72) / 100,
                SWIPE_STEPS,
            )
            SystemClock.sleep(250)
            if (instagramOptionsVisibleOnDevice()) return
        }
    }

    private fun instagramOptionsVisibleOnDevice(): Boolean = safeUi(false) {
        val maxTop = (device.displayHeight * 0.22).toInt()
        val candidates = device.findObjects(By.desc("Options")) +
            device.findObjects(By.desc("Opsi")) +
            device.findObjects(By.descContains("Options")) +
            device.findObjects(By.descContains("Opsi"))
        candidates.any { obj ->
            val b = safeBounds(obj) ?: return@any false
            b.top < maxTop
        }
    }

    private fun clickInstagramOptionsByResource(): Boolean = safeUi(false) {
        val maxTop = (device.displayHeight * 0.22).toInt()
        for (resource in INSTAGRAM_OPTIONS_RESOURCES) {
            val obj = device.findObject(By.res(INSTAGRAM_PACKAGE, resource)) ?: continue
            val bounds = safeBounds(obj) ?: continue
            if (bounds.top >= maxTop) continue
            if (safeClick(obj) || safeClickPoint(bounds.centerX(), bounds.centerY())) {
                Log.i(LOG_TAG, "event=instagram_options_resource_click resource=$resource")
                return@safeUi true
            }
        }
        false
    }

    private fun clickInstagramOptionsUiDevice(): Boolean = safeUi(false) {
        // Match FlowMapFinalProbe: top band only — do not require right-edge geometry.
        val maxTop = (device.displayHeight * 0.22).toInt()
        val candidates = device.findObjects(By.desc("Options")) +
            device.findObjects(By.desc("Opsi")) +
            device.findObjects(By.descContains("Options")) +
            device.findObjects(By.descContains("Opsi")) +
            device.findObjects(By.desc("More options")) +
            device.findObjects(By.desc("Opsi lainnya"))
        val top = candidates
            .mapNotNull { obj ->
                val b = safeBounds(obj) ?: return@mapNotNull null
                obj to b
            }
            .filter { (_, b) -> b.top < maxTop }
            .minByOrNull { (_, b) -> b.top }
            ?.first
        if (top != null) {
            // Capture bounds before click — Xiaomi recycles the UiObject2 so
            // later visibleBounds is null and the point fallback never runs.
            val savedBounds = safeBounds(top)
            var node: UiObject2? = top
            var objectClicked = false
            repeat(6) {
                val cur = node ?: return@repeat
                if (cur.isClickable && safeClick(cur)) {
                    objectClicked = true
                    return@repeat
                }
                node = cur.parent
            }
            if (!objectClicked) {
                objectClicked = safeClick(top)
            }
            SystemClock.sleep(400)
            // Infinix: object-click already opened Settings — do not point-click Search.
            if (instagramOptionsMenuReadyUiDevice()) {
                Log.i(LOG_TAG, "event=instagram_options_uidevice_click success=true via=object")
                return@safeUi true
            }
            val pointBounds = savedBounds ?: safeBounds(device.findObject(By.desc("Opsi")))
            if (pointBounds != null) {
                val x = pointBounds.centerX()
                val y = pointBounds.centerY()
                val pointed = safeClickPoint(x, y)
                Log.i(
                    LOG_TAG,
                    "event=instagram_options_uidevice_point success=$pointed x=$x y=$y " +
                        "object_click=$objectClicked saved=${savedBounds != null}",
                )
                if (pointed) return@safeUi true
            } else {
                Log.i(LOG_TAG, "event=instagram_options_uidevice_point skipped=null_bounds")
            }
            // Do not report success if Settings did not open. Xiaomi rejects
            // UiDevice.click at the Opsi center (success=false x=992 y=170);
            // returning true here skipped the accessibility click path.
            Log.i(LOG_TAG, "event=instagram_options_uidevice_click success=false sheet_closed")
        }
        false
    }

    /** Wait until Settings shows Archive / Your activity / companions. */
    private fun waitForInstagramOptionsMenuReady(): Boolean {
        val started = SystemClock.elapsedRealtime()
        val deadline = started + INSTAGRAM_OPTIONS_MENU_WAIT_MS
        var lastShellAt = 0L
        while (SystemClock.elapsedRealtime() < deadline) {
            if (instagramOptionsMenuReadyUiDevice()) return true
            // Click missed: still on profile with Options visible — don't sit 12s idle.
            if (
                SystemClock.elapsedRealtime() - started >= 2_000L &&
                hasInstagramOwnProfileProof() &&
                instagramOptionsVisibleOnDevice()
            ) {
                Log.i(LOG_TAG, "event=instagram_options_wait_aborted_still_on_profile")
                return false
            }
            // Shell dump at most every 2.5s — never every poll (Infinix hang risk).
            val now = SystemClock.elapsedRealtime()
            if (now - lastShellAt >= SHELL_DUMP_POLL_MIN_MS) {
                lastShellAt = now
                if (instagramOptionsMenuReadyShell()) return true
            }
            if (navigationExpired()) return false
            SystemClock.sleep(INSTAGRAM_ARCHIVE_PROBE_INTERVAL_MS)
        }
        return instagramOptionsMenuReadyUiDevice() || instagramOptionsMenuReadyShell()
    }

    private fun instagramOptionsMenuReadyNow(): Boolean =
        instagramOptionsMenuReadyUiDevice() || instagramOptionsMenuReadyShell()

    private fun instagramOptionsMenuReadyUiDevice(): Boolean =
        device.hasObject(By.text("Archive")) ||
            device.hasObject(By.text("Arsip")) ||
            device.hasObject(By.desc("Archive")) ||
            device.hasObject(By.desc("Arsip")) ||
            device.hasObject(By.text("Your activity")) ||
            device.hasObject(By.text("Aktivitas Anda")) ||
            device.hasObject(By.text("Saved")) ||
            device.hasObject(By.text("Disimpan")) ||
            device.hasObject(By.text("Settings and activity")) ||
            device.hasObject(By.text("Setelan dan aktivitas")) ||
            device.hasObject(By.text("Pengaturan dan aktivitas")) ||
            instagramOptionsMenuVisible(instagramProbeNodes())

    private fun instagramOptionsMenuReadyShell(): Boolean {
        if (shellDumpHasAnyLabel(ARCHIVE_LABELS)) return true
        if (shellDumpHasAnyLabel(YOUR_ACTIVITY_LABELS)) return true
        if (shellDumpHasAnyLabel(INSTAGRAM_OPTIONS_MENU_COMPANION_FLAT)) return true
        return false
    }

    private fun instagramSettingsHasSearchControl(): Boolean = safeUi(false) {
        device.hasObject(By.text("Search")) ||
            device.hasObject(By.text("Cari")) ||
            device.hasObject(By.desc("Search")) ||
            device.hasObject(By.desc("Cari")) ||
            device.hasObject(By.descContains("Search")) ||
            device.hasObject(By.descContains("Cari"))
    }

    private fun clickInstagramOptionsAccessibility(): Boolean =
        performAccessibilityClick { node, bounds ->
            val labels = accessibilityLabels(node)
            bounds.top < (device.displayHeight * 0.22).toInt() &&
                labels.any { label -> label in INSTAGRAM_OPTIONS_NORMALIZED_LABELS }
        }

    private fun clickInstagramProfileMenuCoordinate(): Boolean {
        val bounds = activeWindowBounds()
        val density = context.resources.displayMetrics.density
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        val x = (bounds.right - (24 * density).toInt()).coerceAtLeast(bounds.left)
        val y = (bounds.top + (34 * density).toInt()).coerceAtMost(bounds.bottom - 1)
        if (!safeClickPoint(x, y)) return false
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        return true
    }

    /**
     * Last resort inside the automation APK (same path as Compose comments).
     * Xiaomi rejects UiObject2.click and UiDevice.click at the Opsi node.
     */
    private fun clickInstagramOptionsShellTap(): Boolean {
        val maxTop = (device.displayHeight * 0.22).toInt()
        val node = safeUi(null as UiObject2?) {
            val candidates = device.findObjects(By.desc("Opsi")) +
                device.findObjects(By.desc("Options"))
            candidates
                .mapNotNull { obj ->
                    val b = safeBounds(obj) ?: return@mapNotNull null
                    obj to b
                }
                .filter { (_, b) -> b.top < maxTop }
                .minByOrNull { (_, b) -> b.top }
                ?.first
        } ?: return false
        val bounds = safeBounds(node) ?: return false
        val x = bounds.centerX()
        val y = bounds.centerY()
        val tapped = shellTap(x, y)
        Log.i(LOG_TAG, "event=instagram_options_shell_tap success=$tapped x=$x y=$y")
        if (!tapped) return false
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        return true
    }

    private fun revealInstagramSettingsRow(labels: List<String>) {
        if (hasExactLabel(labels)) return
        val remaining = navigationRemainingMs()
        if (remaining > 800L) {
            SystemClock.sleep(minOf(INSTAGRAM_SETTINGS_VISIBLE_SETTLE_MS, remaining))
        }
        if (hasExactLabel(labels)) return
        ensureInstagramSettingsNearTop()
    }

    private fun clickInstagramVisibleSettingsRow(labels: List<String>): Boolean {
        val expected = labels.map { it.trim().lowercase(Locale.ROOT) }.toSet()
        val deviceBounds = safeUi(null as Rect?) {
            for (label in labels) {
                val obj = device.findObject(By.text(label))
                    ?: device.findObject(By.desc(label))
                    ?: continue
                val raw = sequenceOf(obj.text, obj.contentDescription)
                    .filterNotNull()
                    .map { it.trim() }
                    .firstOrNull()
                    .orEmpty()
                if (raw.isNotEmpty() &&
                    expected.none { value -> raw.equals(value, ignoreCase = true) }
                ) {
                    continue
                }
                return@safeUi safeBounds(obj)
            }
            null
        }
        if (deviceBounds != null && clickInstagramSettingsRowBounds(deviceBounds)) {
            Log.i(LOG_TAG, "event=instagram_settings_row_click via=uidevice")
            return true
        }
        invalidateShellDumpCache()
        val dumpBounds = shellDumpProbeNodes(labels)
            .minByOrNull { node ->
                node.bounds.width().toLong().coerceAtLeast(1L) *
                    node.bounds.height().toLong().coerceAtLeast(1L)
            }
            ?.bounds
        if (dumpBounds != null && clickInstagramSettingsRowBounds(dumpBounds)) {
            Log.i(LOG_TAG, "event=instagram_settings_row_click via=shell_dump")
            return true
        }
        return false
    }

    private fun clickInstagramSettingsRowBounds(labelBounds: Rect): Boolean {
        if (labelBounds.width() <= 0 || labelBounds.height() <= 0) return false
        val density = context.resources.displayMetrics.density
        val window = activeWindowBounds()
        val iconX = (labelBounds.left - (36 * density).toInt())
            .coerceIn(window.left + 16, (labelBounds.centerX()).coerceAtLeast(window.left + 16))
        val y = labelBounds.centerY().coerceIn(window.top + 1, window.bottom - 1)
        if (safeClickPoint(iconX, y)) return true
        val centerX = ((window.left + window.right) / 2)
            .coerceIn(window.left + 8, window.right - 8)
        return safeClickPoint(centerX, y)
    }

    private fun clickInstagramCommentsActivityFallback(): Boolean {
        if (clickInstagramLabelViaShellDump(YOUR_ACTIVITY_LABELS)) return true
        return clickInstagramLabelViaSettingsSearch(
            queries = listOf("Your activity", "Aktivitas Anda"),
            labels = YOUR_ACTIVITY_LABELS,
            eventPrefix = "instagram_activity",
        )
    }

    private fun clickInstagramArchiveMenuEntry(): Boolean {
        val attempts = listOf<() -> Boolean>(
            { clickInstagramArchiveViaAccessibility() },
            { clickInstagramVisibleSettingsRow(ARCHIVE_LABELS) },
            { clickInstagramSettingsLabel(ARCHIVE_LABELS) },
            { clickInstagramArchiveUiDevice() },
            { clickInstagramLabelViaShellDump(ARCHIVE_LABELS) },
            { clickInstagramArchiveViaSettingsSearch() },
        )
        for (attempt in attempts) {
            if (!attempt()) continue
            SystemClock.sleep(INSTAGRAM_ARCHIVE_LOAD_SETTLE_MS)
            if (!instagramOptionsMenuAlreadyOpen()) return true
            Log.i(LOG_TAG, "event=instagram_archive_click_still_on_settings")
        }
        return false
    }

    private fun clickInstagramArchiveViaAccessibility(): Boolean {
        val window = activeWindowBounds()
        return performAccessibilityClick { node, bounds ->
            bounds.top >= window.top &&
                accessibilityLabels(node).any { label -> label in ARCHIVE_NORMALIZED_LABELS }
        }
    }

    private fun clickInstagramArchiveUiDevice(): Boolean = safeUi(false) {
        val selectors = listOf(
            By.text("Archive"),
            By.text("Arsip"),
            By.desc("Archive"),
            By.desc("Arsip"),
            By.textContains("Archive"),
            By.textContains("Arsip"),
            By.descContains("Archive"),
            By.descContains("Arsip"),
        )
        for (selector in selectors) {
            val obj = device.findObject(selector) ?: continue
            val label = sequenceOf(obj.text, obj.contentDescription)
                .filterNotNull()
                .map { it.trim() }
                .firstOrNull()
                .orEmpty()
            // Avoid matching unrelated "Archived" chat rows if any.
            if (label.isNotEmpty() &&
                ARCHIVE_NORMALIZED_LABELS.none { expected ->
                    label.equals(expected, ignoreCase = true) ||
                        label.lowercase(Locale.ROOT).startsWith("$expected ")
                }
            ) {
                continue
            }
            if (safeClick(obj)) {
                Log.i(LOG_TAG, "event=instagram_archive_uidevice_click success=true")
                return@safeUi true
            }
            val bounds = safeBounds(obj) ?: continue
            if (safeClickPoint(bounds.centerX(), bounds.centerY())) {
                Log.i(LOG_TAG, "event=instagram_archive_uidevice_point success=true")
                return@safeUi true
            }
        }
        false
    }

    private fun waitForInstagramArchivePageNodes(): List<InstagramProbeNode>? {
        repeat(INSTAGRAM_ARCHIVE_PROBE_ATTEMPTS) {
            val nodes = instagramProbeNodes()
            if (instagramArchivePageVisible(nodes)) return nodes
            if (navigationExpired()) return null
            SystemClock.sleep(INSTAGRAM_ARCHIVE_PROBE_INTERVAL_MS)
        }
        val nodes = instagramProbeNodes()
        return nodes.takeIf(::instagramArchivePageVisible)
    }

    private fun instagramArchivePageVisible(nodes: List<InstagramProbeNode>): Boolean =
        isForeground(INSTAGRAM_PACKAGE) &&
            !instagramOptionsMenuAlreadyOpen() &&
            instagramHasHeaderLabel(INSTAGRAM_ARCHIVE_PAGE_LABELS, nodes)

    private fun switchInstagramArchiveToStories(
        initialNodes: List<InstagramProbeNode>,
    ): Boolean {
        val bounds = activeWindowBounds()
        val target = instagramExactNode(
            INSTAGRAM_NON_STORY_ARCHIVE_LABELS + ARCHIVE_LABELS,
            initialNodes,
            bounds.top,
            bounds.top + bounds.height() / 3,
        ) ?: return false
        val dropdownClicked = performAccessibilityClick { node, nodeBounds ->
            nodeBounds.top <= bounds.top + bounds.height() / 3 &&
                accessibilityLabels(node).any { label ->
                    label in INSTAGRAM_ARCHIVE_HEADER_NORMALIZED_LABELS
                }
        } || safeClickPoint(target.bounds.centerX(), target.bounds.centerY())
        if (!dropdownClicked) return false
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        debugMapper.capture("instagram_archive_mode_menu_open", SocialScope.OWN_STORY_ARCHIVE)
        val menuNodes = instagramProbeNodes()
        val storyTarget = instagramExactNode(
            STORY_ARCHIVE_LABELS,
            menuNodes,
            bounds.top,
            bounds.bottom,
        ) ?: return false
        val storyClicked = performAccessibilityClick { node, _ ->
            accessibilityLabels(node).any { label -> label in STORY_ARCHIVE_NORMALIZED_LABELS }
        } || safeClickPoint(storyTarget.bounds.centerX(), storyTarget.bounds.centerY())
        if (!storyClicked) return false
        SystemClock.sleep(INSTAGRAM_ARCHIVE_LOAD_SETTLE_MS)
        debugMapper.capture("instagram_story_archive_mode_selected", SocialScope.OWN_STORY_ARCHIVE)
        val storyNodes = waitForInstagramArchivePageNodes() ?: return false
        return instagramHasHeaderLabel(STORY_ARCHIVE_LABELS, storyNodes)
    }

    private fun waitForInstagramOptionsMenuNodes(): List<InstagramProbeNode>? {
        var lastShellAt = 0L
        repeat(INSTAGRAM_ARCHIVE_PROBE_ATTEMPTS) {
            val nodes = instagramProbeNodes()
            if (instagramOptionsMenuVisible(nodes)) return nodes
            // Shell sparsely — Compose Settings; avoid dump-every-poll hang on Infinix.
            val now = SystemClock.elapsedRealtime()
            if (now - lastShellAt >= SHELL_DUMP_POLL_MIN_MS) {
                lastShellAt = now
                if (shellDumpHasAnyLabel(ARCHIVE_LABELS) &&
                    shellDumpHasAnyLabel(INSTAGRAM_OPTIONS_MENU_COMPANION_FLAT)
                ) {
                    return nodes.ifEmpty { shellDumpProbeNodes(ARCHIVE_LABELS) }
                }
            }
            if (navigationExpired()) return null
            SystemClock.sleep(INSTAGRAM_ARCHIVE_PROBE_INTERVAL_MS)
        }
        val nodes = instagramProbeNodes()
        if (instagramOptionsMenuVisible(nodes)) return nodes
        if (shellDumpHasAnyLabel(ARCHIVE_LABELS) &&
            shellDumpHasAnyLabel(INSTAGRAM_OPTIONS_MENU_COMPANION_FLAT)
        ) {
            return nodes.ifEmpty { shellDumpProbeNodes(ARCHIVE_LABELS) }
        }
        return null
    }

    private fun restoreInstagramOwnProfile(): Boolean {
        if (!isForeground(INSTAGRAM_PACKAGE)) return false
        safePressBack()
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
        if (hasInstagramOwnProfileProof()) return true
        return openInstagramOwnProfile()
    }

    private fun instagramOptionsMenuEntry(
        labels: List<String>,
        nodes: List<InstagramProbeNode>,
    ): InstagramProbeNode? {
        val bounds = activeWindowBounds()
        val density = context.resources.displayMetrics.density
        val minimumTop = bounds.top + maxOf(
            bounds.height() / 10,
            (72 * density).toInt(),
        )
        return instagramExactNode(labels, nodes, minimumTop, bounds.bottom)
    }

    private fun instagramOptionsMenuVisible(nodes: List<InstagramProbeNode>): Boolean =
        isForeground(INSTAGRAM_PACKAGE) &&
            instagramOptionsMenuEntry(ARCHIVE_LABELS, nodes) != null &&
            INSTAGRAM_OPTIONS_MENU_COMPANION_LABELS.any { labels ->
                instagramOptionsMenuEntry(labels, nodes) != null
            }

    private fun navigateX(scope: SocialScope): Boolean {
        if (scope !in setOf(
                SocialScope.OWN_PROFILE,
                SocialScope.OWN_TWEETS,
                SocialScope.OWN_REPLIES,
            )
        ) {
            return fail("x_scope_unsupported")
        }
        if (!openXOwnProfile()) {
            if (failureReason == null) return fail("x_profile_not_verified")
            return false
        }
        if (scope == SocialScope.OWN_PROFILE) return true
        dismissXChromeOverlays()
        val labels = if (scope == SocialScope.OWN_TWEETS) X_POSTS_LABELS else X_REPLIES_LABELS
        if (!ensureXProfileTabsVisible() || !clickXProfileTab(labels)) {
            return fail("x_profile_tab_not_found")
        }
        dismissXChromeOverlays()
        xTimelineActive = true
        if (!waitForXTimeline(scope) ||
            X_PACKAGE !in verifiedOwnAccountPackages ||
            xOwnAccountMarker == null
        ) {
            xTimelineActive = false
            return fail("x_profile_tab_not_verified")
        }
        val visited = xVisitedViewportSignatures.getOrPut(scope) { mutableSetOf() }
        xViewportSignature(scope)?.let { signature ->
            visited.add(signature)
            xCurrentViewportSignatures[scope] = signature
        }
        return true
    }

    private fun openXOwnProfile(): Boolean {
        if (looksLikeSignedOutSession(X_PACKAGE)) {
            return fail("account_not_signed_in")
        }
        if (
            X_PACKAGE in verifiedOwnAccountPackages &&
            xOwnAccountMarker != null &&
            isForeground(X_PACKAGE) &&
            ensureXProfileTabsVisible()
        ) {
            return true
        }
        if (hasXOwnProfileProof()) {
            val marker = xAccountMarker() ?: accountMarker(X_PACKAGE, emptyList())
            if (marker != null) {
                verifiedOwnAccountPackages.add(X_PACKAGE)
                xOwnAccountMarker = marker
                Log.i(LOG_TAG, "event=x_profile_already_open marker=$marker")
                return true
            }
        }
        repeat(MAX_BACK_NAVIGATION + 2) { attempt ->
            if (navigationExpired()) return fail("x_navigation_deadline")
            debugMapper.capture("x_profile_nav_attempt_$attempt", SocialScope.OWN_PROFILE)
            Log.i(LOG_TAG, "event=x_profile_nav_attempt attempt=$attempt")
            ensureTextOnlyCoverVisible(X_PACKAGE)
            recoverXFromWrongSurface()

            // Prefer navigation drawer (top-left). Never match tweet "Profile image".
            // No shell dump on this hot path — dump hangs on Infinix home/feed.
            if (xLooksLikeOtherProfile()) {
                safePressBack()
                waitNavigation()
            }
            val openedDrawer = clickXAccountDrawer()
            val openedProfile = when {
                openedDrawer && clickXProfileMenuEntry() -> true
                clickXBottomProfileTab() -> true
                else -> false
            }
            if (openedProfile && waitForXOwnProfileProof()) {
                val marker = xAccountMarker() ?: accountMarker(X_PACKAGE, emptyList())
                if (marker != null) {
                    verifiedOwnAccountPackages.add(X_PACKAGE)
                    xOwnAccountMarker = marker
                    Log.i(LOG_TAG, "event=x_profile_verified marker=$marker attempt=$attempt")
                    debugMapper.capture("x_profile_verified", SocialScope.OWN_PROFILE, "verified")
                    return true
                }
            }
            // Only Back when a sheet/drawer is open — not while idling on Home.
            if (hasXProfileMenuEntry() || openedDrawer) {
                safePressBack()
                waitNavigation()
            } else if (attempt < MAX_BACK_NAVIGATION && isXHomeTimelineVisible()) {
                Log.i(LOG_TAG, "event=x_profile_still_on_home attempt=$attempt")
            }
        }
        return fail("x_profile_not_verified")
    }

    private fun isXHomeTimelineVisible(): Boolean = safeUi(false) {
        device.hasObject(By.desc("For you")) ||
            device.hasObject(By.text("For you")) ||
            device.hasObject(By.text("Untuk Anda")) ||
            device.hasObject(By.desc("Following")) ||
            device.hasObject(By.text("Following")) ||
            device.hasObject(By.text("Mengikuti")) ||
            device.hasObject(By.desc("Home")) ||
            device.hasObject(By.desc("Beranda")) ||
            device.hasObject(By.descContains("Home timeline")) ||
            device.hasObject(By.res(X_PACKAGE, "channels"))
    }

    private fun clickXAccountDrawer(): Boolean {
        val drawerLabels = setOf(
            "show navigation drawer",
            "tampilkan laci navigasi",
            "tampilkan panel navigasi",
            "tampilkan penarik navigasi",
        )
        val appBarBottom = (device.displayHeight / 5).coerceAtLeast(280)
        val leadingRight = (device.displayWidth / 4).coerceAtLeast(200)
        fun drawerGeometry(bounds: android.graphics.Rect): Boolean =
            bounds.top < appBarBottom &&
                bounds.left < leadingRight &&
                bounds.width() in 48..220 &&
                bounds.height() in 48..220
        fun labeledDrawer(node: AccessibilityNodeInfo, bounds: android.graphics.Rect): Boolean {
            if (!drawerGeometry(bounds)) return false
            val labels = accessibilityLabels(node)
            return labels.any { label ->
                label in drawerLabels ||
                    label.contains("penarik navigasi") ||
                    label.contains("laci navigasi") ||
                    label.contains("navigation drawer") ||
                    label.contains("account menu") ||
                    label.contains("menu akun")
            }
        }
        if (performAccessibilityClick(X_PACKAGE, predicate = ::labeledDrawer)) {
            waitNavigation()
            SystemClock.sleep(400)
            if (hasXProfileMenuEntry() || xDrawerLooksOpen()) {
                Log.i(LOG_TAG, "event=x_drawer_open via=accessibility")
                return true
            }
        }
        // ACTION_CLICK can return true on MIUI without opening the drawer.
        if (
            performAccessibilityClick(
                X_PACKAGE,
                allowActionClick = false,
                predicate = ::labeledDrawer,
            )
        ) {
            waitNavigation()
            SystemClock.sleep(400)
            if (hasXProfileMenuEntry() || xDrawerLooksOpen()) {
                Log.i(LOG_TAG, "event=x_drawer_open via=service_tap")
                return true
            }
        }
        // Exact drawer control first — never Broad "Profile" (matches tweet "Profile image").
        val drawer = safeUi(null) {
            device.findObject(By.desc("Show navigation drawer"))
                ?: device.findObject(By.desc("Tampilkan laci navigasi"))
                ?: device.findObject(By.desc("Tampilkan panel navigasi"))
                ?: device.findObject(By.desc("Tampilkan penarik navigasi"))
                ?: device.findObject(By.descContains("Account menu"))
                ?: device.findObject(By.descContains("Menu akun"))
                ?: device.findObject(By.descContains("Show navigation drawer"))
                ?: device.findObject(By.descContains("Open navigation drawer"))
                ?: device.findObject(By.descContains("navigation drawer"))
                ?: device.findObject(By.descContains("laci navigasi"))
                ?: device.findObject(By.descContains("penarik navigasi"))
        }
        if (drawer != null) {
            val bounds = safeBounds(drawer)
            val clicked = safeClick(drawer) || run {
                val b = bounds ?: return@run false
                safeClickPoint(b.centerX(), b.centerY()) ||
                    shellTap(b.centerX(), b.centerY())
            }
            if (clicked) {
                waitNavigation()
                SystemClock.sleep(400)
                if (hasXProfileMenuEntry() || xDrawerLooksOpen()) {
                    Log.i(LOG_TAG, "event=x_drawer_open via=show_navigation_drawer")
                    return true
                }
            }
            if (bounds != null && a11yServiceTap(bounds.centerX(), bounds.centerY())) {
                waitNavigation()
                SystemClock.sleep(400)
                if (hasXProfileMenuEntry() || xDrawerLooksOpen()) {
                    Log.i(LOG_TAG, "event=x_drawer_open via=show_navigation_drawer_service_tap")
                    return true
                }
            }
        }
        return false
    }

    private fun recoverXFromWrongSurface(): Boolean {
        if (!isForeground(X_PACKAGE)) return false
        var recovered = false
        repeat(3) {
            when {
                xLooksLikeProfileMediaViewer() -> {
                    safePressBack()
                    waitNavigation()
                    SystemClock.sleep(300)
                    recovered = true
                }
                xLooksLikeOtherProfile() -> {
                    safePressBack()
                    waitNavigation()
                    SystemClock.sleep(300)
                    recovered = true
                }
                else -> return recovered
            }
        }
        return recovered
    }

    private fun xLooksLikeProfileMediaViewer(): Boolean = safeUi(false) {
        if (hasXOwnProfileProof() || isXHomeTimelineVisible()) return@safeUi false
        val hasBack = device.hasObject(By.descContains("Back")) ||
            device.hasObject(By.descContains("Kembali")) ||
            device.hasObject(By.descContains("Navigate up"))
        val hasFeedChrome = isXHomeTimelineVisible() ||
            xProfileTabsVisible() ||
            hasXProfileMenuEntry()
        hasBack && !hasFeedChrome && !hasExactLabel(EDIT_PROFILE_LABELS)
    }

    private fun xDrawerLooksOpen(): Boolean = safeUi(false) {
        // UiDevice only — shell dump on Home/feed hangs on Infinix.
        hasExactText(X_PROFILE_LABELS) ||
            X_PROFILE_LABELS.any { label ->
                device.hasObject(By.text(label)) || device.hasObject(By.desc(label))
            } ||
            device.hasObject(By.text("Bookmarks")) ||
            device.hasObject(By.text("Markah")) ||
            device.hasObject(By.text("Premium")) ||
            device.hasObject(By.text("Lists")) ||
            device.hasObject(By.text("Daftar"))
    }

    private fun hasXProfileMenuEntry(): Boolean =
        hasExactText(X_PROFILE_LABELS) ||
            hasExactLabel(X_PROFILE_LABELS) ||
            X_PROFILE_LABELS.any { label ->
                device.hasObject(By.desc(label)) || device.hasObject(By.text(label))
            }

    private fun clickXProfileMenuEntry(): Boolean {
        if (clickExactText(X_PROFILE_LABELS) && !xDrawerLooksOpen()) return true
        if (clickExactDescription(X_PROFILE_LABELS) && !xDrawerLooksOpen()) return true
        fun profileMenuNode(node: AccessibilityNodeInfo, bounds: android.graphics.Rect): Boolean {
            val labels = accessibilityLabels(node)
            if (labels.any { label ->
                    label.contains("profile image") || label.contains("gambar profil")
                }
            ) {
                return false
            }
            if ("profil" !in labels && "profile" !in labels) return false
            // Drawer row, not a tweet avatar or bottom-nav guess.
            return bounds.width() > 8 &&
                bounds.height() > 8 &&
                bounds.centerX() < (device.displayWidth * 2) / 3 &&
                bounds.top < (device.displayHeight * 3) / 4
        }
        if (performAccessibilityClick(X_PACKAGE, predicate = ::profileMenuNode)) {
            waitNavigation()
            if (!xDrawerLooksOpen()) return true
        }
        if (
            performAccessibilityClick(
                X_PACKAGE,
                allowActionClick = false,
                predicate = ::profileMenuNode,
            )
        ) {
            waitNavigation()
            if (!xDrawerLooksOpen()) return true
        }
        for (label in X_PROFILE_LABELS) {
            val target = device.findObject(By.desc(label))
                ?: device.findObject(By.text(label))
                ?: continue
            val desc = target.contentDescription?.toString().orEmpty()
            if (desc.contains("Profile image", ignoreCase = true)) continue
            val clickable = clickableAncestor(target) ?: target
            val bounds = safeBounds(clickable) ?: safeBounds(target)
            if (safeClick(clickable)) {
                waitNavigation()
                if (!xDrawerLooksOpen() && hasXOwnProfileProof()) return true
                if (!xDrawerLooksOpen()) return true
            }
            if (bounds != null && a11yServiceTap(bounds.centerX(), bounds.centerY())) {
                waitNavigation()
                if (!xDrawerLooksOpen() && hasXOwnProfileProof()) return true
                if (!xDrawerLooksOpen()) return true
            }
        }
        return false
    }

    private fun clickXBottomProfileTab(): Boolean {
        val minTop = (device.displayHeight * 4) / 5
        for (label in X_PROFILE_LABELS) {
            val matches = safeUi(emptyList<UiObject2>()) {
                device.findObjects(By.desc(label)) + device.findObjects(By.text(label))
            }
            val target = matches.firstOrNull { value ->
                val desc = value.contentDescription?.toString().orEmpty()
                val bounds = safeBounds(value) ?: return@firstOrNull false
                !desc.contains("Profile image", ignoreCase = true) &&
                    !desc.contains("Gambar profil", ignoreCase = true) &&
                    bounds.top >= minTop
            } ?: continue
            val bounds = safeBounds(target) ?: continue
            val tapped = safeClick(target) ||
                a11yServiceTap(bounds.centerX(), bounds.centerY())
            if (!tapped) continue
            waitNavigation()
            if (hasXOwnProfileProof()) return true
        }
        return false
    }

    private fun xLooksLikeOtherProfile(): Boolean {
        if (!isForeground(X_PACKAGE)) return false
        if (hasExactLabel(EDIT_PROFILE_LABELS)) return false
        if (device.hasObject(By.res(X_PACKAGE, "menu_edit_profile"))) return false
        val followVisible = hasExactLabel(listOf("Follow", "Ikuti", "Subscribe", "Langganan"))
        val messageVisible = hasExactLabel(listOf("Message", "Pesan"))
        return followVisible || (followVisible && messageVisible)
    }

    private fun ensureXProfileTabsVisible(): Boolean {
        if (xProfileTabsVisible()) return true
        if (X_PACKAGE !in verifiedOwnAccountPackages || xOwnAccountMarker == null) return false
        repeat(MAX_X_RETURN_TO_TABS_SWIPES) {
            if (!swipeBackward()) return false
            waitNavigation()
            if (xProfileTabsVisible()) return true
        }
        return xProfileTabsVisible()
    }

    private fun xProfileTabBandBottom(): Int = (device.displayHeight * 4) / 5

    private fun xProfileTabsVisible(): Boolean {
        val maximumTop = xProfileTabBandBottom()
        return boundedLabelCandidates(X_POSTS_LABELS, 0, maximumTop).isNotEmpty() &&
            boundedLabelCandidates(X_REPLIES_LABELS, 0, maximumTop).isNotEmpty()
    }

    private fun clickXProfileTab(labels: List<String>): Boolean {
        dismissXChromeOverlays()
        if (xProfileTabAlreadyActive(labels)) return true
        val maximumTop = xProfileTabBandBottom()
        val candidates = boundedLabelCandidates(labels, 0, maximumTop)
        val target = candidates.minByOrNull { value ->
            safeBounds(value)?.top ?: Int.MAX_VALUE
        } ?: return false
        val clickable = clickableAncestor(target) ?: target
        val bounds = safeBounds(clickable) ?: safeBounds(target)
        val tapped = safeClick(clickable) ||
            (bounds != null && a11yServiceTap(bounds.centerX(), bounds.centerY()))
        if (!tapped) return false
        waitNavigation()
        dismissXChromeOverlays()
        return xProfileTabConfirmed(labels)
    }

    private fun xProfileTabConfirmed(labels: List<String>): Boolean {
        val replies = labels === X_REPLIES_LABELS || labels.any { it in X_REPLIES_LABELS }
        repeat(3) {
            if (replies) {
                if (xTabLooksSelected(X_REPLIES_LABELS) && !xTabLooksSelected(X_POSTS_LABELS)) {
                    return true
                }
                if (xTabLooksSelected(X_REPLIES_LABELS)) return true
                if (
                    !xTabLooksSelected(X_POSTS_LABELS) &&
                    boundedLabelCandidates(X_REPLIES_LABELS, 0, xProfileTabBandBottom()).isNotEmpty()
                ) {
                    return true
                }
            } else {
                if (xTabLooksSelected(X_POSTS_LABELS) && !xTabLooksSelected(X_REPLIES_LABELS)) {
                    return true
                }
                if (
                    !xTabLooksSelected(X_REPLIES_LABELS) &&
                    boundedLabelCandidates(X_POSTS_LABELS, 0, xProfileTabBandBottom()).isNotEmpty()
                ) {
                    return true
                }
            }
            SystemClock.sleep(X_SCROLL_IDLE_MS)
        }
        return if (replies) {
            xTabLooksSelected(X_REPLIES_LABELS)
        } else {
            !xTabLooksSelected(X_REPLIES_LABELS) &&
                boundedLabelCandidates(labels, 0, xProfileTabBandBottom()).isNotEmpty()
        }
    }

    private fun xProfileTabAlreadyActive(labels: List<String>): Boolean {
        if (xHighlightsMenuVisible()) return false
        if (labels === X_REPLIES_LABELS || labels.any { it in X_REPLIES_LABELS }) {
            return xTabLooksSelected(X_REPLIES_LABELS)
        }
        return xTabLooksSelected(X_POSTS_LABELS) && !xTabLooksSelected(X_REPLIES_LABELS)
    }

    private fun xTabLooksSelected(labels: List<String>): Boolean = safeUi(false) {
        labels.any { label ->
            device.findObjects(By.text(label)).any { value -> value.isSelected } ||
                device.findObjects(By.desc(label)).any { value -> value.isSelected } ||
                device.findObjects(By.descContains(label)).any { value ->
                    value.isSelected ||
                        value.contentDescription?.contains("selected", ignoreCase = true) == true
                }
        }
    }

    private fun xHighlightsMenuVisible(): Boolean = safeUi(false) {
        val posts = boundedLabelCandidates(X_POSTS_LABELS, 0, xProfileTabBandBottom())
            .minByOrNull { value -> safeBounds(value)?.top ?: Int.MAX_VALUE }
            ?: return@safeUi false
        val postsBounds = safeBounds(posts) ?: return@safeUi false
        boundedLabelCandidates(
            listOf("Highlights", "Sorotan"),
            postsBounds.bottom - 8,
            postsBounds.bottom + 280,
        ).any { highlight ->
            val bounds = safeBounds(highlight) ?: return@any false
            bounds.top > postsBounds.bottom - 8
        }
    }

    private fun dismissXChromeOverlays() {
        dismissBlockingSystemPrompts()
        if (xHighlightsMenuVisible()) {
            safePressBack()
            waitNavigation()
        }
    }

    private fun waitForXTimeline(scope: SocialScope): Boolean {
        repeat(X_TIMELINE_WAIT_ATTEMPTS) {
            if (isXTimelineSurface(scope) || hasLabelContaining(X_EMPTY_TIMELINE_LABELS)) return true
            device.waitForIdle(NAVIGATION_IDLE_MS)
        }
        return isXTimelineSurface(scope) || hasLabelContaining(X_EMPTY_TIMELINE_LABELS)
    }

    private fun navigateFacebook(scope: SocialScope): Boolean {
        dismissBlockingSystemPrompts()
        ensureTextOnlyCoverVisible(FACEBOOK_PACKAGE)
        advanceFacebookProfilePastOnboarding()
        if (!isForeground(FACEBOOK_PACKAGE)) {
            dismissBlockingSystemPrompts()
            if (!isForeground(FACEBOOK_PACKAGE)) {
                return fail("facebook_not_foreground")
            }
        }
        if (!openFacebookOwnProfile()) {
            if (failureReason == null) return fail("facebook_profile_not_verified")
            return false
        }
        return when (scope) {
            SocialScope.OWN_PROFILE -> true
            SocialScope.OWN_POSTS -> openFacebookOwnPosts()
            SocialScope.OWN_COMMENTS -> openFacebookComments()
            SocialScope.OWN_STORY_ARCHIVE -> openFacebookStoryArchive()
            else -> fail("facebook_scope_unsupported")
        }
    }

    /**
     * Clear heads-up call banners + runtime permission sheets that steal foreground
     * from social apps (FB notification Allow on Infinix → zero FB records).
     */
    private fun dismissBlockingSystemPrompts() {
        repeat(3) { attempt ->
            var acted = false
            // Prefer deny — crawl does not need notification delivery.
            if (
                clickExactText(
                    listOf(
                        "Don’t allow",
                        "Don't allow",
                        "Don’t Allow",
                        "Don't Allow",
                        "Deny",
                        "Jangan izinkan",
                        "Tolak",
                    ),
                ) ||
                clickExactDescription(
                    listOf("Don’t allow", "Don't allow", "Deny", "Jangan izinkan"),
                )
            ) {
                acted = true
                Log.i(LOG_TAG, "event=system_permission_denied attempt=$attempt")
            } else if (foregroundLooksLikePermissionController()) {
                // Unblock if Deny label missed (locale) — Allow still clears the sheet.
                if (
                    clickExactText(
                        listOf(
                            "Don’t allow",
                            "Don't allow",
                            "Jangan izinkan",
                            "Deny",
                            "Tolak",
                        ),
                    )
                ) {
                    acted = true
                } else if (
                    clickExactText(
                        listOf(
                            "Allow",
                            "Izinkan",
                            "While using the app",
                            "Saat menggunakan aplikasi",
                        ),
                    )
                ) {
                    acted = true
                    Log.i(LOG_TAG, "event=system_permission_allowed attempt=$attempt")
                }
            }
            // Incoming WhatsApp / Phone call heads-up blocks taps on the profile tab.
            if (
                clickExactText(listOf("Decline", "Tolak", "Dismiss", "Abaikan")) ||
                clickExactDescription(listOf("Decline", "Tolak", "Dismiss"))
            ) {
                acted = true
                Log.i(LOG_TAG, "event=heads_up_call_declined attempt=$attempt")
            }
            if (foregroundLooksLikePermissionController()) {
                safePressBack()
                acted = true
                Log.i(LOG_TAG, "event=permission_controller_back attempt=$attempt")
            }
            if (foregroundLooksLikeCredentialManager()) {
                safePressBack()
                acted = true
                Log.i(LOG_TAG, "event=credential_manager_back attempt=$attempt")
            }
            if (!acted) return
            SystemClock.sleep(280L)
            waitNavigation()
        }
    }

    private fun dismissCredentialOverlays() {
        repeat(3) { attempt ->
            if (!foregroundLooksLikeCredentialManager()) return
            safePressBack()
            Log.i(LOG_TAG, "event=credential_manager_dismiss attempt=$attempt")
            SystemClock.sleep(280L)
            waitNavigation()
        }
    }

    private fun foregroundLooksLikeCredentialManager(): Boolean {
        val fg = foregroundPackageName().orEmpty()
        return fg == "com.android.credentialmanager" ||
            fg.endsWith(".credentialmanager") ||
            fg.contains("credentialmanager")
    }

    private fun looksLikeSignedOutSession(targetPackage: String): Boolean {
        when (targetPackage) {
            X_PACKAGE -> if (hasXOwnProfileProof()) return false
            INSTAGRAM_PACKAGE -> if (hasInstagramOwnProfileProof()) return false
            FACEBOOK_PACKAGE -> if (hasFacebookOwnProfileProof()) return false
        }
        if (hasExactLabel(EDIT_PROFILE_LABELS)) return false
        return hasExactLabel(AUTH_WALL_LABELS) ||
            hasLabelContaining(AUTH_WALL_FRAGMENTS) ||
            foregroundLooksLikeCredentialManager()
    }

    private fun foregroundLooksLikePermissionController(): Boolean {
        val fg = foregroundPackageName().orEmpty()
        return fg.contains("permissioncontroller") ||
            fg == "com.android.permissioncontroller" ||
            fg == "com.google.android.permissioncontroller"
    }

    private fun dismissFacebookPermissionPrompt() {
        dismissBlockingSystemPrompts()
    }

    /** FB 2025+ profile setup wizard (Xiaomi ID: "Selamat datang di profil Anda"). */
    private fun isFacebookProfileOnboarding(): Boolean {
        if (!isForeground(FACEBOOK_PACKAGE)) return false
        if (hasLabelContaining(FACEBOOK_PROFILE_ONBOARDING_FRAGMENTS)) return true
        if (
            hasExactLabel(FACEBOOK_PROFILE_PHOTO_SETUP_LABELS) &&
            !hasFacebookAllFilter() &&
            !hasExactLabel(listOf("About", "Tentang"))
        ) {
            return true
        }
        return false
    }

    private fun dismissFacebookProfileOnboarding(): Boolean {
        if (!isForeground(FACEBOOK_PACKAGE)) return false
        if (hasLabelContaining(FACEBOOK_PROFILE_SETUP_STOP_FRAGMENTS)) {
            if (
                clickFacebookLabeledControl(
                    FACEBOOK_PROFILE_SETUP_STOP_LABELS,
                    allowActionClick = false,
                ) ||
                clickExactText(FACEBOOK_PROFILE_SETUP_STOP_LABELS) ||
                clickExactDescription(FACEBOOK_PROFILE_SETUP_STOP_LABELS)
            ) {
                waitNavigation()
                SystemClock.sleep(400)
                Log.i(LOG_TAG, "event=facebook_profile_setup_stopped")
                return true
            }
        }
        if (!isFacebookProfileOnboarding()) return false
        if (
            clickFacebookLabeledControl(
                FACEBOOK_PROFILE_ONBOARDING_SKIP_LABELS,
                allowActionClick = false,
            ) ||
            clickExactText(FACEBOOK_PROFILE_ONBOARDING_SKIP_LABELS) ||
            clickExactDescription(FACEBOOK_PROFILE_ONBOARDING_SKIP_LABELS)
        ) {
            waitNavigation()
            SystemClock.sleep(400)
            Log.i(LOG_TAG, "event=facebook_profile_onboarding_skipped")
            return true
        }
        return false
    }

    /** Multi-step setup (photo → city → …) before the real profile wall appears. */
    private fun advanceFacebookProfilePastOnboarding(maxSteps: Int = 8): Boolean {
        var progressed = false
        repeat(maxSteps) {
            if (hasFacebookOwnProfileProof()) return progressed || true
            if (!dismissFacebookProfileOnboarding()) return@repeat
            progressed = true
            dismissBlockingSystemPrompts()
        }
        return progressed
    }

    private fun openFacebookOwnProfile(): Boolean {
        if (
            FACEBOOK_PACKAGE in verifiedOwnAccountPackages &&
            fbOwnAccountMarker != null &&
            isForeground(FACEBOOK_PACKAGE) &&
            hasFacebookOwnProfileProof()
        ) {
            return true
        }
        repeat(MAX_BACK_NAVIGATION + 2) { attempt ->
            if (navigationExpired()) return fail("facebook_navigation_deadline")
            dismissBlockingSystemPrompts()
            advanceFacebookProfilePastOnboarding()
            if (hasFacebookOwnProfileProof()) {
                val marker = fbAccountMarker()
                if (marker != null) {
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    fbOwnAccountMarker = marker
                    Log.i(LOG_TAG, "event=facebook_profile_verified marker=$marker attempt=$attempt")
                    return true
                }
                refreshFacebookAccountMarker(forceShell = true)
                if (fbOwnAccountMarker != null) {
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    Log.i(
                        LOG_TAG,
                        "event=facebook_profile_verified marker=${fbOwnAccountMarker} " +
                            "attempt=$attempt via=shell",
                    )
                    return true
                }
            }
            var opened = clickDescriptionContains(FACEBOOK_PROFILE_TAB_DESC) ||
                clickExactDescription(FACEBOOK_PROFILE_TAB_DESC) ||
                clickBottomNavigation(FACEBOOK_PROFILE_TAB_DESC) ||
                clickFacebookLabeledControl(FACEBOOK_OWN_PROFILE_LABELS) ||
                clickExactText(FACEBOOK_OWN_PROFILE_LABELS) ||
                clickExactDescription(FACEBOOK_OWN_PROFILE_LABELS)
            if (!hasFacebookOwnProfileProof()) {
                opened = clickFacebookLabeledControl(
                    FACEBOOK_OWN_PROFILE_LABELS,
                    allowActionClick = false,
                ) || opened
            }
            if (!hasFacebookOwnProfileProof()) {
                val menuOpened = clickFacebookLabeledControl(FACEBOOK_MENU_LABELS) ||
                    clickExactDescription(FACEBOOK_MENU_LABELS) ||
                    clickFacebookLabeledControl(
                        FACEBOOK_MENU_LABELS,
                        allowActionClick = false,
                    )
                if (menuOpened) {
                    waitNavigation()
                    SystemClock.sleep(350)
                    opened = clickFacebookLabeledControl(FACEBOOK_OWN_PROFILE_LABELS) ||
                        clickExactTextWithScroll(FACEBOOK_OWN_PROFILE_LABELS, MENU_SCROLL_LIMIT) ||
                        clickExactDescription(FACEBOOK_OWN_PROFILE_LABELS) ||
                        clickFacebookLabeledControl(
                            FACEBOOK_OWN_PROFILE_LABELS,
                            allowActionClick = false,
                        ) ||
                        opened
                }
            }
            if (!hasFacebookOwnProfileProof()) {
                opened = clickExactDescription(listOf("Go to profile")) || opened
            }
            if (opened) {
                waitNavigation()
                SystemClock.sleep(450)
                advanceFacebookProfilePastOnboarding()
            }
            if (hasFacebookOwnProfileProof()) {
                val marker = fbAccountMarker()
                if (marker != null) {
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    fbOwnAccountMarker = marker
                    Log.i(LOG_TAG, "event=facebook_profile_verified marker=$marker attempt=$attempt")
                    return true
                }
                refreshFacebookAccountMarker(forceShell = true)
                if (fbOwnAccountMarker != null) {
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    Log.i(
                        LOG_TAG,
                        "event=facebook_profile_verified marker=${fbOwnAccountMarker} " +
                            "attempt=$attempt via=shell",
                    )
                    return true
                }
                // Proof without readable marker yet; posts path refreshes marker.
                if (opened && hasFacebookOwnProfileProof()) {
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    Log.i(LOG_TAG, "event=facebook_profile_verified marker=deferred attempt=$attempt")
                    return true
                }
            }
            if (isFacebookProfileOnboarding()) {
                advanceFacebookProfilePastOnboarding()
            } else if (attempt < MAX_BACK_NAVIGATION) {
                safePressBack()
                waitNavigation()
            }
        }
        return fail("facebook_profile_not_verified")
    }

    private fun openFacebookOwnPosts(): Boolean {
        if (!hasFacebookOwnProfileProof()) return fail("facebook_posts_profile_missing")
        fbActivityPhase = FacebookActivityPhase.NONE
        // Refresh marker from the live profile header before TEXT_ONLY row matching.
        // FB Compose puts the display name on ViewGroup, not TextView.
        refreshFacebookAccountMarker(forceShell = true)
        // Prefer All filter; posts also appear while scrolling the profile feed.
        clickDescriptionContains(FACEBOOK_ALL_FILTER_DESC) ||
            clickExactText(FACEBOOK_ALL_FILTER_LABELS)
        waitNavigation()
        SystemClock.sleep(350)
        fbFeedActive = true
        if (fbOwnAccountMarker == null) {
            // Header may have been scrolled away — infer author from own posts list.
            for (attempt in 0 until 6) {
                val inferred = inferFbMarkerFromOwnPostsSurface()
                if (inferred != null) {
                    fbOwnAccountMarker = inferred
                    verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                    Log.i(
                        LOG_TAG,
                        "event=facebook_marker_inferred marker=$inferred attempt=$attempt",
                    )
                    break
                }
                invalidateShellDumpCache()
                if (!swipeFacebookFeed()) break
                SystemClock.sleep(FB_SCROLL_IDLE_MS)
            }
        }
        if (FACEBOOK_PACKAGE !in verifiedOwnAccountPackages || fbOwnAccountMarker == null) {
            fbFeedActive = false
            return fail("facebook_posts_marker_missing")
        }
        val marker = fbOwnAccountMarker!!
        // Initial viewport is header + "People you may know" — engine aborts the whole
        // scope if the first capture stores nothing, so pre-scroll until own posts appear.
        for (attempt in 0 until 8) {
            val rows = fbPostsRowsFromUiDevice(marker).ifEmpty {
                fbPostsRowsFromShellDump(marker)
            }
            if (rows.isNotEmpty()) {
                Log.i(
                    LOG_TAG,
                    "event=facebook_posts_ready attempt=$attempt rows=${rows.size} marker=$marker",
                )
                break
            }
            invalidateShellDumpCache()
            if (!swipeFacebookFeed()) break
            SystemClock.sleep(FB_SCROLL_IDLE_MS)
        }
        val visited = fbVisitedViewportSignatures.getOrPut(SocialScope.OWN_POSTS) { mutableSetOf() }
        fbViewportSignature(SocialScope.OWN_POSTS)?.let { signature ->
            visited.add(signature)
            fbCurrentViewportSignatures[SocialScope.OWN_POSTS] = signature
        }
        return true
    }

    private fun openFacebookStoryArchive(): Boolean {
        if (!clickExactLabel(listOf("Archive", "Arsip"))) {
            if (!openFacebookMoreProfileSettings()) return false
            if (!clickExactTextWithScroll(listOf("Archive", "Arsip"), MENU_SCROLL_LIMIT)) {
                return false
            }
        }
        if (waitForHeader(STORY_ARCHIVE_LABELS)) return true
        if (!clickExactTextWithScroll(STORY_ARCHIVE_LABELS, MENU_SCROLL_LIMIT)) return false
        return waitForHeader(STORY_ARCHIVE_LABELS)
    }

    private fun openFacebookComments(): Boolean {
        if (!openFacebookMoreProfileSettings()) {
            return fail("facebook_comments_menu_missing")
        }
        val openedActivityHub =
            clickExactTextWithScroll(FACEBOOK_ACTIVITY_LOG_LABELS, MENU_SCROLL_LIMIT) ||
                clickFacebookLabeledControl(FACEBOOK_ACTIVITY_LOG_LABELS) ||
                clickFacebookLabeledControl(FACEBOOK_ACTIVITY_LOG_LABELS, allowActionClick = false) ||
                clickExactTextWithScroll(FACEBOOK_YOUR_ACTIVITY_LABELS, MENU_SCROLL_LIMIT) ||
                clickFacebookLabeledControl(FACEBOOK_YOUR_ACTIVITY_LABELS) ||
                clickFacebookLabeledControl(FACEBOOK_YOUR_ACTIVITY_LABELS, allowActionClick = false)
        if (!openedActivityHub) {
            return fail("facebook_activity_log_missing")
        }
        waitNavigation()
        SystemClock.sleep(400)
        clickFacebookLabeledControl(FACEBOOK_YOUR_ACTIVITY_LABELS) ||
            clickDescriptionContains(FACEBOOK_YOUR_ACTIVITY_DESC) ||
            clickExactText(FACEBOOK_YOUR_ACTIVITY_LABELS) ||
            clickFacebookLabeledControl(FACEBOOK_YOUR_ACTIVITY_LABELS, allowActionClick = false)
        waitNavigation()
        SystemClock.sleep(350)
        val openedCommentsHub =
            clickFacebookLabeledControl(FACEBOOK_COMMENTS_REACTIONS_LABELS) ||
                clickDescriptionContains(FACEBOOK_COMMENTS_REACTIONS_DESC) ||
                clickExactText(FACEBOOK_COMMENTS_REACTIONS_LABELS) ||
                clickFacebookLabeledControl(
                    FACEBOOK_COMMENTS_REACTIONS_LABELS,
                    allowActionClick = false,
                ) ||
                clickExactTextWithScroll(FACEBOOK_COMMENTS_REACTIONS_LABELS, MENU_SCROLL_LIMIT)
        if (openedCommentsHub) {
            waitNavigation()
            SystemClock.sleep(350)
        }
        if (openFacebookActivitySection(FACEBOOK_COMMENTS_LABELS)) {
            fbActivityPhase = FacebookActivityPhase.COMMENTS
        } else {
            clickFacebookLabeledControl(FACEBOOK_ACTIVITY_ALL_FILTER_LABELS) ||
                clickExactText(FACEBOOK_ACTIVITY_ALL_FILTER_LABELS)
            waitNavigation()
            SystemClock.sleep(250)
            if (!isFacebookCombinedActivitySurface()) {
                return fail("facebook_comments_list_missing")
            }
            fbActivityPhase = FacebookActivityPhase.COMBINED
        }
        fbCommentsBoundaryReached = false
        fbFeedActive = true
        if (!isFacebookCommentsSurface()) {
            fbFeedActive = false
            return fail("facebook_comments_list_missing")
        }
        val visited = fbVisitedViewportSignatures.getOrPut(SocialScope.OWN_COMMENTS) { mutableSetOf() }
        fbViewportSignature(SocialScope.OWN_COMMENTS)?.let { signature ->
            visited.add(signature)
            fbCurrentViewportSignatures[SocialScope.OWN_COMMENTS] = signature
        }
        return true
    }

    private fun openFacebookActivitySection(labels: List<String>): Boolean {
        val opened = clickFacebookLabeledControl(labels) ||
            clickExactDescription(labels) ||
            clickExactText(labels) ||
            clickFacebookLabeledControl(labels, allowActionClick = false) ||
            clickExactTextWithScroll(labels, MENU_SCROLL_LIMIT)
        if (!opened) return false
        waitNavigation()
        SystemClock.sleep(350)
        // The Activity Log opens unfiltered. On current Facebook builds the
        // visible "Semua"/"All" below the category chips is Select All, not a
        // date/category filter. Do not tap it: doing so selects every activity
        // row and can enable destructive controls. Only use an explicit,
        // clickable filter on older variants that have not rendered rows yet.
        if (!hasFacebookActivityRows() && !isFacebookCommentsEmpty()) {
            clickFacebookActivityAllFilter()
            waitNavigation()
            SystemClock.sleep(250)
        }
        return hasExactLabel(labels) || hasFacebookActivityRows() || isFacebookCommentsEmpty()
    }

    private fun openFacebookReactions(): Boolean {
        if (fbActivityPhase == FacebookActivityPhase.COMMENTS) {
            safePressBack()
            waitNavigation()
            SystemClock.sleep(250)
        }
        repeat(MAX_BACK_NAVIGATION) { attempt ->
            if (openFacebookActivitySection(FACEBOOK_LIKES_REACTIONS_LABELS)) {
                fbActivityPhase = FacebookActivityPhase.REACTIONS
                fbFeedActive = true
                Log.i(LOG_TAG, "event=facebook_activity_phase phase=reactions attempt=$attempt")
                return true
            }
            if (!isForeground(FACEBOOK_PACKAGE)) return false
            val reopenedCommentsHub =
                clickFacebookLabeledControl(FACEBOOK_COMMENTS_REACTIONS_LABELS) ||
                    clickDescriptionContains(FACEBOOK_COMMENTS_REACTIONS_DESC) ||
                    clickExactText(FACEBOOK_COMMENTS_REACTIONS_LABELS) ||
                    clickFacebookLabeledControl(
                        FACEBOOK_COMMENTS_REACTIONS_LABELS,
                        allowActionClick = false,
                    )
            if (reopenedCommentsHub) {
                waitNavigation()
                SystemClock.sleep(300)
                return@repeat
            }
            safePressBack()
            waitNavigation()
            SystemClock.sleep(250)
        }
        return false
    }

    private fun openFacebookMoreProfileSettings(): Boolean {
        if (!hasFacebookOwnProfileProof() && !openFacebookOwnProfile()) return false
        advanceFacebookProfilePastOnboarding()
        if (facebookSettingsSheetVisible()) return true
        if (
            clickFacebookLabeledControl(FACEBOOK_MORE_PROFILE_SETTINGS_LABELS) ||
            clickExactDescription(FACEBOOK_MORE_PROFILE_SETTINGS_LABELS) ||
            clickExactText(FACEBOOK_MORE_PROFILE_SETTINGS_LABELS)
        ) {
            waitNavigation()
            SystemClock.sleep(350)
            if (facebookSettingsSheetVisible()) return true
        }
        if (
            clickFacebookLabeledControl(
                FACEBOOK_MORE_PROFILE_SETTINGS_LABELS,
                allowActionClick = false,
            )
        ) {
            waitNavigation()
            SystemClock.sleep(350)
            if (facebookSettingsSheetVisible()) return true
        }
        // Generic More is last: it also matches post overflow on some locales.
        if (clickExactLabel(FACEBOOK_MORE_LABELS) || clickFacebookLabeledControl(FACEBOOK_MORE_LABELS)) {
            waitNavigation()
            SystemClock.sleep(350)
            if (facebookSettingsSheetVisible()) return true
        }
        return facebookSettingsSheetVisible()
    }

    private fun facebookSettingsSheetVisible(): Boolean =
        hasExactLabel(FACEBOOK_ACTIVITY_LOG_LABELS) ||
            hasExactLabel(FACEBOOK_YOUR_ACTIVITY_LABELS) ||
            hasExactLabel(FACEBOOK_MORE_PROFILE_SETTINGS_LABELS) ||
            hasExactLabel(listOf("Settings", "Pengaturan", "Setelan", "Profile settings", "Pengaturan profil"))

    private fun clickDescriptionContains(labels: List<String>): Boolean =
        clickFirst(labels.map { By.descContains(it) })

    private fun captureFacebookTimelineScope(
        scope: SocialScope,
        visibleNodes: List<VisibleNodeRecord>,
    ): ScopeCapture {
        if (scope == SocialScope.OWN_COMMENTS && isFacebookCommentsEmpty()) {
            val finalPhase = fbActivityPhase in setOf(
                FacebookActivityPhase.REACTIONS,
                FacebookActivityPhase.COMBINED,
            )
            if (!finalPhase) fbCommentsBoundaryReached = true
            Log.i(
                LOG_TAG,
                "event=facebook_activity_empty phase=${fbActivityPhase.name.lowercase()}",
            )
            return ScopeCapture(true, null, exhausted = finalPhase)
        }
        val marker = fbOwnAccountMarker ?: run {
            failureReason = "facebook_account_marker_missing"
            return ScopeCapture(false, null)
        }
        var rows = when (scope) {
            SocialScope.OWN_POSTS -> fbPostsRowsFromUiDevice(marker).ifEmpty {
                fbPostsRowsFromShellDump(marker)
            }
            SocialScope.OWN_COMMENTS -> fbCommentsRowsFromUiDevice(marker)
            else -> emptyList()
        }
        repeat(FB_CONTENT_WAIT_ATTEMPTS) {
            if (rows.isNotEmpty()) return@repeat
            if (scope == SocialScope.OWN_COMMENTS && isFacebookCommentsEmpty()) {
                val finalPhase = fbActivityPhase in setOf(
                    FacebookActivityPhase.REACTIONS,
                    FacebookActivityPhase.COMBINED,
                )
                if (!finalPhase) fbCommentsBoundaryReached = true
                Log.i(
                    LOG_TAG,
                    "event=facebook_activity_empty phase=${fbActivityPhase.name.lowercase()}",
                )
                return ScopeCapture(true, null, exhausted = finalPhase)
            }
            if (navigationExpired()) return@repeat
            SystemClock.sleep(FB_SCROLL_IDLE_MS)
            invalidateShellDumpCache()
            rows = when (scope) {
                SocialScope.OWN_POSTS -> fbPostsRowsFromUiDevice(marker).ifEmpty {
                    fbPostsRowsFromShellDump(marker)
                }
                SocialScope.OWN_COMMENTS -> fbCommentsRowsFromUiDevice(marker)
                else -> emptyList()
            }
        }
        if (rows.isEmpty() && scope == SocialScope.OWN_POSTS) {
            // Fallback: treat joined a11y text as one owned viewport if marker present.
            val joined = CommunicationPolicy.joinedText(
                visibleNodes.flatMap { node -> listOf(node.text, node.contentDescription) },
                BuildConfig.MAX_SMS_TEXT_LENGTH,
            )
            if (
                joined != null &&
                joined.contains(marker, ignoreCase = true) &&
                hasMeaningfulFbText(joined, marker)
            ) {
                rows = listOf(
                    FbTimelineRow(
                        nodes = visibleNodes.take(BuildConfig.MAX_UI_NODES),
                        normalizedText = joined,
                        contentHash = CommunicationPolicy.contentHash(
                            FACEBOOK_PACKAGE,
                            scope.wireName,
                            joined,
                        ),
                    ),
                )
            }
        }
        if (rows.isEmpty()) {
            failureReason = "facebook_own_content_not_visible"
            Log.w(
                LOG_TAG,
                "event=facebook_timeline_empty scope=${scope.wireName} marker=$marker",
            )
            return ScopeCapture(false, null)
        }
        val evaluatedRows = rows.map { row ->
            row to temporalDecision(scope, row.nodes, row.normalizedText)
        }
        val eligibleRows = evaluatedRows.filterNot { (_, temporal) -> temporal.outOfScope }
        if (evaluatedRows.isNotEmpty() && eligibleRows.isEmpty()) {
            if (
                scope == SocialScope.OWN_COMMENTS &&
                fbActivityPhase == FacebookActivityPhase.COMMENTS
            ) {
                fbCommentsBoundaryReached = true
            } else {
                temporalBoundaryScopes.add(scope)
            }
            return ScopeCapture(true, null)
        }
        val known = fbStoredItemSignatures.getOrPut(scope) { mutableSetOf() }
        var storedAny = false
        var rejected = false
        val now = System.currentTimeMillis()
        eligibleRows.forEachIndexed { index, (row, temporal) ->
            if (row.contentHash in known) return@forEachIndexed
            val stored = store.recordVisibleSnapshot(
                packageName = FACEBOOK_PACKAGE,
                windowId = -1,
                activityContext = row.nodes.firstNotNullOfOrNull(VisibleNodeRecord::className),
                eventType = AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
                eventTime = temporal.sourceTimeEpochMs ?: now + index,
                nodes = row.nodes.take(BuildConfig.MAX_UI_NODES),
                normalizedText = row.normalizedText,
                contentHash = row.contentHash,
                socialScope = scope.wireName,
                screenshotIds = emptyList(),
                now = now + index,
            )
            if (stored) {
                known.add(row.contentHash)
                storedAny = true
            } else {
                rejected = true
            }
        }
        val accepted = storedAny || eligibleRows.all { (row, _) -> row.contentHash in known }
        if (!accepted) {
            failureReason =
                if (rejected) "snapshot_store_rejected" else "facebook_own_content_not_visible"
        } else {
            Log.i(
                LOG_TAG,
                "event=facebook_timeline_stored scope=${scope.wireName} rows=${rows.size} " +
                    "new=$storedAny mode=text_only",
            )
        }
        val stagnantExhausted = timelineStagnantExhausted(
            fbStagnantCaptures,
            scope,
            storedAny,
        )
        return ScopeCapture(
            accepted,
            null,
            exhausted = accepted &&
                fbActivityPhase != FacebookActivityPhase.COMMENTS &&
                stagnantExhausted,
        )
    }

    private fun advanceFacebookFeed(scope: SocialScope): ScrollResult {
        if (!isFacebookFeedSurface(scope)) {
            return failScroll("facebook_activity_surface_lost")
        }
        if (
            scope == SocialScope.OWN_COMMENTS &&
            fbActivityPhase == FacebookActivityPhase.COMMENTS &&
            fbCommentsBoundaryReached
        ) {
            return transitionFacebookToReactions(scope)
        }
        val before = fbCurrentViewportSignatures[scope] ?: fbViewportSignature(scope)
        val width = device.displayWidth
        val height = device.displayHeight
        if (width <= 0 || height <= 0) return failScroll("display_bounds_invalid")
        fun moved(): Boolean {
            if (!isFacebookFeedSurface(scope)) return false
            val after = fbViewportSignature(scope) ?: return false
            val visited = fbVisitedViewportSignatures.getOrPut(scope) { mutableSetOf() }
            if (after == before || !visited.add(after)) return false
            fbCurrentViewportSignatures[scope] = after
            return true
        }
        invalidateShellDumpCache()
        performAccessibilityScrollForward(packageName = FACEBOOK_PACKAGE)
        SystemClock.sleep(FB_SCROLL_IDLE_MS)
        if (moved()) return ScrollResult.MOVED
        safeSwipe(width / 2, (height * 3) / 4, width / 2, height / 4, SWIPE_STEPS)
        SystemClock.sleep(FB_SCROLL_IDLE_MS)
        if (moved()) return ScrollResult.MOVED
        if (!isFacebookFeedSurface(scope)) {
            return failScroll("facebook_activity_surface_lost")
        }
        if (
            scope == SocialScope.OWN_COMMENTS &&
            fbActivityPhase == FacebookActivityPhase.COMMENTS
        ) {
            return transitionFacebookToReactions(scope)
        }
        if (scope == SocialScope.OWN_COMMENTS && isFacebookCommentsEmpty()) {
            return ScrollResult.EXHAUSTED
        }
        val after = fbViewportSignature(scope)
        if (before != null && after == before) return ScrollResult.EXHAUSTED
        return failScroll("facebook_feed_scroll_unverified")
    }

    private fun transitionFacebookToReactions(scope: SocialScope): ScrollResult {
        if (!openFacebookReactions()) {
            return failScroll("facebook_reactions_list_missing")
        }
        fbCommentsBoundaryReached = false
        temporalBoundaryScopes.remove(scope)
        fbVisitedViewportSignatures[scope] = mutableSetOf()
        fbCurrentViewportSignatures.remove(scope)
        fbStagnantCaptures[scope] = 0
        fbViewportSignature(scope)?.let { signature ->
            fbVisitedViewportSignatures.getValue(scope).add(signature)
            fbCurrentViewportSignatures[scope] = signature
        }
        return ScrollResult.MOVED
    }

    private fun swipeFacebookFeed(): Boolean {
        val width = device.displayWidth
        val height = device.displayHeight
        if (width <= 0 || height <= 0) return false
        invalidateShellDumpCache()
        if (performAccessibilityScrollForward(packageName = FACEBOOK_PACKAGE)) {
            return true
        }
        return safeSwipe(
            width / 2,
            (height * 3) / 4,
            width / 2,
            height / 4,
            SWIPE_STEPS,
        )
    }

    private fun isFacebookFeedSurface(scope: SocialScope): Boolean {
        if (!fbFeedActive || !isForeground(FACEBOOK_PACKAGE)) return false
        if (FACEBOOK_PACKAGE !in verifiedOwnAccountPackages || fbOwnAccountMarker == null) {
            return false
        }
        return when (scope) {
            SocialScope.OWN_POSTS -> hasFacebookOwnProfileProof() || hasFacebookAllFilter()
            SocialScope.OWN_COMMENTS -> isFacebookCommentsSurface()
            else -> false
        }
    }

    private fun isFacebookCommentsSurface(): Boolean = when (fbActivityPhase) {
        FacebookActivityPhase.COMMENTS ->
            hasExactLabel(FACEBOOK_COMMENTS_LABELS) ||
                hasFacebookActivityRows() ||
                isFacebookCommentsEmpty()
        FacebookActivityPhase.REACTIONS ->
            hasExactLabel(FACEBOOK_LIKES_REACTIONS_LABELS) ||
                hasFacebookActivityRows() ||
                isFacebookCommentsEmpty()
        FacebookActivityPhase.COMBINED -> isFacebookCombinedActivitySurface()
        FacebookActivityPhase.NONE -> false
    }

    private fun isFacebookCombinedActivitySurface(): Boolean =
        hasExactLabel(FACEBOOK_COMMENTS_REACTIONS_LABELS) &&
            (hasFacebookActivityRows() || hasFacebookAllFilter() || isFacebookCommentsEmpty())

    private fun isFacebookCommentsEmpty(): Boolean =
        hasExactLabel(FACEBOOK_EMPTY_COMMENTS_LABELS) ||
            hasLabelContaining(listOf("No items", "Tidak ada item"))

    private fun hasFacebookActivityRows(): Boolean = safeUi(false) {
        device.findObjects(By.clazz("android.widget.Button")).any { value ->
            value.resourceName.orEmpty().substringAfterLast('/') ==
                FACEBOOK_ACTIVITY_ITEM_RESOURCE
        }
    }

    private fun clickFacebookActivityAllFilter(): Boolean = safeUi(false) {
        val candidate = FACEBOOK_ACTIVITY_ALL_FILTER_LABELS.asSequence()
            .flatMap { label ->
                sequenceOf(By.text(label), By.desc(label))
                    .flatMap { selector -> device.findObjects(selector).asSequence() }
            }
            // Exact clickable controls only. Never coordinate-tap a plain
            // "Semua" text node because it can label the Select All checkbox.
            .firstOrNull { value -> value.isClickable }
            ?: return@safeUi false
        safeClick(candidate)
    }

    private fun hasFacebookAllFilter(): Boolean =
        hasExactLabel(FACEBOOK_ALL_FILTER_LABELS) ||
            FACEBOOK_ALL_FILTER_DESC.any { label -> device.hasObject(By.descContains(label)) }

    private fun fbViewportSignature(scope: SocialScope): String? {
        val marker = fbOwnAccountMarker ?: return null
        val rows = when (scope) {
            SocialScope.OWN_POSTS -> fbPostsRowsFromUiDevice(marker).ifEmpty {
                fbPostsRowsFromShellDump(marker)
            }
            SocialScope.OWN_COMMENTS -> fbCommentsRowsFromUiDevice(marker)
            else -> emptyList()
        }
        if (rows.isEmpty()) return null
        return CommunicationPolicy.contentHash(
            FACEBOOK_PACKAGE,
            scope.wireName,
            rows.joinToString("\n") { it.contentHash },
        )
    }

    private fun fbPostsRowsFromUiDevice(marker: String): List<FbTimelineRow> = safeUi(emptyList()) {
        val minimumTop = fbFeedTop()
        val authors = device.findObjects(By.text(marker)) +
            device.findObjects(By.desc(marker))
        if (authors.isEmpty()) return@safeUi emptyList()
        val classes = listOf("android.widget.TextView", "android.view.ViewGroup")
        classes.asSequence()
            .flatMap { clazz -> device.findObjects(By.clazz(clazz)).asSequence() }
            .mapNotNull { obj ->
                val text = obj.text?.toString()?.trim().orEmpty()
                if (text.length < 2) return@mapNotNull null
                if (text.equals(marker, ignoreCase = true)) return@mapNotNull null
                if (isFacebookPostNoise(text)) return@mapNotNull null
                if (FB_RELATIVE_TIME_PATTERN.matches(text)) return@mapNotNull null
                val bounds = safeBounds(obj) ?: return@mapNotNull null
                if (bounds.top < minimumTop) return@mapNotNull null
                val authorNearby = authors.any { author ->
                    val ab = safeBounds(author) ?: return@any false
                    ab.top <= bounds.top &&
                        bounds.top - ab.bottom <= device.displayHeight / 3
                }
                if (!authorNearby) return@mapNotNull null
                val labels = buildList {
                    add(marker)
                    add(text)
                    collectUiObjectLabels(obj).take(8).forEach(::add)
                }
                buildFbTimelineRow(labels, marker, SocialScope.OWN_POSTS, obj)
            }
            .distinctBy(FbTimelineRow::contentHash)
            .toList()
    }

    /**
     * Compose post bodies often miss UiDevice TextView hits. The in-process
     * hierarchy dump still sees
     * author + body text ("Azaheuq Jdjsjs" / "Pesta babi") with bounds.
     */
    private fun fbPostsRowsFromShellDump(marker: String): List<FbTimelineRow> {
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return emptyList()
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        data class DumpHit(
            val text: String,
            val top: Int,
            val bottom: Int,
            val clickable: Boolean,
        )
        val hits = ArrayList<DumpHit>(64)
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val text = sequenceOf(shellDumpAttr(tag, "text"), shellDumpAttr(tag, "content-desc"))
                .map { it.trim() }
                .firstOrNull { it.isNotEmpty() }
                ?: continue
            val b = boundsRe.find(tag) ?: continue
            hits += DumpHit(
                text = text,
                top = b.groupValues[2].toInt(),
                bottom = b.groupValues[4].toInt(),
                clickable = shellDumpAttr(tag, "clickable") == "true",
            )
        }
        val authors = hits.filter { it.text.equals(marker, ignoreCase = true) }
        if (authors.isEmpty()) return emptyList()
        val minimumTop = fbFeedTop()
        val maxGap = device.displayHeight / 3
        return hits.asSequence()
            .filter { hit ->
                hit.text.length >= 2 &&
                    !hit.text.equals(marker, ignoreCase = true) &&
                    !isFacebookPostNoise(hit.text) &&
                    hit.top >= minimumTop &&
                    authors.any { author ->
                        author.top <= hit.top && hit.top - author.bottom <= maxGap
                    }
            }
            .mapNotNull { hit ->
                buildFbTimelineRow(
                    listOf(marker, hit.text),
                    marker,
                    SocialScope.OWN_POSTS,
                    null,
                )
            }
            .distinctBy(FbTimelineRow::contentHash)
            .toList()
    }

    private fun fbCommentsRowsFromUiDevice(marker: String): List<FbTimelineRow> = safeUi(emptyList()) {
        if (isFacebookCommentsEmpty()) return@safeUi emptyList()
        val minimumTop = device.displayHeight / 8
        val phaseLabel = when (fbActivityPhase) {
            FacebookActivityPhase.REACTIONS -> "Facebook likes/reactions"
            FacebookActivityPhase.COMMENTS -> "Facebook comments"
            FacebookActivityPhase.COMBINED -> "Facebook comments/reactions"
            FacebookActivityPhase.NONE -> return@safeUi emptyList()
        }
        // Facebook Activity Log exposes one semantic button per logical item.
        // Prefer it so the summary + comment/reaction body stay together and
        // nested TextViews do not become duplicate analysis/gallery records.
        val activityRows = device.findObjects(By.clazz("android.widget.Button"))
            .asSequence()
            .filter { obj ->
                obj.resourceName.orEmpty().substringAfterLast('/') ==
                    FACEBOOK_ACTIVITY_ITEM_RESOURCE
            }
            .mapNotNull { obj ->
                val bounds = safeBounds(obj) ?: return@mapNotNull null
                if (bounds.top < minimumTop) return@mapNotNull null
                val description = cleanFacebookActivityText(
                    obj.contentDescription?.toString().orEmpty(),
                )
                val rowLabels = collectUiObjectLabels(obj)
                    .map(::cleanFacebookActivityText)
                    .filterNot(::isFacebookCommentNoise)
                val body = description.takeIf { value ->
                    value.isNotBlank() && !isFacebookCommentNoise(value)
                } ?: CommunicationPolicy.joinedText(
                    rowLabels,
                    BuildConfig.MAX_SMS_TEXT_LENGTH,
                ).orEmpty()
                if (body.length < 2) return@mapNotNull null
                buildFbTimelineRow(
                    listOf(marker, phaseLabel, body),
                    marker,
                    SocialScope.OWN_COMMENTS,
                    obj,
                )
            }
            .distinctBy(FbTimelineRow::contentHash)
            .toList()
        if (activityRows.isNotEmpty()) return@safeUi activityRows

        // Additive compatibility fallback for Facebook builds that do not
        // expose the stable Activity Log row resource.
        listOf(
            "android.widget.TextView",
            "android.view.ViewGroup",
            "android.widget.Button",
        ).asSequence()
            .flatMap { clazz -> device.findObjects(By.clazz(clazz)).asSequence() }
            .mapNotNull { obj ->
                val text = obj.text?.toString()?.trim().orEmpty()
                val desc = obj.contentDescription?.toString()?.trim().orEmpty()
                val body = text.ifBlank { desc }
                if (body.length < 2) return@mapNotNull null
                if (isFacebookCommentNoise(body)) return@mapNotNull null
                val bounds = safeBounds(obj) ?: return@mapNotNull null
                if (bounds.top < minimumTop) return@mapNotNull null
                val labels = listOf(marker, phaseLabel, body).filter { it.isNotBlank() }
                buildFbTimelineRow(labels, marker, SocialScope.OWN_COMMENTS, obj)
            }
            .distinctBy(FbTimelineRow::contentHash)
            .toList()
    }

    private fun cleanFacebookActivityText(value: String): String =
        value.replace(FB_INVISIBLE_FORMATTING, "").trim()

    private fun buildFbTimelineRow(
        labels: List<String>,
        marker: String,
        scope: SocialScope,
        anchor: UiObject2?,
    ): FbTimelineRow? {
        if (labels.isEmpty()) return null
        val normalizedText = CommunicationPolicy.joinedText(
            labels,
            BuildConfig.MAX_SMS_TEXT_LENGTH,
        )?.takeIf { value -> hasMeaningfulFbText(value, marker) } ?: return null
        val bounds = anchor?.let(::safeBounds) ?: Rect(0, 0, device.displayWidth, device.displayHeight)
        val nodes = labels.take(BuildConfig.MAX_UI_NODES).mapIndexed { index, label ->
            VisibleNodeRecord(
                sequence = index,
                depth = 0,
                text = CommunicationPolicy.boundedText(label, BuildConfig.MAX_UI_TEXT_LENGTH),
                contentDescription = null,
                className = "android.widget.TextView",
                viewId = null,
                left = bounds.left,
                top = bounds.top,
                right = bounds.right,
                bottom = bounds.bottom,
                clickable = false,
                scrollable = false,
            )
        }
        return FbTimelineRow(
            nodes,
            normalizedText,
            CommunicationPolicy.contentHash(FACEBOOK_PACKAGE, scope.wireName, normalizedText),
        )
    }

    private fun hasMeaningfulFbText(value: String, marker: String): Boolean =
        value.lineSequence()
            .map { line -> line.trim() }
            .filter(String::isNotEmpty)
            .any { line ->
                val normalized = line.lowercase(Locale.ROOT)
                normalized != marker.lowercase(Locale.ROOT) &&
                    !isFacebookPostNoise(line) &&
                    !isFacebookCommentNoise(line)
            }

    private fun isFacebookPostNoise(value: String): Boolean {
        val lower = value.lowercase(Locale.ROOT)
        if (lower in FB_POST_NOISE) return true
        if (FB_POST_NOISE_FRAGMENTS.any { fragment -> lower.contains(fragment) }) return true
        if (FB_SHARED_WITH_PATTERN.containsMatchIn(value)) return true
        if (FacebookProfileMetricParser.isMetricLine(value)) return true
        return FACEBOOK_POST_ACTION_DESC.any { lower == it.lowercase(Locale.ROOT) }
    }

    private fun isFacebookCommentNoise(value: String): Boolean {
        val lower = value.lowercase(Locale.ROOT)
        return lower in FB_COMMENT_NOISE || FacebookProfileMetricParser.isMetricLine(value)
    }

    private fun fbFeedTop(): Int {
        val filters = boundedLabelCandidates(
            FACEBOOK_ALL_FILTER_LABELS + listOf("Photos", "Foto", "Reels"),
            0,
            (device.displayHeight * 2) / 3,
        )
        val bottom = filters.mapNotNull(::safeBounds).minOfOrNull { it.bottom }
        return bottom?.coerceAtLeast(device.displayHeight / 10) ?: (device.displayHeight / 5)
    }

    private fun fbAccountMarker(): String? {
        val fromDevice = safeUi(null) {
            val editTop = EDIT_PROFILE_LABELS.asSequence()
                .mapNotNull { label ->
                    device.findObject(By.text(label)) ?: device.findObject(By.desc(label))
                }
                .mapNotNull(::safeBounds)
                .minOfOrNull { it.top }
            val addStoryTop = listOf("Add to story", "Tambahkan ke cerita").asSequence()
                .mapNotNull { label ->
                    device.findObject(By.text(label)) ?: device.findObject(By.desc(label))
                }
                .mapNotNull(::safeBounds)
                .minOfOrNull { it.top }
            val anchorTop = listOfNotNull(editTop, addStoryTop).minOrNull()
            val statusBand = (device.displayHeight * 8) / 100
            val half = device.displayHeight / 2
            // FB Compose puts display names on ViewGroup (not TextView) — live dump proof.
            val candidates = fbLabeledNameCandidates()
                .filter { (_, top, _) -> top >= statusBand && top < half }
                .distinctBy { it.first.lowercase(Locale.ROOT) }
                .toList()
            if (candidates.isEmpty()) return@safeUi null
            // Prefer person names just above Edit profile; never longest chrome phrase.
            fun score(candidate: Triple<String, Int, Int>): Int {
                val tokens = candidate.first.split(Regex("\\s+")).size
                var points = 20
                if (candidate.first.contains(' ')) points += 40
                if (tokens in 2..3) points += 30
                // Closer to Edit profile / Add to story wins over cover-bubble chrome.
                points += candidate.third / 10
                return points
            }
            // Prefer the display name sitting just above Edit profile / Add to story.
            if (anchorTop != null) {
                val band = candidates.filter { (_, top, bottom) ->
                    bottom <= anchorTop &&
                        top >= (anchorTop - device.displayHeight / 3).coerceAtLeast(statusBand)
                }
                band.maxByOrNull(::score)?.first?.let { return@safeUi it }
            }
            candidates.maxByOrNull(::score)?.first
        }
        return fromDevice ?: shellDumpFbAccountMarker()
    }

    /**
     * FB Litho/Compose often exposes names on ViewGroup.text, not TextView.
     */
    private fun fbLabeledNameCandidates(): Sequence<Triple<String, Int, Int>> {
        val classes = listOf(
            "android.widget.TextView",
            "android.view.ViewGroup",
            "android.widget.Button",
        )
        return classes.asSequence()
            .flatMap { clazz -> device.findObjects(By.clazz(clazz)).asSequence() }
            .mapNotNull { value ->
                val bounds = safeBounds(value) ?: return@mapNotNull null
                val text = sequenceOf(value.text, value.contentDescription)
                    .filterNotNull()
                    .map { it.trim() }
                    .mapNotNull(::normalizeFbDisplayName)
                    .firstOrNull()
                    ?: return@mapNotNull null
                Triple(text, bounds.top, bounds.bottom)
            }
    }

    /**
     * When header scrolled away: author of own posts sits above relative times (30m / 1h)
     * under "All posts" / "Manage posts".
     */
    private fun inferFbMarkerFromOwnPostsSurface(): String? = safeUi(null) {
        val feedTop = fbFeedTop()
        val timeHits = device.findObjects(By.clazz("android.view.ViewGroup")).asSequence() +
            device.findObjects(By.clazz("android.widget.TextView")).asSequence()
        val timeBottoms = timeHits.mapNotNull { obj ->
            val label = sequenceOf(obj.text, obj.contentDescription)
                .filterNotNull()
                .map { it.trim() }
                .firstOrNull()
                ?: return@mapNotNull null
            if (!FB_RELATIVE_TIME_PATTERN.matches(label)) return@mapNotNull null
            safeBounds(obj)?.bottom
        }.toList()
        val authors = fbLabeledNameCandidates()
            .filter { (name, top, bottom) ->
                name.contains(' ') &&
                    top >= feedTop &&
                    (
                        timeBottoms.any { timeBottom ->
                            bottom <= timeBottom && timeBottom - bottom <= device.displayHeight / 5
                        } ||
                            top > (device.displayHeight * 45) / 100
                        )
            }
            .distinctBy { it.first.lowercase(Locale.ROOT) }
            .toList()
        authors.maxByOrNull { it.first.length }?.first
            ?: shellDumpInferFbMarkerFromPosts()
    }

    private fun shellDumpInferFbMarkerFromPosts(): String? {
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return null
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        data class Hit(val text: String, val top: Int, val bottom: Int)
        val hits = ArrayList<Hit>(64)
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val raw = sequenceOf(shellDumpAttr(tag, "text"), shellDumpAttr(tag, "content-desc"))
                .map { it.trim() }
                .firstOrNull { it.isNotEmpty() }
                ?: continue
            val b = boundsRe.find(tag) ?: continue
            hits += Hit(raw, b.groupValues[2].toInt(), b.groupValues[4].toInt())
        }
        val timeBottoms = hits.mapNotNull { hit ->
            if (!FB_RELATIVE_TIME_PATTERN.matches(hit.text.trim())) return@mapNotNull null
            hit.bottom
        }
        val feedTop = device.displayHeight / 5
        return hits.asSequence()
            .mapNotNull { hit ->
                val name = normalizeFbDisplayName(hit.text) ?: return@mapNotNull null
                if (!name.contains(' ')) return@mapNotNull null
                if (hit.top < feedTop) return@mapNotNull null
                if (
                    timeBottoms.none { timeBottom ->
                        hit.bottom <= timeBottom && timeBottom - hit.bottom <= device.displayHeight / 5
                    }
                ) {
                    return@mapNotNull null
                }
                name to hit.top
            }
            .distinctBy { it.first.lowercase(Locale.ROOT) }
            .maxByOrNull { it.first.length }
            ?.first
    }

    private fun refreshFacebookAccountMarker(forceShell: Boolean = false) {
        if (!forceShell) {
            fbAccountMarker()?.let { marker ->
                fbOwnAccountMarker = marker
                verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
                return
            }
        }
        invalidateShellDumpCache()
        val marker = if (forceShell) {
            shellDumpFbAccountMarker() ?: fbAccountMarker()
        } else {
            fbAccountMarker()
        } ?: return
        fbOwnAccountMarker = marker
        verifiedOwnAccountPackages.add(FACEBOOK_PACKAGE)
    }

    /**
     * FB profile display name is often Compose-only — UiDevice TextView scan misses it
     * while the in-process hierarchy dump still exposes the display name above Edit profile.
     */
    private fun shellDumpFbAccountMarker(): String? {
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return null
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        var editTop: Int? = null
        var addStoryTop: Int? = null
        data class Hit(val text: String, val top: Int, val bottom: Int)
        val hits = ArrayList<Hit>(32)
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val raw = sequenceOf(shellDumpAttr(tag, "text"), shellDumpAttr(tag, "content-desc"))
                .map { it.trim() }
                .firstOrNull { it.isNotEmpty() }
                ?: continue
            val b = boundsRe.find(tag) ?: continue
            val top = b.groupValues[2].toInt()
            val bottom = b.groupValues[4].toInt()
            if (EDIT_PROFILE_LABELS.any { it.equals(raw, ignoreCase = true) }) {
                editTop = minOf(editTop ?: top, top)
            }
            if (
                raw.equals("Add to story", ignoreCase = true) ||
                raw.equals("Tambahkan ke cerita", ignoreCase = true)
            ) {
                addStoryTop = minOf(addStoryTop ?: top, top)
            }
            val name = normalizeFbDisplayName(raw) ?: continue
            hits += Hit(name, top, bottom)
        }
        if (hits.isEmpty()) return null
        val statusBand = (device.displayHeight * 8) / 100
        val half = device.displayHeight / 2
        val candidates = hits
            .filter { it.top >= statusBand && it.top < half }
            .distinctBy { it.text.lowercase(Locale.ROOT) }
        if (candidates.isEmpty()) return null
        fun score(hit: Hit): Int {
            val tokens = hit.text.split(Regex("\\s+")).size
            var points = 20
            if (hit.text.contains(' ')) points += 40
            if (tokens in 2..3) points += 30
            points += hit.bottom / 10
            return points
        }
        val anchorTop = listOfNotNull(editTop, addStoryTop).minOrNull()
        if (anchorTop != null) {
            val band = candidates.filter { hit ->
                hit.bottom <= anchorTop &&
                    hit.top >= (anchorTop - device.displayHeight / 3).coerceAtLeast(statusBand)
            }
            band.maxByOrNull(::score)?.text?.let { return it }
        }
        return candidates.maxByOrNull(::score)?.text
    }

    private fun normalizeFbDisplayName(value: String): String? {
        val trimmed = value.trim()
        if (trimmed.length !in 2..80) return null
        val lower = trimmed.lowercase(Locale.ROOT)
        if (lower in FB_RESERVED_DISPLAY_NAMES) return null
        // Composer / PYMK chrome (exact reserved miss: "Create note: What's on your mind?").
        if (lower.contains("on your mind") || lower.contains("di pikiranmu")) return null
        if (lower.contains("create note") || lower.contains("buat catatan")) return null
        if (lower.contains("friend suggestion") || lower.contains("saran teman")) return null
        if (lower.contains("remove friend") || lower.contains("hapus saran")) return null
        if (lower.contains("profile picture") || lower.contains("foto profil")) return null
        if (lower == "add" || lower == "remove" || lower == "camera") return null
        if (FacebookProfileMetricParser.isMetricLine(trimmed)) return null
        // Status-bar clock often sits above Edit profile (e.g. "5:33" / "5:33 PM").
        if (FB_CLOCK_PATTERN.containsMatchIn(trimmed)) return null
        if (trimmed.contains("tab", ignoreCase = true)) return null
        if (trimmed.contains("•")) return null
        if (trimmed.contains("Shared with", ignoreCase = true)) return null
        if (trimmed.contains("People you may know", ignoreCase = true)) return null
        if (EDIT_PROFILE_LABELS.any { it.equals(trimmed, ignoreCase = true) }) return null
        if (FACEBOOK_POST_ACTION_DESC.any { it.equals(trimmed, ignoreCase = true) }) return null
        if (FB_CHROME_NOISE.any { noise -> lower == noise || lower.startsWith("$noise,") }) {
            return null
        }
        // Real FB display names are short person names, not composer sentences.
        if (!looksLikeFbPersonName(trimmed)) return null
        return trimmed
    }

    /** "Azaheuq Jdjsjs" yes; "Create note: What's on your mind?" no. */
    private fun looksLikeFbPersonName(value: String): Boolean {
        if (value.any { ch -> ch in ":?!@#" }) return false
        val tokens = value.trim().split(Regex("\\s+")).filter(String::isNotEmpty)
        if (tokens.size !in 1..5) return false
        if (value.length > 48) return false
        return tokens.all { token ->
            token.length in 1..32 &&
                token.first().isLetter() &&
                token.all { ch -> ch.isLetter() || ch == '.' || ch == '\'' || ch == '-' }
        }
    }

    private fun facebookProfileEvidenceFromUiDevice(): List<VisibleNodeRecord> {
        if (fbOwnAccountMarker == null) {
            refreshFacebookAccountMarker(forceShell = true)
        }
        val deviceNodes = safeUi(emptyList<VisibleNodeRecord>()) {
            val out = ArrayList<VisibleNodeRecord>(12)
            var seq = 0
            fun addNode(text: String?, desc: String?, bounds: Rect?, viewId: String) {
                if (text.isNullOrBlank() && desc.isNullOrBlank()) return
                val b = bounds ?: return
                out += VisibleNodeRecord(
                    sequence = seq++,
                    depth = 0,
                    text = text?.takeIf { it.isNotBlank() },
                    contentDescription = desc?.takeIf { it.isNotBlank() },
                    className = "android.widget.TextView",
                    viewId = viewId,
                    left = b.left,
                    top = b.top,
                    right = b.right,
                    bottom = b.bottom,
                    clickable = false,
                    scrollable = false,
                )
            }
            val seenKinds = mutableSetOf<FacebookProfileMetricKind>()
            facebookProfileMetricObjects().forEach { obj ->
                val blob = listOfNotNull(obj.text, obj.contentDescription).joinToString(" ")
                FacebookProfileMetricParser.parse(blob).forEach { metric ->
                    if (seenKinds.add(metric.kind)) {
                        addNode(
                            metric.value,
                            null,
                            safeBounds(obj),
                            facebookProfileMetricViewId(metric.kind),
                        )
                    }
                }
            }
            fbOwnAccountMarker?.let { marker ->
                device.findObjects(By.text(marker)).firstOrNull()?.let { obj ->
                    addNode(
                        marker,
                        obj.contentDescription?.toString(),
                        safeBounds(obj),
                        "$FACEBOOK_PACKAGE:id/profile_display_name",
                    )
                } ?: addNode(
                    marker,
                    null,
                    Rect(0, device.displayHeight / 10, device.displayWidth, device.displayHeight / 5),
                    "$FACEBOOK_PACKAGE:id/profile_display_name",
                )
            }
            out
        }
        val out = ArrayList(deviceNodes)
        var seq = out.size
        fun addSynthetic(text: String, viewId: String, topFrac: Int, bottomFrac: Int) {
            val h = device.displayHeight.coerceAtLeast(1)
            out += VisibleNodeRecord(
                sequence = seq++,
                depth = 0,
                text = text,
                contentDescription = null,
                className = "android.widget.TextView",
                viewId = viewId,
                left = 0,
                top = h / topFrac,
                right = device.displayWidth,
                bottom = h / bottomFrac,
                clickable = false,
                scrollable = false,
            )
        }
        if (out.none { it.viewId?.endsWith("profile_display_name") == true }) {
            refreshFacebookAccountMarker(forceShell = true)
            fbOwnAccountMarker?.let { marker ->
                addSynthetic(marker, "$FACEBOOK_PACKAGE:id/profile_display_name", 10, 5)
            }
        }
        val missingMetricKinds = setOf(
            FacebookProfileMetricKind.FRIENDS,
            FacebookProfileMetricKind.POSTS,
        ).filter { kind ->
            out.none { node -> node.viewId == facebookProfileMetricViewId(kind) }
        }.toSet()
        if (missingMetricKinds.isNotEmpty()) {
            val xml = readShellUiDump()
            val shellMetrics = FB_XML_LABEL_ATTRIBUTE.findAll(xml)
                .flatMap { match ->
                    val value = match.groupValues[1]
                    if (FacebookProfileMetricParser.isMetricLine(value)) {
                        FacebookProfileMetricParser.parse(value).asSequence()
                    } else {
                        emptySequence()
                    }
                }
                .filter { metric -> metric.kind in missingMetricKinds }
                .distinctBy(FacebookProfileMetricToken::kind)
                .toList()
            shellMetrics.forEach { metric ->
                addSynthetic(
                    metric.value,
                    facebookProfileMetricViewId(metric.kind),
                    5,
                    4,
                )
            }
        }
        return out
    }

    private fun facebookProfileMetricObjects(): List<UiObject2> = safeUi(emptyList()) {
        buildList {
            addAll(device.findObjects(By.clazz("android.widget.TextView")))
            addAll(device.findObjects(By.clazz("android.view.ViewGroup")))
            FACEBOOK_METRIC_HINTS.forEach { hint ->
                addAll(device.findObjects(By.textContains(hint)))
                addAll(device.findObjects(By.descContains(hint)))
            }
        }
            .asSequence()
            .filter { obj ->
                (safeBounds(obj)?.top ?: Int.MAX_VALUE) < (device.displayHeight * 2) / 3
            }
            .filter { obj ->
                val blob = listOfNotNull(obj.text, obj.contentDescription).joinToString(" ")
                FacebookProfileMetricParser.isMetricLine(blob)
            }
            .distinctBy { obj ->
                val bounds = safeBounds(obj)
                listOf(
                    obj.text,
                    obj.contentDescription,
                    bounds?.left,
                    bounds?.top,
                    bounds?.right,
                    bounds?.bottom,
                )
            }
            .take(MAX_FACEBOOK_PROFILE_METRIC_OBJECTS)
            .toList()
    }

    private fun facebookProfileMetricViewId(kind: FacebookProfileMetricKind): String = when (kind) {
        FacebookProfileMetricKind.FRIENDS -> "$FACEBOOK_PACKAGE:id/friends_stat"
        FacebookProfileMetricKind.FOLLOWING -> "$FACEBOOK_PACKAGE:id/following_stat"
        FacebookProfileMetricKind.POSTS -> "$FACEBOOK_PACKAGE:id/posts_stat"
    }

    private fun scopeStillVisible(packageName: String, scope: SocialScope): Boolean {
        if (packageName !in verifiedOwnAccountPackages) return false
        return when (packageName) {
            INSTAGRAM_PACKAGE -> when (scope) {
                SocialScope.OWN_PROFILE -> hasInstagramOwnProfileProof()
                SocialScope.OWN_POSTS -> isInstagramOwnPostSurface()
                SocialScope.OWN_STORY_ARCHIVE -> isInstagramArchiveListSurface()
                SocialScope.OWN_COMMENTS -> instagramCommentsListChromeVisible()
                else -> false
            }
            X_PACKAGE -> when (scope) {
                SocialScope.OWN_PROFILE ->
                    hasXOwnProfileProof()
                SocialScope.OWN_TWEETS,
                SocialScope.OWN_REPLIES,
                -> isXTimelineSurface(scope)
                else -> false
            }
            FACEBOOK_PACKAGE -> when (scope) {
                SocialScope.OWN_PROFILE -> hasFacebookOwnProfileProof()
                SocialScope.OWN_POSTS ->
                    hasFacebookOwnProfileProof() || (fbFeedActive && hasFacebookAllFilter())
                SocialScope.OWN_STORY_ARCHIVE -> hasHeader(STORY_ARCHIVE_LABELS)
                SocialScope.OWN_COMMENTS -> isFacebookCommentsSurface()
                else -> false
            }
            else -> false
        }
    }

    private fun advanceXTimeline(scope: SocialScope): ScrollResult {
        if (!isXTimelineSurface(scope)) return failScroll("x_timeline_surface_lost")
        val before = xCurrentViewportSignatures[scope] ?: xViewportSignature(scope)
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) {
            return failScroll("display_bounds_invalid")
        }
        fun moved(): Boolean {
            if (!isXTimelineSurface(scope)) return false
            val after = waitForXViewportSignature(scope) ?: return false
            val visited = xVisitedViewportSignatures.getOrPut(scope) { mutableSetOf() }
            if (after == before || !visited.add(after)) return false
            xCurrentViewportSignatures[scope] = after
            return true
        }
        val x = bounds.centerX()
        val yFrom = bounds.top + (bounds.height() * 76) / 100
        val yTo = bounds.top + (bounds.height() * 32) / 100
        performAccessibilityScrollForward(packageName = X_PACKAGE)
        SystemClock.sleep(X_SCROLL_IDLE_MS)
        if (moved()) return ScrollResult.MOVED
        safeSwipe(x, yFrom, x, yTo, SWIPE_STEPS)
        SystemClock.sleep(X_SCROLL_IDLE_MS)
        if (moved()) return ScrollResult.MOVED
        if (!isXTimelineSurface(scope)) return failScroll("x_timeline_surface_lost")
        if (hasLabelContaining(X_EMPTY_TIMELINE_LABELS)) return ScrollResult.EXHAUSTED
        val after = xViewportSignature(scope)
        if (before != null && after == before) return ScrollResult.EXHAUSTED
        return failScroll("x_timeline_scroll_unverified")
    }

    private fun xOwnedContentCandidates(marker: String): List<UiObject2> = safeUi(emptyList()) {
        val minimumTop = xTimelineTop()
        val selectors = listOf(
            By.text(marker),
            By.text("@$marker"),
            By.textContains("@$marker"),
            By.textContains(marker),
            By.descContains("@$marker"),
            By.descContains(marker),
        )
        selectors.asSequence()
            .flatMap { selector -> device.findObjects(selector).asSequence() }
            .filter { value -> (safeBounds(value)?.top ?: Int.MAX_VALUE) >= minimumTop }
            .mapNotNull(::wideClickableAncestor)
            .distinctBy { value ->
                val bounds = safeBounds(value) ?: return@distinctBy emptyList<Int>()
                listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)
            }
            .filter { value ->
                val bounds = safeBounds(value) ?: return@filter false
                bounds.width() >= device.displayWidth / 2 &&
                    bounds.height() in 1..((activeWindowBounds().height() * 3) / 4)
            }
            .sortedBy { value -> safeBounds(value)?.top ?: Int.MAX_VALUE }
            .toList()
    }

    private fun wideClickableAncestor(value: UiObject2): UiObject2? {
        var current: UiObject2? = value
        var fallback: UiObject2? = null
        repeat(MAX_CONTENT_ANCESTOR_DEPTH + 1) {
            val node = current ?: return@repeat
            val bounds = safeBounds(node)
            if (bounds != null && bounds.width() >= device.displayWidth / 2) {
                fallback = node
            }
            if (node.isClickable) {
                fallback = node
                if ((bounds?.width() ?: 0) >= device.displayWidth / 2) return node
            }
            current = node.parent
        }
        return fallback
    }

    private fun xOwnedTimelineRows(
        nodes: List<VisibleNodeRecord>,
        marker: String,
        scope: SocialScope,
    ): List<XTimelineRow> {
        val candidateRows = xOwnedContentCandidates(marker).mapNotNull { candidate ->
            val bounds = safeBounds(candidate) ?: return@mapNotNull null
            buildXTimelineRow(
                nodes.filter { node ->
                    node.bottom > bounds.top &&
                        node.top < bounds.bottom &&
                        node.left >= bounds.left - X_ROW_BOUNDS_TOLERANCE &&
                        node.right <= bounds.right + X_ROW_BOUNDS_TOLERANCE
                },
                marker,
                scope,
            )
        }
        val rows = candidateRows.ifEmpty {
            xTimelineRowsFromMarkerBands(nodes, marker, scope)
        }
        return rows.distinctBy(XTimelineRow::contentHash)
    }

    /** Text-only tweet/reply extraction from UiDevice — no shell dump (hangs on Infinix). */
    private fun xTimelineRowsFromUiDevice(
        marker: String,
        scope: SocialScope,
    ): List<XTimelineRow> {
        // Proven on Infinix: id/row content-desc embeds "@handle … tweet body …".
        val fromRows = safeUi(emptyList<XTimelineRow>()) {
            device.findObjects(By.res(X_PACKAGE, "row")).asSequence()
                .mapNotNull { obj ->
                    val bounds = safeBounds(obj) ?: return@mapNotNull null
                    if (bounds.top < xTimelineTop() - device.displayHeight / 20) {
                        return@mapNotNull null
                    }
                    val desc = obj.contentDescription?.toString()?.trim().orEmpty()
                    val text = obj.text?.toString()?.trim().orEmpty()
                    val labels = buildList {
                        if (desc.isNotEmpty()) add(desc)
                        if (text.isNotEmpty()) add(text)
                        addAll(collectUiObjectLabels(obj).take(12))
                    }
                    buildXTimelineRowFromLabels(labels, marker, scope, obj)
                }
                .toList()
        }
        if (fromRows.isNotEmpty()) {
            return fromRows.distinctBy(XTimelineRow::contentHash)
        }
        val fromCandidates = xOwnedContentCandidates(marker).mapNotNull { candidate ->
            buildXTimelineRowFromLabels(collectUiObjectLabels(candidate), marker, scope, candidate)
        }
        if (fromCandidates.isNotEmpty()) {
            return fromCandidates.distinctBy(XTimelineRow::contentHash)
        }
        val fromTweetResources = safeUi(emptyList<XTimelineRow>()) {
            X_TIMELINE_RESOURCES.asSequence()
                .flatMap { res ->
                    device.findObjects(By.res(X_PACKAGE, res)).asSequence()
                }
                .mapNotNull { obj ->
                    val bounds = safeBounds(obj) ?: return@mapNotNull null
                    if (bounds.top < xTimelineTop()) return@mapNotNull null
                    buildXTimelineRowFromLabels(collectUiObjectLabels(obj), marker, scope, obj)
                }
                .toList()
        }
        return fromTweetResources.distinctBy(XTimelineRow::contentHash)
    }

    private fun collectUiObjectLabels(root: UiObject2): List<String> {
        val out = ArrayList<String>(32)
        val queue = ArrayDeque<UiObject2>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < 96 && out.size < 48) {
            val node = queue.removeFirst()
            visited += 1
            sequenceOf(node.text, node.contentDescription)
                .filterNotNull()
                .map { value -> value.trim() }
                .filter(String::isNotEmpty)
                .forEach { label ->
                    if (out.none { existing -> existing.equals(label, ignoreCase = true) }) {
                        out += label
                    }
                }
            try {
                node.children.orEmpty().forEach(queue::add)
            } catch (_: Throwable) {
                // UiObject2 can go stale mid-walk on Compose refreshes.
            }
        }
        return out
    }

    private fun buildXTimelineRowFromLabels(
        labels: List<String>,
        marker: String,
        scope: SocialScope,
        anchor: UiObject2?,
    ): XTimelineRow? {
        if (labels.isEmpty()) return null
        val hasMarker = labels.any { label ->
            label.equals(marker, ignoreCase = true) ||
                label.equals("@$marker", ignoreCase = true) ||
                label.contains("@$marker", ignoreCase = true)
        }
        // Candidates are already marker-anchored; shell-dump bands require an explicit marker.
        if (!hasMarker && anchor == null) return null
        val normalizedText = CommunicationPolicy.joinedText(
            labels,
            BuildConfig.MAX_SMS_TEXT_LENGTH,
        )?.takeIf { value -> hasMeaningfulXText(value, marker) } ?: return null
        val bounds = anchor?.let(::safeBounds) ?: Rect(0, 0, device.displayWidth, device.displayHeight)
        val nodes = labels.take(BuildConfig.MAX_UI_NODES).mapIndexed { index, label ->
            VisibleNodeRecord(
                sequence = index,
                depth = 0,
                text = CommunicationPolicy.boundedText(label, BuildConfig.MAX_UI_TEXT_LENGTH),
                contentDescription = null,
                className = "android.widget.TextView",
                viewId = null,
                left = bounds.left,
                top = bounds.top,
                right = bounds.right,
                bottom = bounds.bottom,
                clickable = false,
                scrollable = false,
            )
        }
        return XTimelineRow(
            nodes,
            normalizedText,
            CommunicationPolicy.contentHash(X_PACKAGE, scope.wireName, normalizedText),
        )
    }

    private fun xTimelineRowsFromShellDump(
        marker: String,
        scope: SocialScope,
    ): List<XTimelineRow> {
        invalidateShellDumpCache()
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return emptyList()
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        val minimumTop = xTimelineTop()
        val markerLower = marker.lowercase(Locale.ROOT)
        data class DumpHit(val top: Int, val bottom: Int)
        val hits = mutableListOf<DumpHit>()
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val text = shellDumpAttr(tag, "text").trim()
            val desc = shellDumpAttr(tag, "content-desc").trim()
            val labels = listOf(text, desc).filter { it.isNotEmpty() }
            if (labels.isEmpty()) continue
            val b = boundsRe.find(tag) ?: continue
            val top = b.groupValues[2].toInt()
            val bottom = b.groupValues[4].toInt()
            if (top < minimumTop) continue
            val hasMarker = labels.any { label ->
                val lower = label.lowercase(Locale.ROOT)
                lower == markerLower ||
                    lower == "@$markerLower" ||
                    lower.contains("@$markerLower")
            }
            if (!hasMarker) continue
            hits += DumpHit(top, bottom)
        }
        if (hits.isEmpty()) return emptyList()
        val density = context.resources.displayMetrics.density
        val cluster = (20 * density).toInt().coerceAtLeast(20)
        return hits
            .sortedBy { it.top }
            .groupBy { it.top / cluster }
            .values
            .mapNotNull { group ->
                val top = group.minOf { it.top }
                val bottom = group.maxOf { it.bottom }.coerceAtLeast(top + 1)
                val bandLabels = mutableListOf<String>()
                for (match in nodeRe.findAll(xml)) {
                    val tag = match.value
                    val text = shellDumpAttr(tag, "text").trim()
                    val desc = shellDumpAttr(tag, "content-desc").trim()
                    val labels = listOf(text, desc).filter { it.isNotEmpty() }
                    if (labels.isEmpty()) continue
                    val b = boundsRe.find(tag) ?: continue
                    val nodeTop = b.groupValues[2].toInt()
                    val nodeBottom = b.groupValues[4].toInt()
                    if (nodeBottom <= top - cluster || nodeTop >= bottom + cluster) continue
                    labels.forEach { label ->
                        if (bandLabels.none { it.equals(label, ignoreCase = true) }) {
                            bandLabels += label
                        }
                    }
                }
                buildXTimelineRowFromLabels(bandLabels, marker, scope, null)
            }
            .distinctBy(XTimelineRow::contentHash)
    }

    private fun xTimelineRowsFromMarkerBands(
        nodes: List<VisibleNodeRecord>,
        marker: String,
        scope: SocialScope,
    ): List<XTimelineRow> {
        val minimumTop = xTimelineTop()
        val density = context.resources.displayMetrics.density
        val clusterSize = (16 * density).toInt().coerceAtLeast(16)
        val markerTops = nodes.asSequence()
            .filter { node -> node.top >= minimumTop && nodeContainsAccountMarker(node, marker) }
            .map(VisibleNodeRecord::top)
            .distinctBy { top -> top / clusterSize }
            .sorted()
            .toList()
        return markerTops.mapIndexedNotNull { index, markerTop ->
            val rowTop = (markerTop - clusterSize).coerceAtLeast(minimumTop)
            val rowBottom = markerTops.getOrNull(index + 1)
                ?.minus(1)
                ?: activeWindowBottom()
            buildXTimelineRow(
                nodes.filter { node -> node.bottom > rowTop && node.top < rowBottom },
                marker,
                scope,
            )
        }
    }

    private fun buildXTimelineRow(
        values: List<VisibleNodeRecord>,
        marker: String,
        scope: SocialScope,
    ): XTimelineRow? {
        val nodes = values.asSequence()
            .filter { node ->
                node.text?.isNotBlank() == true || node.contentDescription?.isNotBlank() == true
            }
            .distinctBy { node ->
                listOf(
                    node.text,
                    node.contentDescription,
                    node.viewId,
                    node.left,
                    node.top,
                    node.right,
                    node.bottom,
                )
            }
            .take(BuildConfig.MAX_UI_NODES)
            .toList()
        if (nodes.none { node -> nodeContainsAccountMarker(node, marker) }) return null
        val normalizedText = CommunicationPolicy.joinedText(
            nodes.asSequence()
                .flatMap { node -> sequenceOf(node.text, node.contentDescription) }
                .filterNotNull()
                .map { value -> value.trim() }
                .filter(String::isNotEmpty)
                .distinct()
                .asIterable(),
            BuildConfig.MAX_SMS_TEXT_LENGTH,
        )?.takeIf { value -> hasMeaningfulXText(value, marker) } ?: return null
        return XTimelineRow(
            nodes,
            normalizedText,
            CommunicationPolicy.contentHash(X_PACKAGE, scope.wireName, normalizedText),
        )
    }

    private fun hasMeaningfulXText(value: String, marker: String): Boolean {
        if (X_EMPTY_TIMELINE_LABELS.any { label -> value.contains(label, ignoreCase = true) }) {
            return false
        }
        return value.lineSequence()
            .map { line -> line.trim() }
            .filter(String::isNotEmpty)
            .any { line ->
                val normalized = line.lowercase()
                normalized != marker.lowercase() &&
                    normalized != "@${marker.lowercase()}" &&
                    normalized !in X_TIMELINE_NOISE
            }
    }

    private fun waitForXViewportSignature(scope: SocialScope): String? {
        repeat(X_SIGNATURE_WAIT_ATTEMPTS) {
            xViewportSignature(scope)?.let { return it }
            if (navigationExpired()) return null
            SystemClock.sleep(X_SIGNATURE_IDLE_MS)
        }
        return xViewportSignature(scope)
    }

    private fun xViewportSignature(scope: SocialScope): String? {
        val marker = xOwnAccountMarker ?: return null
        val rows = xTimelineRowsFromUiDevice(marker, scope)
        if (rows.isEmpty()) return null
        return CommunicationPolicy.contentHash(
            X_PACKAGE,
            scope.wireName,
            rows.joinToString("\u001f", transform = XTimelineRow::normalizedText),
        )
    }

    private fun xTimelineTop(): Int {
        val tabs = boundedLabelCandidates(
            X_POSTS_LABELS + X_REPLIES_LABELS + X_SECONDARY_TAB_LABELS,
            0,
            (device.displayHeight * 2) / 3,
        )
        val tabBottom = tabs.mapNotNull(::safeBounds).minOfOrNull { bounds -> bounds.bottom }
        return tabBottom?.coerceAtLeast(device.displayHeight / 10)
            ?: device.displayHeight / 5
    }

    private fun isXTimelineSurface(scope: SocialScope): Boolean {
        if (!xTimelineActive || !isForeground(X_PACKAGE)) return false
        if (X_PACKAGE !in verifiedOwnAccountPackages || xOwnAccountMarker == null) return false
        return when (scope) {
            SocialScope.OWN_REPLIES -> xRepliesSurfaceReady()
            SocialScope.OWN_TWEETS -> xPostsSurfaceReady()
            else -> false
        }
    }

    private fun xRepliesSurfaceReady(): Boolean {
        if (!xProfileTabsVisible() && !hasExactLabel(X_REPLIES_LABELS)) return false
        if (xTabLooksSelected(X_POSTS_LABELS) && !xTabLooksSelected(X_REPLIES_LABELS)) {
            return false
        }
        if (xTabLooksSelected(X_REPLIES_LABELS)) return true
        if (xTabLooksSelected(X_POSTS_LABELS)) return false
        return hasExactLabel(X_REPLIES_LABELS) ||
            hasLabelContaining(X_EMPTY_TIMELINE_LABELS) ||
            hasAnyResource(X_PACKAGE, X_TIMELINE_RESOURCES) ||
            safeUi(false) { device.hasObject(By.scrollable(true)) }
    }

    private fun xPostsSurfaceReady(): Boolean {
        if (xTabLooksSelected(X_REPLIES_LABELS) && !xTabLooksSelected(X_POSTS_LABELS)) {
            return false
        }
        return hasExactLabel(X_POSTS_LABELS) ||
            xTabLooksSelected(X_POSTS_LABELS) ||
            hasAnyResource(X_PACKAGE, X_TIMELINE_RESOURCES) ||
            safeUi(false) { device.hasObject(By.scrollable(true)) }
    }

    private fun scopedNodes(
        packageName: String,
        scope: SocialScope,
        nodes: List<VisibleNodeRecord>,
    ): List<VisibleNodeRecord> {
        val marker = when {
            packageName == X_PACKAGE && scope in setOf(
                SocialScope.OWN_TWEETS,
                SocialScope.OWN_REPLIES,
            ) -> xOwnAccountMarker
            else -> null
        }
        if (marker == null) {
            return when {
                packageName == INSTAGRAM_PACKAGE && scope == SocialScope.OWN_PROFILE ->
                    instagramProfileBounds(nodes)
                packageName == X_PACKAGE && scope == SocialScope.OWN_PROFILE ->
                    xProfileBounds(nodes)
                packageName == FACEBOOK_PACKAGE && scope == SocialScope.OWN_PROFILE ->
                    facebookProfileBounds(nodes)
                packageName == INSTAGRAM_PACKAGE &&
                    scope == SocialScope.OWN_POSTS &&
                    packageName in verifiedOwnAccountPackages ->
                    instagramGridBounds(nodes)
                else -> nodes
            }
        }
        val markerNode = nodes.asSequence()
            .filter { node -> nodeContainsAccountMarker(node, marker) }
            .minByOrNull(VisibleNodeRecord::top)
            ?: return emptyList()
        val minimumTop = (markerNode.top - OWNED_CONTENT_TOP_PADDING).coerceAtLeast(0)
        val maximumBottom = (
            markerNode.top + (device.displayHeight * 11) / 20
            ).coerceAtMost(device.displayHeight)
        return nodes.filter { node ->
            node.bottom >= minimumTop && node.top <= maximumBottom
        }
    }

    private fun instagramProfileBounds(nodes: List<VisibleNodeRecord>): List<VisibleNodeRecord> {
        val navTop = (device.displayHeight * 3) / 5
        val filtered = nodes.filter { node ->
            node.top < navTop && !instagramNavigationNoise(node)
        }
        return filtered.ifEmpty {
            nodes.filterNot(::instagramNavigationNoise)
        }
    }

    private fun instagramGridBounds(nodes: List<VisibleNodeRecord>): List<VisibleNodeRecord> {
        val gridTop = (device.displayHeight * 2) / 5
        val navTop = (device.displayHeight * 4) / 5
        val filtered = nodes.filter { node ->
            node.bottom > gridTop &&
                node.top < navTop &&
                !instagramNavigationNoise(node)
        }
        return filtered.ifEmpty {
            nodes.filterNot(::instagramNavigationNoise)
        }
    }

    private fun xProfileBounds(nodes: List<VisibleNodeRecord>): List<VisibleNodeRecord> {
        val tabTop = nodes.asSequence()
            .filter { node ->
                sequenceOf(node.text, node.contentDescription)
                    .filterNotNull()
                    .any { value ->
                        value.trim() in X_POSTS_LABELS + X_REPLIES_LABELS + X_SECONDARY_TAB_LABELS
                    }
            }
            .map(VisibleNodeRecord::top)
            .filter { top -> top > 0 }
            .minOrNull()
        val cutoff = tabTop?.plus(device.displayHeight / 12)
            ?: (device.displayHeight * 2) / 3
        return nodes.filter { node ->
            node.top < cutoff && !xProfileChromeNoise(node)
        }
    }

    private fun xProfileChromeNoise(node: VisibleNodeRecord): Boolean {
        val label = sequenceOf(node.text, node.contentDescription)
            .filterNotNull()
            .joinToString(" ")
            .trim()
            .lowercase(Locale.ROOT)
        if (label.isEmpty()) return false
        return label in X_PROFILE_CHROME_NOISE ||
            X_PROFILE_CHROME_NOISE.any { noise -> label == noise || label.startsWith("$noise ") }
    }

    private fun instagramNavigationNoise(node: VisibleNodeRecord): Boolean {
        val label = sequenceOf(node.text, node.contentDescription)
            .filterNotNull()
            .joinToString(" ")
            .trim()
        if (node.viewId?.contains("notification", ignoreCase = true) == true) return true
        return instagramLabelNoise(label)
    }

    private fun nodeContainsAccountMarker(node: VisibleNodeRecord, marker: String): Boolean =
        sequenceOf(node.text, node.contentDescription)
            .filterNotNull()
            .any { value ->
                value.equals(marker, ignoreCase = true) ||
                    value.equals("@$marker", ignoreCase = true) ||
                    value.contains("@$marker", ignoreCase = true)
            }

    private fun fail(reason: String): Boolean {
        failureReason = reason
        return false
    }

    private fun failScroll(reason: String): ScrollResult {
        failureReason = reason
        return ScrollResult.FAILED
    }

    private fun foregroundPackageName(): String? {
        val rootPackage = try {
            uiAutomation.rootInActiveWindow?.packageName?.toString()
        } catch (_: Exception) {
            null
        }
        return rootPackage ?: safeUi<String?>(null) { device.currentPackageName }
    }

    private fun resetInstagramCaptureProgress() {
        instagramPostCountKnown = false
        instagramResolvedPostCount = null
        instagramPostEndReached = false
        instagramArchiveEndReached = false
        instagramLastPostCaptureSignature = null
        instagramLastArchiveCaptureSignature = null
        instagramCommentsViewportIndex = 0
        instagramCommentsContentSignature = null
        instagramCommentsStagnantScrolls = 0
        instagramArchiveScrollsCompleted = 0
    }

    private fun updateInstagramCaptureProgress(scope: SocialScope, signature: String) {
        when (scope) {
            SocialScope.OWN_POSTS -> {
                val previous = instagramLastPostCaptureSignature
                if (previous != null && previous == signature) instagramPostEndReached = true
                instagramLastPostCaptureSignature = signature
                Log.i(
                    LOG_TAG,
                    "event=instagram_posts_capture duplicate=${previous == signature} " +
                        "count_known=$instagramPostCountKnown",
                )
            }
            SocialScope.OWN_STORY_ARCHIVE -> {
                val previous = instagramLastArchiveCaptureSignature
                if (
                    previous != null &&
                    previous == signature &&
                    instagramArchiveScrollsCompleted >= INSTAGRAM_ARCHIVE_SCROLL_LIMIT
                ) {
                    instagramArchiveEndReached = true
                }
                instagramLastArchiveCaptureSignature = signature
                Log.i(
                    LOG_TAG,
                    "event=instagram_archive_capture duplicate=${previous == signature}",
                )
            }
            else -> Unit
        }
    }

    private fun fileSha256(value: File): String? = try {
        val digest = MessageDigest.getInstance("SHA-256")
        value.inputStream().buffered().use { input ->
            val buffer = ByteArray(FILE_HASH_BUFFER_BYTES)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                if (read > 0) digest.update(buffer, 0, read)
            }
        }
        digest.digest().joinToString("") { byte ->
            "%02x".format(byte.toInt() and 0xff)
        }
    } catch (_: java.io.IOException) {
        null
    } catch (_: SecurityException) {
        null
    }

    private fun <T> safeUi(default: T, block: () -> T): T =
        try {
            block()
        } catch (_: RuntimeException) {
            default
        }

    private fun <T> uiQueryBounded(fallback: T, block: () -> T): T {
        val holder = arrayOfNulls<Any>(1)
        val worker = Thread(
            {
                try {
                    holder[0] = block()
                } catch (_: Throwable) {
                    holder[0] = fallback
                }
            },
            "siksik-ui-bound",
        )
        worker.start()
        try {
            worker.join(UI_DEVICE_BOUND_MS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        if (worker.isAlive) {
            Log.w(LOG_TAG, "event=uidevice_bounded_timeout ms=$UI_DEVICE_BOUND_MS")
            return fallback
        }
        @Suppress("UNCHECKED_CAST")
        return (holder[0] as T?) ?: fallback
    }

    private fun shellTap(x: Int, y: Int): Boolean = try {
        val output = device.executeShellCommand("input tap $x $y")
        val accepted = shellInputAccepted(output)
        if (!accepted) {
            Log.w(LOG_TAG, "event=shell_tap_rejected x=$x y=$y")
        }
        accepted
    } catch (error: Throwable) {
        Log.w(LOG_TAG, "event=shell_tap_failed type=${error.javaClass.simpleName}")
        false
    }

    private fun shellInputAccepted(output: String): Boolean {
        val normalized = output.lowercase(Locale.ROOT)
        return SHELL_INPUT_FAILURE_MARKERS.none(normalized::contains)
    }

    private fun accessibilityGestureAccepted(output: String): Boolean =
        output.contains(
            "${CommunicationPolicy.A11Y_GESTURE_RESULT_PREFIX};status=accepted",
        )

    private fun a11yServiceTap(x: Int, y: Int): Boolean {
        return try {
            val output = device.executeShellCommand(
                "am broadcast --include-stopped-packages " +
                    "-a ${CommunicationPolicy.A11Y_TAP_ACTION} " +
                    "--ei ${CommunicationPolicy.A11Y_TAP_X_EXTRA} $x " +
                    "--ei ${CommunicationPolicy.A11Y_TAP_Y_EXTRA} $y " +
                    "-n $AGENT_ACCESSIBILITY_RECEIVER",
            )
            SystemClock.sleep(320)
            accessibilityGestureAccepted(output)
        } catch (error: RuntimeException) {
            Log.w(LOG_TAG, "event=accessibility_service_tap_failed type=${error.javaClass.simpleName}")
            false
        }
    }

    private fun a11yServiceSwipe(
        xFrom: Int,
        yFrom: Int,
        xTo: Int,
        yTo: Int,
        durationMs: Long,
    ): Boolean {
        return try {
            val output = device.executeShellCommand(
                "am broadcast --include-stopped-packages " +
                    "-a ${CommunicationPolicy.A11Y_SWIPE_ACTION} " +
                    "--ei ${CommunicationPolicy.A11Y_SWIPE_X_FROM_EXTRA} $xFrom " +
                    "--ei ${CommunicationPolicy.A11Y_SWIPE_Y_FROM_EXTRA} $yFrom " +
                    "--ei ${CommunicationPolicy.A11Y_SWIPE_X_TO_EXTRA} $xTo " +
                    "--ei ${CommunicationPolicy.A11Y_SWIPE_Y_TO_EXTRA} $yTo " +
                    "--el ${CommunicationPolicy.A11Y_SWIPE_DURATION_EXTRA} $durationMs " +
                    "-n $AGENT_ACCESSIBILITY_RECEIVER",
            )
            SystemClock.sleep(A11Y_GESTURE_DISPATCH_WAIT_MS)
            val accepted = accessibilityGestureAccepted(output)
            Log.i(
                LOG_TAG,
                "event=accessibility_service_swipe package=${foregroundPackageName()} " +
                    "accepted=$accepted",
            )
            accepted
        } catch (error: RuntimeException) {
            Log.w(
                LOG_TAG,
                "event=accessibility_service_swipe_failed type=${error.javaClass.simpleName}",
            )
            false
        }
    }

    private fun safePressBack(): Boolean {
        if (safeUi(false) { device.pressBack() }) return true
        val packageName = foregroundPackageName().orEmpty()
        if (packageName !in CommunicationPolicy.supportedSocialTargets) return false
        return try {
            val output = device.executeShellCommand(
                "am broadcast --include-stopped-packages " +
                    "-a ${CommunicationPolicy.A11Y_BACK_ACTION} " +
                    "-n $AGENT_ACCESSIBILITY_RECEIVER",
            )
            SystemClock.sleep(A11Y_GESTURE_DISPATCH_WAIT_MS)
            val accepted = accessibilityGestureAccepted(output)
            Log.i(LOG_TAG, "event=accessibility_service_back package=$packageName accepted=$accepted")
            accepted
        } catch (error: RuntimeException) {
            Log.w(
                LOG_TAG,
                "event=accessibility_service_back_failed type=${error.javaClass.simpleName}",
            )
            false
        }
    }

    private fun accessibilityLabels(node: AccessibilityNodeInfo): Set<String> =
        sequenceOf(node.text, node.contentDescription)
            .filterNotNull()
            .map { value -> value.toString().trim().lowercase(Locale.ROOT) }
            .filter(String::isNotEmpty)
            .toSet()

    private fun accessibilityHasAnyLabel(labels: List<String>): Boolean {
        val expected = labels.map { value -> value.trim().lowercase(Locale.ROOT) }.toSet()
        return safeUi(false) {
            val root = uiAutomation.rootInActiveWindow ?: return@safeUi false
            if (root.packageName?.toString() != INSTAGRAM_PACKAGE) return@safeUi false
            val queue = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
            queue.addLast(root to 0)
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_INSTAGRAM_PROBE_NODES) {
                val (node, depth) = queue.removeFirst()
                visited += 1
                if (node.isVisibleToUser && accessibilityLabels(node).any(expected::contains)) {
                    return@safeUi true
                }
                if (depth >= BuildConfig.MAX_UI_DEPTH) continue
                for (index in 0 until node.childCount) {
                    node.getChild(index)?.let { child -> queue.addLast(child to depth + 1) }
                }
            }
            false
        }
    }

    private fun accessibilityRootsForPackage(packageName: String): List<AccessibilityNodeInfo> {
        val roots = ArrayList<AccessibilityNodeInfo>(4)
        fun consider(root: AccessibilityNodeInfo?) {
            if (root != null && root.packageName?.toString() == packageName) {
                roots.add(root)
            }
        }
        consider(uiAutomation.rootInActiveWindow)
        for (window in uiAutomation.windows) {
            consider(window.root)
        }
        return roots.distinctBy { System.identityHashCode(it) }
    }

    private fun performAccessibilityClick(
        packageName: String = INSTAGRAM_PACKAGE,
        allowActionClick: Boolean = true,
        predicate: (AccessibilityNodeInfo, Rect) -> Boolean,
    ): Boolean = safeUi(false) {
        val roots = accessibilityRootsForPackage(packageName)
        if (roots.isEmpty()) {
            Log.i(
                LOG_TAG,
                "event=accessibility_click success=false package=$packageName reason=no_root",
            )
            return@safeUi false
        }
        var visited = 0
        for (root in roots) {
            val queue = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
            queue.addLast(root to 0)
            while (queue.isNotEmpty() && visited < MAX_INSTAGRAM_PROBE_NODES) {
                val (node, depth) = queue.removeFirst()
                visited += 1
                val bounds = Rect()
                node.getBoundsInScreen(bounds)
                if (!bounds.isEmpty && predicate(node, bounds)) {
                    var candidate: AccessibilityNodeInfo? = node
                    var ancestorDepth = 0
                    while (
                        allowActionClick &&
                        candidate != null &&
                        ancestorDepth <= MAX_CLICKABLE_ANCESTOR_DEPTH
                    ) {
                        if (candidate.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                            Log.i(
                                LOG_TAG,
                                "event=accessibility_click success=true package=$packageName " +
                                    "ancestor_depth=$ancestorDepth",
                            )
                            return@safeUi true
                        }
                        candidate = candidate.parent
                        ancestorDepth += 1
                    }
                    if (a11yServiceTap(bounds.centerX(), bounds.centerY())) {
                        Log.i(
                            LOG_TAG,
                            "event=accessibility_click success=true package=$packageName via=service_tap",
                        )
                        return@safeUi true
                    }
                }
                if (depth >= BuildConfig.MAX_UI_DEPTH) continue
                for (index in 0 until node.childCount) {
                    node.getChild(index)?.let { child -> queue.addLast(child to depth + 1) }
                }
            }
        }
        Log.i(
            LOG_TAG,
            "event=accessibility_click success=false package=$packageName visited=$visited",
        )
        false
    }

    private fun performAccessibilityScrollForward(
        allowViewPager: Boolean = false,
        packageName: String = INSTAGRAM_PACKAGE,
    ): Boolean = safeUi(false) {
        val root = uiAutomation.rootInActiveWindow ?: return@safeUi false
        if (root.packageName?.toString() != packageName) return@safeUi false
        val queue = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
        val candidates = mutableListOf<Pair<AccessibilityNodeInfo, Rect>>()
        queue.addLast(root to 0)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_INSTAGRAM_PROBE_NODES) {
            val (node, depth) = queue.removeFirst()
            visited += 1
            val bounds = Rect()
            node.getBoundsInScreen(bounds)
            val className = node.className?.toString().orEmpty()
            val isPager = className.contains("ViewPager", ignoreCase = true)
            if (node.isVisibleToUser &&
                node.isScrollable &&
                !bounds.isEmpty &&
                (allowViewPager || !isPager)
            ) {
                candidates.add(node to bounds)
            }
            if (depth >= BuildConfig.MAX_UI_DEPTH) continue
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { child -> queue.addLast(child to depth + 1) }
            }
        }
        val ordered = candidates.sortedWith(
            compareByDescending<Pair<AccessibilityNodeInfo, Rect>> { (node, _) ->
                val cls = node.className?.toString().orEmpty()
                val id = node.viewIdResourceName.orEmpty()
                when {
                    id.endsWith(":id/list") || cls.contains("ListView", true) -> 3
                    cls.contains("RecyclerView", true) -> 2
                    else -> 1
                }
            }.thenByDescending { (_, bounds) ->
                bounds.width().toLong() * bounds.height().toLong()
            },
        )
        for ((node, _) in ordered) {
            if (node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)) {
                Log.i(
                    LOG_TAG,
                    "event=accessibility_scroll success=true package=$packageName " +
                        "candidates=${ordered.size}",
                )
                return@safeUi true
            }
        }
        Log.i(
            LOG_TAG,
            "event=accessibility_scroll success=false package=$packageName " +
                "candidates=${ordered.size}",
        )
        false
    }

    private fun safeBounds(value: UiObject2): android.graphics.Rect? =
        try {
            value.visibleBounds
        } catch (_: RuntimeException) {
            null
        }

    private fun safeClick(value: UiObject2): Boolean =
        safeUi(false) {
            value.click()
            true
        }

    private fun safeClickPoint(x: Int, y: Int): Boolean =
        safeUi(false) { device.click(x, y) }

    /**
     * Input injection is denied on a number of OEM builds (notably this MIUI
     * device) even though UiAutomation and the bound accessibility service are
     * healthy. Never let that SecurityException abort a social scope. The
     * accessibility overlay is not touchable, so a service gesture reaches the
     * covered X/Facebook surface without exposing it.
     */
    private fun safeSwipe(
        xFrom: Int,
        yFrom: Int,
        xTo: Int,
        yTo: Int,
        steps: Int,
    ): Boolean {
        val injected = try {
            device.swipe(xFrom, yFrom, xTo, yTo, steps)
        } catch (error: RuntimeException) {
            Log.w(
                LOG_TAG,
                "event=device_swipe_failed type=${error.javaClass.simpleName}",
            )
            false
        }
        if (injected) return true
        val packageName = foregroundPackageName().orEmpty()
        if (packageName !in CommunicationPolicy.supportedSocialTargets) return false
        return a11yServiceSwipe(
            xFrom,
            yFrom,
            xTo,
            yTo,
            (steps.coerceAtLeast(1) * A11Y_SWIPE_STEP_MS).coerceAtLeast(
                A11Y_SWIPE_MIN_DURATION_MS,
            ),
        )
    }

    private fun takeScopedScreenshot(
        target: File,
        packageName: String,
        scope: SocialScope,
        nodes: List<VisibleNodeRecord>,
    ): Boolean {
        if (packageName == X_PACKAGE) return false
        val profileCrop = scope == SocialScope.OWN_PROFILE &&
            packageName == INSTAGRAM_PACKAGE
        val gridCrop = scope == SocialScope.OWN_POSTS &&
            packageName == INSTAGRAM_PACKAGE
        val cropRequired = profileCrop || gridCrop
        if (!cropRequired) return device.takeScreenshot(target)
        val full = target.resolveSibling(".${target.name}.full")
        return try {
            if (!device.takeScreenshot(full)) return false
            val bitmap = BitmapFactory.decodeFile(full.absolutePath) ?: return false
            try {
                val top = when {
                    profileCrop -> (bitmap.height / 24).coerceIn(0, bitmap.height - 1)
                    gridCrop -> ((bitmap.height * 2) / 5).coerceIn(0, bitmap.height - 1)
                    else -> nodes.minOf(VisibleNodeRecord::top).coerceIn(0, bitmap.height - 1)
                }
                val bottom = when {
                    profileCrop -> ((bitmap.height * 9) / 20).coerceIn(top + 1, bitmap.height)
                    gridCrop -> ((bitmap.height * 4) / 5).coerceIn(top + 1, bitmap.height)
                    else -> nodes.maxOf(VisibleNodeRecord::bottom).coerceIn(top + 1, bitmap.height)
                }
                val cropped = Bitmap.createBitmap(bitmap, 0, top, bitmap.width, bottom - top)
                try {
                    FileOutputStream(target).use { output ->
                        cropped.compress(Bitmap.CompressFormat.PNG, 100, output)
                    }
                } finally {
                    cropped.recycle()
                }
            } finally {
                bitmap.recycle()
            }
            target.isFile && target.length() > 0
        } catch (_: IllegalArgumentException) {
            target.delete()
            false
        } catch (_: java.io.IOException) {
            target.delete()
            false
        } catch (_: SecurityException) {
            target.delete()
            false
        } finally {
            full.delete()
        }
    }

    private fun clickResource(packageName: String, resourceName: String): Boolean =
        clickSelector(By.res(packageName, resourceName))

    private fun swipeForward(): Boolean {
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        val packageName = foregroundPackageName()
        if (
            packageName != null &&
            CommunicationPolicy.usesTextOnlyCrawlCover(packageName) &&
            performAccessibilityScrollForward(packageName = packageName)
        ) {
            return true
        }
        return safeSwipe(
            bounds.centerX(),
            bounds.top + (bounds.height() * 3) / 4,
            bounds.centerX(),
            bounds.top + bounds.height() / 4,
            SWIPE_STEPS,
        )
    }

    private fun swipeInstagramGrid(): Boolean {
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        // Prefer vertical list/recycler scroll; never page a ViewPager.
        if (performAccessibilityScrollForward(allowViewPager = false)) return true
        val list = safeUi(null) {
            device.findObject(By.res("android", "list"))
                ?: device.findObject(By.clazz("androidx.recyclerview.widget.RecyclerView"))
        }
        if (list != null) {
            val swiped = safeUi(false) {
                try {
                    list.setGestureMarginPercentage(0.18f)
                    list.swipe(Direction.UP, 0.80f, 8_000)
                    true
                } catch (_: Throwable) {
                    false
                }
            }
            if (swiped) return true
        }
        // One grid page ≈ 3 rows (percent of content height).
        return safeSwipe(
            bounds.centerX(),
            bounds.top + (bounds.height() * 78) / 100,
            bounds.centerX(),
            bounds.top + (bounds.height() * 28) / 100,
            SWIPE_STEPS,
        )
    }

    /**
     * Stories archive: one page ≈ 9 cells (3×3). Finger DOWN reveals the previous
     * page when the privacy footer is visible. Never ACTION_SCROLL on view_pager.
     */
    private fun swipeInstagramArchivePage(): Boolean {
        val list = safeUi(null) {
            device.findObject(By.res("android", "list"))
        }
        if (list != null) {
            val swiped = safeUi(false) {
                try {
                    list.setGestureMarginPercentage(0.20f)
                    list.swipe(Direction.DOWN, ARCHIVE_PAGE_SWIPE_PERCENT, ARCHIVE_PAGE_SWIPE_SPEED)
                    true
                } catch (_: Throwable) {
                    false
                }
            }
            if (swiped) {
                Log.i(LOG_TAG, "event=instagram_archive_page_scroll via=list.swipe")
                return true
            }
        }
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        // Grid band only: ~3 rows, finger down (previous page above).
        val x = bounds.centerX()
        val yFrom = bounds.top + (bounds.height() * 40) / 100
        val yTo = bounds.top + (bounds.height() * 72) / 100
        val ok = safeSwipe(x, yFrom, x, yTo, ARCHIVE_PAGE_SWIPE_STEPS)
        Log.i(LOG_TAG, "event=instagram_archive_page_scroll via=percent ok=$ok")
        return ok
    }

    /** Keep chronological Stories list tab (not Highlights/Calendar/Map). */
    private fun ensureInstagramStoriesArchiveTabSelected() {
        if (!instagramArchiveListActive) return
        if (device.hasObject(By.res(INSTAGRAM_PACKAGE, "day_text"))) return
        if (device.hasObject(By.text("Memories"))) return
        if (!device.hasObject(By.textContains("No archived highlights")) &&
            !device.hasObject(By.textContains("they'll appear on this Map"))
        ) {
            return
        }
        val bounds = activeWindowBounds()
        // First of four archive mode tabs under the header.
        val x = bounds.left + bounds.width() / 8
        val y = bounds.top + (bounds.height() * 8) / 100
        safeClickPoint(x, y)
        SystemClock.sleep(INSTAGRAM_ACTION_SETTLE_MS)
    }

    private fun readShellUiDump(): String {
        val now = SystemClock.elapsedRealtime()
        if (cachedShellDump != null &&
            now - cachedShellDumpAtMs < SHELL_DUMP_CACHE_MS
        ) {
            return cachedShellDump.orEmpty()
        }
        if (now < shellDumpDisabledUntilMs) {
            Log.i(LOG_TAG, "event=shell_dump_skipped reason=cooldown")
            return ""
        }
        val holder = arrayOfNulls<String>(1)
        val worker = Thread(
            {
                try {
                    // Reuse the instrumentation-owned UiAutomation connection.
                    // Starting the shell `uiautomator dump` binary here creates a
                    // second UiAutomationService and crashes on several OEMs.
                    ByteArrayOutputStream().use { output ->
                        device.dumpWindowHierarchy(output)
                        holder[0] = output.toString(Charsets.UTF_8.name())
                    }
                } catch (_: Throwable) {
                    holder[0] = buildAccessibilityHierarchyDump()
                }
            },
            "siksik-hierarchy-dump",
        )
        worker.isDaemon = true
        worker.start()
        try {
            worker.join(SHELL_DUMP_JOIN_MS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        if (worker.isAlive) {
            Log.w(LOG_TAG, "event=shell_dump_timeout join_ms=$SHELL_DUMP_JOIN_MS")
            shellDumpDisabledUntilMs = now + SHELL_DUMP_COOLDOWN_MS
            cachedShellDump = ""
            cachedShellDumpAtMs = now
            return ""
        }
        val xml = holder[0].orEmpty().takeIf { it.contains("<node") }
            ?: buildAccessibilityHierarchyDump()
        if (!xml.contains("<node")) {
            Log.i(LOG_TAG, "event=shell_dump_empty")
        }
        cachedShellDump = xml
        cachedShellDumpAtMs = SystemClock.elapsedRealtime()
        return xml
    }

    private fun buildAccessibilityHierarchyDump(): String {
        val root = try {
            uiAutomation.rootInActiveWindow
        } catch (_: RuntimeException) {
            null
        } ?: return ""
        val nodes = snapshotVisibleNodes(root)
        if (nodes.isEmpty()) return ""
        return buildString {
            append("<hierarchy>")
            nodes.forEach { node ->
                append("<node")
                appendHierarchyAttribute("text", node.text)
                appendHierarchyAttribute("content-desc", node.contentDescription)
                appendHierarchyAttribute("resource-id", node.viewId)
                appendHierarchyAttribute("class", node.className)
                append(" clickable=\"").append(node.clickable).append('"')
                append(" scrollable=\"").append(node.scrollable).append('"')
                append(" bounds=\"[").append(node.left).append(',').append(node.top)
                    .append("][").append(node.right).append(',').append(node.bottom)
                    .append("]\"/>")
            }
            append("</hierarchy>")
        }
    }

    private fun StringBuilder.appendHierarchyAttribute(name: String, value: String?) {
        append(' ').append(name).append("=\"")
        value.orEmpty().forEach { character ->
            append(
                when (character) {
                    '&' -> "&amp;"
                    '<' -> "&lt;"
                    '>' -> "&gt;"
                    '"' -> "&quot;"
                    '\'' -> "&apos;"
                    else -> character
                },
            )
        }
        append('"')
    }

    private fun invalidateShellDumpCache() {
        cachedShellDump = null
        cachedShellDumpAtMs = 0L
    }

    private fun shellDumpHasAnyLabel(labels: List<String>): Boolean {
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return false
        return labels.any { raw ->
            val variants = listOf(raw, raw.lowercase(Locale.ROOT))
            variants.any { label ->
                xml.contains("text=\"$label\"") ||
                    xml.contains("content-desc=\"$label\"")
            }
        }
    }

    private fun shellDumpProbeNodes(labels: List<String>): List<InstagramProbeNode> {
        val xml = readShellUiDump()
        val expected = labels.map { it.trim().lowercase(Locale.ROOT) }.toSet()
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        val out = mutableListOf<InstagramProbeNode>()
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val text = shellDumpAttr(tag, "text")
            val desc = shellDumpAttr(tag, "content-desc")
            val labelsFound = sequenceOf(text, desc)
                .map { it.trim().lowercase(Locale.ROOT) }
                .filter(String::isNotEmpty)
                .filter(expected::contains)
                .distinct()
                .toList()
            if (labelsFound.isEmpty()) continue
            val b = boundsRe.find(tag) ?: continue
            out += InstagramProbeNode(
                labels = labelsFound,
                resourceName = shellDumpAttr(tag, "resource-id").substringAfterLast('/'),
                bounds = Rect(
                    b.groupValues[1].toInt(),
                    b.groupValues[2].toInt(),
                    b.groupValues[3].toInt(),
                    b.groupValues[4].toInt(),
                ),
                className = shellDumpAttr(tag, "class"),
                clickable = shellDumpAttr(tag, "clickable") == "true",
                scrollable = shellDumpAttr(tag, "scrollable") == "true",
            )
        }
        return out
    }

    private fun shellDumpAttr(nodeTag: String, name: String): String {
        val match = Regex("""$name="([^"]*)"""").find(nodeTag) ?: return ""
        return match.groupValues[1]
    }

    private fun clickInstagramLabelViaShellDump(labels: List<String>): Boolean {
        invalidateShellDumpCache()
        val hits = shellDumpProbeNodes(labels)
        val best = pickInstagramShellRowTarget(hits)
            ?: hits.maxByOrNull { node ->
                node.bounds.width().toLong() * node.bounds.height().toLong()
            }
            ?: return false
        Log.i(
            LOG_TAG,
            "event=instagram_shell_dump_click label=${labels.firstOrNull()} " +
                "bounds=${best.bounds.toShortString()}",
        )
        return shellTap(best.bounds.centerX(), best.bounds.centerY()) ||
            safeClickPoint(best.bounds.centerX(), best.bounds.centerY())
    }

    private fun shellDumpProbeByResourceSuffix(suffix: String): Rect? {
        val xml = readShellUiDump()
        if (!xml.contains("<node")) return null
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        val needle = "/$suffix"
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            val resourceId = shellDumpAttr(tag, "resource-id")
            if (resourceId != suffix && !resourceId.endsWith(needle)) continue
            val bounds = boundsRe.find(tag) ?: continue
            return Rect(
                bounds.groupValues[1].toInt(),
                bounds.groupValues[2].toInt(),
                bounds.groupValues[3].toInt(),
                bounds.groupValues[4].toInt(),
            )
        }
        return null
    }

    private fun swipeXTimeline(): Boolean {
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        if (performAccessibilityScrollForward(packageName = X_PACKAGE)) return true
        return safeSwipe(
            bounds.centerX(),
            bounds.top + (bounds.height() * 76) / 100,
            bounds.centerX(),
            bounds.top + (bounds.height() * 32) / 100,
            SWIPE_STEPS,
        )
    }

    private fun swipeBackward(): Boolean {
        val bounds = activeWindowBounds()
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        return safeSwipe(
            bounds.centerX(),
            bounds.top + (bounds.height() * 30) / 100,
            bounds.centerX(),
            bounds.top + (bounds.height() * 78) / 100,
            SWIPE_STEPS,
        )
    }

    private fun activeWindowBottom(): Int = activeWindowBounds().bottom

    private fun activeWindowBounds(): Rect {
        val statusInset = systemBarInset("status_bar_height")
        val navigationInset = systemBarInset("navigation_bar_height")
        val contentTop = statusInset.coerceIn(0, device.displayHeight / 4)
        val contentBottom = (device.displayHeight - navigationInset)
            .coerceIn(contentTop + 1, device.displayHeight)
        val fallback = Rect(0, contentTop, device.displayWidth, contentBottom)
        val root = try {
            uiAutomation.rootInActiveWindow
        } catch (_: Exception) {
            null
        } ?: return fallback
        val bounds = Rect()
        return try {
            root.getBoundsInScreen(bounds)
            bounds.left = bounds.left.coerceIn(0, device.displayWidth)
            bounds.top = bounds.top.coerceIn(contentTop, device.displayHeight)
            bounds.right = bounds.right.coerceIn(bounds.left, device.displayWidth)
            bounds.bottom = bounds.bottom.coerceIn(bounds.top, contentBottom)
            bounds.takeIf { value -> value.width() > 0 && value.height() > 0 } ?: fallback
        } catch (_: RuntimeException) {
            fallback
        }
    }

    private fun systemBarInset(resourceName: String): Int = safeUi(0) {
        val resourceId = context.resources.getIdentifier(resourceName, "dimen", "android")
        if (resourceId == 0) 0 else context.resources.getDimensionPixelSize(resourceId)
    }

    private fun clickContentCandidate(
        packageName: String,
        resourceNames: List<String>,
        descriptionFragments: List<String>,
        minimumTop: Int,
        maximumBottom: Int,
        includeImageViewFallback: Boolean = true,
    ): Boolean {
        val selectors = buildList {
            resourceNames.forEach { add(By.res(packageName, it)) }
            descriptionFragments.forEach { add(By.descContains(it)) }
            if (includeImageViewFallback) add(By.clazz("android.widget.ImageView"))
        }
        val candidate = safeUi<UiObject2?>(null) {
            selectors.asSequence()
                .flatMap { selector -> device.findObjects(selector).asSequence() }
                .mapNotNull(::clickableAncestor)
                .distinctBy { value ->
                    val bounds = safeBounds(value) ?: return@distinctBy ""
                    listOf(bounds.left, bounds.top, bounds.right, bounds.bottom).toString()
                }
                .filter { value ->
                    val bounds = safeBounds(value) ?: return@filter false
                    bounds.top >= minimumTop &&
                        bounds.bottom <= maximumBottom &&
                        bounds.width() >= device.displayWidth / 6 &&
                        bounds.height() >= device.displayWidth / 6
                }
                .minWithOrNull(
                    compareBy<UiObject2> { safeBounds(it)?.top ?: Int.MAX_VALUE }
                        .thenBy { safeBounds(it)?.left ?: Int.MAX_VALUE },
                )
        } ?: return false
        safeClick(candidate)
        waitNavigation()
        return isForeground(packageName)
    }

    private fun clickableAncestor(value: UiObject2): UiObject2? {
        var candidate: UiObject2? = value
        repeat(MAX_CLICKABLE_ANCESTOR_DEPTH + 1) {
            val current = candidate ?: return null
            if (current.isClickable) return current
            candidate = current.parent
        }
        return null
    }

    private fun clickAnyResource(packageName: String, resourceNames: List<String>): Boolean =
        resourceNames.any { resourceName -> clickResource(packageName, resourceName) }

    private fun hasAnyResource(packageName: String, resourceNames: List<String>): Boolean =
        resourceNames.any { resourceName ->
            device.hasObject(By.res(packageName, resourceName))
        }

    private fun isInstagramOwnPostSurface(): Boolean =
        instagramOwnPostActive &&
            INSTAGRAM_PACKAGE in verifiedOwnAccountPackages &&
            isForeground(INSTAGRAM_PACKAGE)

    private fun isInstagramArchiveListSurface(): Boolean =
        // Soft gate: Stories header is re-checked after open; mid-scroll Compose a11y
        // is often empty so avoid shell-dump on every scopeStillVisible poll.
        instagramArchiveListActive && isForeground(INSTAGRAM_PACKAGE)

    private fun instagramAccountMarker(nodes: List<InstagramProbeNode>): String? =
        INSTAGRAM_USERNAME_RESOURCES.asSequence()
            .flatMap { resource ->
                nodes.asSequence()
                    .filter { node -> node.resourceName == resource }
                    .flatMap { node -> node.labels.asSequence() }
            }
            .mapNotNull(::normalizeAccountMarker)
            .firstOrNull()

    private fun xAccountMarker(): String? = safeUi<String?>(null) {
        device.findObjects(By.clazz("android.widget.TextView")).asSequence()
            .filter { value -> (safeBounds(value)?.top ?: Int.MAX_VALUE) < (device.displayHeight * 2) / 3 }
            .flatMap { value -> sequenceOf(value.text, value.contentDescription) }
            .filterNotNull()
            .filter { value -> value.trim().startsWith('@') }
            .mapNotNull(::normalizeAccountMarker)
            .maxByOrNull(String::length)
    } ?: accountMarker(X_PACKAGE, X_USERNAME_RESOURCES)

    private fun accountMarker(packageName: String, resourceNames: List<String>): String? =
        safeUi(null) {
            resourceNames.forEach { resourceName ->
                val resourceMarker = device.findObjects(By.res(packageName, resourceName))
                    .asSequence()
                    .flatMap { value -> sequenceOf(value.text, value.contentDescription) }
                    .filterNotNull()
                    .mapNotNull(::normalizeAccountMarker)
                    .firstOrNull()
                if (resourceMarker != null) return@safeUi resourceMarker
            }
            device.findObjects(By.clazz("android.widget.TextView")).asSequence()
                .filter { value -> (safeBounds(value)?.top ?: Int.MAX_VALUE) < device.displayHeight / 2 }
                .flatMap { value -> sequenceOf(value.text, value.contentDescription) }
                .filterNotNull()
                .filter { value -> value.trim().startsWith('@') }
                .mapNotNull(::normalizeAccountMarker)
                .maxByOrNull { it.length }
        }

    private fun normalizeAccountMarker(value: String): String? {
        val trimmed = value.trim()
        if (trimmed.endsWith("..") || trimmed.endsWith("…")) return null
        val marker = trimmed.removePrefix("@").trim()
        if (marker.endsWith(".") || marker.contains("..")) return null
        if (!ACCOUNT_MARKER_PATTERN.matches(marker)) return null
        if (marker.lowercase() in RESERVED_ACCOUNT_MARKERS) return null
        if (NUMERIC_ACCOUNT_MARKER.matches(marker)) return null
        return marker
    }

    private fun hasAccountMarker(marker: String): Boolean =
        device.hasObject(By.text(marker)) ||
            device.hasObject(By.text("@$marker")) ||
            device.hasObject(By.descContains(marker))

    private fun clickBottomNavigation(labels: List<String>): Boolean =
        clickBoundedLabel(labels, (device.displayHeight * 2) / 3, device.displayHeight)

    private fun clickTopNavigation(labels: List<String>): Boolean =
        clickBoundedLabel(labels, 0, (device.displayHeight / 3).coerceAtLeast(1))

    private fun clickBoundedLabel(labels: List<String>, minimumTop: Int, maximumTop: Int): Boolean {
        val candidates = boundedLabelCandidates(labels, minimumTop, maximumTop)
        val target = candidates.firstOrNull { it.isClickable } ?: candidates.firstOrNull()
            ?: return false
        target.click()
        waitNavigation()
        return true
    }

    private fun boundedLabelCandidates(
        labels: List<String>,
        minimumTop: Int,
        maximumTop: Int,
    ) = safeUi(emptyList()) {
        labels.asSequence()
            .flatMap { label ->
                sequenceOf(
                    By.text(label),
                    By.desc(label),
                    By.textContains(label),
                    By.descContains(label),
                )
                    .flatMap { selector -> device.findObjects(selector).asSequence() }
            }
            .filter { value ->
                val bounds = safeBounds(value) ?: return@filter false
                // Use bottom/top band inclusively so bottom tabs at screen edge still match.
                bounds.top >= minimumTop &&
                    bounds.top <= maximumTop &&
                    bounds.left >= 0 &&
                    bounds.right <= device.displayWidth
            }
            .toList()
    }

    private fun exactBoundedLabelCandidates(
        labels: List<String>,
        minimumTop: Int,
        maximumTop: Int,
    ) = safeUi(emptyList()) {
        labels.asSequence()
            .flatMap { label ->
                sequenceOf(By.text(label), By.desc(label))
                    .flatMap { selector -> device.findObjects(selector).asSequence() }
            }
            .distinctBy { value ->
                val bounds = safeBounds(value) ?: return@distinctBy emptyList<Int>()
                listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)
            }
            .filter { value ->
                val bounds = safeBounds(value) ?: return@filter false
                bounds.top in minimumTop..maximumTop &&
                    bounds.left >= 0 &&
                    bounds.right <= device.displayWidth
            }
            .toList()
    }

    private fun waitForResource(packageName: String, resourceName: String): Boolean =
        device.wait(Until.hasObject(By.res(packageName, resourceName)), NAVIGATION_WAIT_MS)

    private fun clickExactText(labels: List<String>): Boolean =
        clickFirst(labels.map(By::text))

    private fun clickExactDescription(labels: List<String>): Boolean =
        clickFirst(labels.map(By::desc))

    private fun clickExactLabel(labels: List<String>): Boolean =
        clickExactText(labels) || clickExactDescription(labels)

    private fun clickExactTextWithScroll(labels: List<String>, maxScrolls: Int): Boolean {
        repeat(maxScrolls + 1) { attempt ->
            if (clickExactLabel(labels)) return true
            // Always use a generic swipe for menus — never the scope-specific advance.
            if (attempt < maxScrolls && !swipeForward()) return false
            waitNavigation()
        }
        return false
    }

    private fun clickExactTextWithScrollBackward(labels: List<String>, maxScrolls: Int): Boolean {
        repeat(maxScrolls + 1) { attempt ->
            if (clickExactLabel(labels)) return true
            if (attempt < maxScrolls && !swipeBackward()) return false
            waitNavigation()
        }
        return false
    }

    private fun clickFirst(selectors: List<BySelector>): Boolean {
        val selector = selectors.firstOrNull(device::hasObject) ?: return false
        return clickSelector(selector)
    }

    private fun clickSelector(selector: BySelector): Boolean {
        val matches = device.findObjects(selector)
        val target = matches.firstOrNull { value -> value.isClickable }
            ?: matches.firstOrNull()
            ?: return false
        if (safeClick(target)) {
            waitNavigation()
            return true
        }
        val bounds = safeBounds(target) ?: return false
        if (
            safeClickPoint(bounds.centerX(), bounds.centerY()) ||
            shellTap(bounds.centerX(), bounds.centerY())
        ) {
            waitNavigation()
            return true
        }
        return false
    }

    private fun clickFacebookLabeledControl(
        labels: List<String>,
        allowActionClick: Boolean = true,
    ): Boolean {
        val expected = labels.map { label -> label.trim().lowercase(Locale.ROOT) }.toSet()
        fun matchesControl(node: AccessibilityNodeInfo, bounds: Rect): Boolean {
            val nodeLabels = accessibilityLabels(node)
            val cls = node.className?.toString().orEmpty()
            return nodeLabels.any(expected::contains) ||
                (
                    expected.contains("menu") &&
                        bounds.top < 280 &&
                        bounds.left < 200 &&
                        bounds.width() in 70..180 &&
                        (node.isClickable || cls.contains("Button"))
                    )
        }
        if (
            performAccessibilityClick(
                FACEBOOK_PACKAGE,
                allowActionClick = allowActionClick,
                predicate = ::matchesControl,
            )
        ) {
            waitNavigation()
            SystemClock.sleep(350)
            return true
        }
        val matches = safeUi(emptyList<UiObject2>()) {
            labels.flatMap { label ->
                device.findObjects(By.desc(label)) + device.findObjects(By.text(label))
            }
        }
        val target = matches.firstOrNull { value -> value.isClickable }
            ?: matches.firstOrNull()
            ?: return false
        val bounds = safeBounds(target) ?: return false
        val tapped = if (allowActionClick) {
            safeClick(target) ||
                safeClickPoint(bounds.centerX(), bounds.centerY()) ||
                shellTap(bounds.centerX(), bounds.centerY())
        } else {
            false
        }
        if (tapped) {
            waitNavigation()
            SystemClock.sleep(350)
            return true
        }
        if (a11yServiceTap(bounds.centerX(), bounds.centerY())) {
            waitNavigation()
            SystemClock.sleep(350)
            return true
        }
        return false
    }

    private fun hasExactText(labels: List<String>): Boolean =
        labels.any { label -> device.hasObject(By.text(label)) }

    private fun hasExactLabel(labels: List<String>): Boolean =
        labels.any { label ->
            device.hasObject(By.text(label)) || device.hasObject(By.desc(label))
        }

    private fun hasLabelContaining(labels: List<String>): Boolean = safeUi(false) {
        labels.any { label ->
            device.hasObject(By.textContains(label)) || device.hasObject(By.descContains(label))
        }
    }

    private fun visibleTextLabels(maximumTop: Int): List<String> = safeUi(emptyList()) {
        device.findObjects(By.clazz("android.widget.TextView"))
            .asSequence()
            .filter { value -> (safeBounds(value)?.top ?: Int.MAX_VALUE) <= maximumTop }
            .flatMap { value -> sequenceOf(value.text, value.contentDescription) }
            .filterNotNull()
            .map { value -> value.trim() }
            .filter(String::isNotEmpty)
            .distinct()
            .toList()
    }

    private fun hasFacebookOwnProfileProof(): Boolean {
        if (!isForeground(FACEBOOK_PACKAGE)) return false
        if (isFacebookProfileOnboarding()) return false
        val editVisible = hasExactLabel(EDIT_PROFILE_LABELS)
        if (!editVisible) return false
        val metricVisible = facebookProfileMetricObjects().isNotEmpty()
        // Require profile-only chrome (About / All filter), not Home labels like Reels/Posts.
        val profileChrome = hasExactLabel(listOf("About", "Tentang")) ||
            hasFacebookAllFilter() ||
            hasExactLabel(listOf("Add to story", "Tambahkan ke cerita", "Edit public details"))
        return metricVisible || profileChrome
    }

    private fun facebookProfileBounds(nodes: List<VisibleNodeRecord>): List<VisibleNodeRecord> {
        val navTop = (device.displayHeight * 4) / 5
        val filtered = nodes.filter { node ->
            (node.top == 0 && node.bottom == 0 || node.top < navTop) &&
                !facebookChromeNoise(node)
        }
        return filtered.ifEmpty {
            nodes.filterNot(::facebookChromeNoise)
        }
    }

    private fun facebookChromeNoise(node: VisibleNodeRecord): Boolean {
        val label = sequenceOf(node.text, node.contentDescription)
            .filterNotNull()
            .joinToString(" ")
            .trim()
            .lowercase(Locale.ROOT)
        if (label.isEmpty()) return false
        if (FB_CHROME_NOISE.any { noise -> label == noise || label.startsWith("$noise,") }) {
            return true
        }
        if (label.contains("tab ") && label.contains(" of ")) return true
        if (label.startsWith("create, double tap")) return true
        if (label.contains("story tray")) return true
        if (label.contains("on your mind") || label.contains("di pikiranmu")) return true
        if (label.contains("create note") || label.contains("buat catatan")) return true
        if (label.contains("friend suggestion") || label.contains("saran teman")) return true
        if (label.contains("profile picture") || label.contains("foto profil")) return true
        if (label == "facebook logo" || label == "search facebook" || label == "messaging") {
            return true
        }
        return false
    }

    private fun hasMeaningfulFacebookProfileCapture(nodes: List<VisibleNodeRecord>): Boolean {
        // Own-profile proof (Edit profile + metrics/chrome) is enough — do not drop
        // the Facebook report block when a11y only returns chrome tabs.
        if (hasFacebookOwnProfileProof()) return true
        val marker = fbOwnAccountMarker
        val labels = nodes.asSequence()
            .flatMap { node -> sequenceOf(node.text, node.contentDescription) }
            .filterNotNull()
            .map { value -> value.trim() }
            .filter(String::isNotEmpty)
            .toList()
        if (labels.isEmpty()) return false
        if (marker != null && labels.any { it.equals(marker, ignoreCase = true) }) return true
        if (labels.any(FacebookProfileMetricParser::isMetricLine)) {
            return true
        }
        return labels.any { value ->
            EDIT_PROFILE_LABELS.any { it.equals(value, ignoreCase = true) }
        }
    }

    private fun hasXOwnProfileProof(): Boolean {
        if (!isForeground(X_PACKAGE)) return false
        if (xLooksLikeOtherProfile()) return false
        val editVisible = device.hasObject(By.res(X_PACKAGE, "menu_edit_profile")) ||
            hasExactLabel(EDIT_PROFILE_LABELS)
        if (!editVisible) return false
        val tabsVisible = xProfileTabsVisible()
        val handleVisible = hasAnyResource(X_PACKAGE, X_USERNAME_RESOURCES) ||
            safeUi(false) {
                device.findObjects(By.clazz("android.widget.TextView")).any { value ->
                    value.text?.trim()?.startsWith("@") == true &&
                        (safeBounds(value)?.top ?: Int.MAX_VALUE) < device.displayHeight / 2
                }
            }
        return tabsVisible || handleVisible
    }

    private fun waitForXOwnProfileProof(): Boolean {
        repeat(5) {
            if (hasXOwnProfileProof()) return true
            device.waitForIdle(NAVIGATION_IDLE_MS)
        }
        return hasXOwnProfileProof()
    }

    private fun waitForExactText(labels: List<String>): Boolean {
        if (hasExactText(labels)) return true
        if (navigationExpired()) return false
        for (label in labels) {
            val budget = waitBudgetMs(LABEL_WAIT_MS)
            if (budget <= 0L) return false
            if (
                device.wait(Until.hasObject(By.text(label)), budget) ||
                device.wait(Until.hasObject(By.textContains(label)), budget)
            ) {
                return hasExactText(labels)
            }
        }
        return false
    }

    private fun waitForExactLabel(labels: List<String>): Boolean {
        if (hasExactLabel(labels)) return true
        if (navigationExpired()) return false
        for (label in labels) {
            val budget = waitBudgetMs(LABEL_WAIT_MS)
            if (budget <= 0L) return false
            if (
                device.wait(Until.hasObject(By.text(label)), budget) ||
                device.wait(Until.hasObject(By.desc(label)), budget) ||
                device.wait(Until.hasObject(By.descContains(label)), budget)
            ) {
                return hasExactLabel(labels)
            }
        }
        return false
    }

    private fun navigationExpired(): Boolean =
        System.currentTimeMillis() >= navigationDeadlineAtMs

    private fun navigationRemainingMs(): Long =
        (navigationDeadlineAtMs - System.currentTimeMillis()).coerceAtLeast(0L)

    private fun waitBudgetMs(requested: Long): Long {
        val remaining = navigationDeadlineAtMs - System.currentTimeMillis()
        if (remaining <= 0L) return 0L
        return minOf(requested, remaining)
    }

    private fun hasHeader(labels: List<String>): Boolean {
        val maximumTop = (device.displayHeight / 3).coerceAtLeast(1)
        return labels.any { label ->
            device.findObjects(By.text(label)).any { value ->
                value.visibleBounds.top in 0 until maximumTop
            }
        }
    }

    private fun waitForHeader(labels: List<String>): Boolean {
        if (!waitForExactText(labels)) return false
        waitNavigation()
        return hasHeader(labels)
    }

    private fun hasHeaderLabel(labels: List<String>): Boolean {
        val maximumTop = (device.displayHeight / 3).coerceAtLeast(1)
        return safeUi(false) {
            labels.any { label ->
                sequenceOf(By.text(label), By.desc(label), By.textContains(label), By.descContains(label))
                    .flatMap { selector -> device.findObjects(selector).asSequence() }
                    .any { value -> (safeBounds(value)?.top ?: Int.MAX_VALUE) < maximumTop }
            }
        }
    }

    private fun waitForHeaderLabel(labels: List<String>): Boolean {
        repeat(HEADER_WAIT_ATTEMPTS) {
            if (hasHeaderLabel(labels)) return true
            if (navigationExpired()) return false
            device.waitForIdle(NAVIGATION_IDLE_MS)
        }
        return hasHeaderLabel(labels)
    }

    private fun waitNavigation() {
        SystemClock.sleep(NAVIGATION_IDLE_MS)
    }

    private fun deactivateScope(forceLedgerClear: Boolean = false) {
        val hadActiveScope = activePackage != null || activeScope != null
        val keepArchiveFlag = activeScope == SocialScope.OWN_STORY_ARCHIVE
        activePackage = null
        activeScope = null
        instagramOwnPostActive = false
        if (!keepArchiveFlag) {
            instagramArchiveListActive = false
        }
        xTimelineActive = false
        fbFeedActive = false
        fbActivityPhase = FacebookActivityPhase.NONE
        fbCommentsBoundaryReached = false
        instagramGridScrollBudget = null
        instagramArchiveScrollBudget = null
        if (forceLedgerClear || hadActiveScope) {
            store.clearVerifiedSocialScope(crawlId, System.currentTimeMillis())
        }
    }

    private fun ensureTextOnlyCoverVisible(packageName: String = activePackage.orEmpty()): Boolean {
        if (!CommunicationPolicy.usesTextOnlyCrawlCover(packageName)) return true
        if (TextOnlyCrawlCoverClient.show(context, device)) return true
        failureReason = "text_only_cover_required"
        Log.w(LOG_TAG, "event=text_only_cover_required package=$packageName")
        return false
    }

    override fun returnToAgent() {
        // Keep the TEXT_ONLY white cover up through the finished mapping frame.
        // The host owns the unpin and performs it only after starting the agent,
        // so Facebook/X chrome is never exposed between instrumentation and host
        // lifecycle restoration.
        debugMapper.capture("target_automation_finished", activeScope, "finished")
        deactivateScope(forceLedgerClear = true)
    }

    override fun close() {
        store.close()
    }

    companion object {
        private const val INSTAGRAM_PACKAGE = "com.instagram.android"
        private const val X_PACKAGE = "com.twitter.android"
        private const val FACEBOOK_PACKAGE = "com.facebook.katana"
        private const val AGENT_ACCESSIBILITY_RECEIVER =
            "com.siksik.agent/com.siksik.agent.accessibility.TextOnlyCrawlCoverReceiver"
        private const val LOG_TAG = "SIKSIKAutomation"
        private const val SWIPE_STEPS = 16
        private const val A11Y_SWIPE_STEP_MS = 12L
        private const val A11Y_SWIPE_MIN_DURATION_MS = 240L
        private const val A11Y_GESTURE_DISPATCH_WAIT_MS = 120L
        private const val TIMELINE_STAGNANT_SCROLL_LIMIT = 2
        private val SHELL_INPUT_FAILURE_MARKERS = listOf(
            "securityexception",
            "inject_events permission",
            "permission denial",
            "error while accessing settings",
        )
        private const val MAX_BACK_NAVIGATION = 4
        private const val PROFILE_PROOF_ATTEMPTS = 24
        private const val HEADER_WAIT_ATTEMPTS = 4
        private const val MIN_INSTAGRAM_OWN_PROFILE_SIGNALS = 3
        private const val MENU_SCROLL_LIMIT = 4
        private const val NAVIGATION_WAIT_MS = 3_000L
        private const val LABEL_WAIT_MS = 800L
        private const val NAVIGATION_IDLE_MS = 500L
        private const val FOREGROUND_POLL_MS = 50L
        private const val LAUNCH_VERIFY_MS = 600L
        private const val PROFILE_ACTION_INTERVAL_MS = 200L
        private const val PROFILE_PROBE_INTERVAL_MS = 250L
        // Local only — Samsung still exits on first Edit/Share proof; Infinix needs spinner headroom.
        private const val PROFILE_NAVIGATION_BUDGET_MS = 28_000L
        private const val PROFILE_LATE_SETTLE_MS = 500L
        private const val INSTAGRAM_ACTION_SETTLE_MS = 300L
        private const val INSTAGRAM_SETTINGS_LOAD_SETTLE_MS = 1_200L
        private const val INSTAGRAM_SETTINGS_VISIBLE_SETTLE_MS = 350L
        private const val INSTAGRAM_OPTIONS_MENU_WAIT_MS = 12_000L
        private const val INSTAGRAM_SETTINGS_SEARCH_SETTLE_MS = 900L
        private const val INSTAGRAM_SCROLL_SETTLE_MS = 250L
        private const val INSTAGRAM_YOUR_ACTIVITY_WAIT_MS = 10_000L
        private const val INSTAGRAM_COMMENTS_LIST_WAIT_MS = 12_000L
        private const val INSTAGRAM_SHELL_POLL_MS = 280L
        private const val INSTAGRAM_ARCHIVE_SCROLL_SETTLE_MS = 1_200L
        private const val INSTAGRAM_ARCHIVE_LOAD_SETTLE_MS = 1_200L
        private const val INSTAGRAM_ARCHIVE_PROBE_INTERVAL_MS = 300L
        private const val UI_AUTOMATOR_IDLE_TIMEOUT_MS = 250L
        private const val UI_AUTOMATOR_SELECTOR_TIMEOUT_MS = 500L
        private const val UI_DEVICE_BOUND_MS = 900L
        private const val UI_DEVICE_SELECTOR_WAIT_MS = 500L
        private const val X_SCROLL_IDLE_MS = 350L
        private const val X_SIGNATURE_IDLE_MS = 250L
        private const val DEFAULT_NAVIGATION_BUDGET_MS = 120_000L
        private const val MIN_PROFILE_TAB_PROOFS = 2
        private const val MAX_CLICKABLE_ANCESTOR_DEPTH = 4
        private const val MAX_CONTENT_ANCESTOR_DEPTH = 8
        private const val MAX_X_RETURN_TO_TABS_SWIPES = 6
        private const val X_TIMELINE_WAIT_ATTEMPTS = 4
        private const val X_CONTENT_WAIT_ATTEMPTS = 6
        private const val X_SIGNATURE_WAIT_ATTEMPTS = 3
        private const val X_ROW_BOUNDS_TOLERANCE = 12
        private const val OWNED_CONTENT_TOP_PADDING = 120
        private const val ARCHIVE_PAGE_SWIPE_PERCENT = 0.70f
        private const val ARCHIVE_PAGE_SWIPE_SPEED = 5_500
        private const val ARCHIVE_PAGE_SWIPE_STEPS = 36
        private const val SHELL_DUMP_CACHE_MS = 1_200L
        private const val SHELL_DUMP_JOIN_MS = 11_000L
        private const val SHELL_DUMP_COOLDOWN_MS = 20_000L
        private const val SHELL_DUMP_POLL_MIN_MS = 2_500L
        private const val VISIBLE_GRID_POSTS = 3
        private const val GRID_SCROLL_MIN_POSTS = 3
        private const val MAX_GRID_SCROLLS = 200
        private const val MAX_INSTAGRAM_PROBE_NODES = 512
        private const val MAX_INSTAGRAM_PROFILE_BACK_STEPS = 8
        private const val INSTAGRAM_MENU_OPEN_ATTEMPTS = 2
        private const val INSTAGRAM_ARCHIVE_PROBE_ATTEMPTS = 24
        private const val FILE_HASH_BUFFER_BYTES = 64 * 1024
        private val PROFILE_RECOVERY_ATTEMPTS = setOf(5, 13)

        private val ACCOUNT_MARKER_PATTERN = Regex("^[A-Za-z0-9._]{2,30}$")
        private val NUMERIC_ACCOUNT_MARKER = Regex("^[0-9._]+$")
        private val POST_COUNT_INLINE = Regex(
            "(?i)([0-9]+(?:[.,][0-9]+)?\\s*(?:k|m|b|rb|jt)?)" +
                "\\s*(?:posts|postingan|kiriman|tweets|tweet)\\b",
        )
        private val POST_COUNT_LABELS = setOf("posts", "postingan", "kiriman", "tweets", "tweet")
        private val INSTAGRAM_PROFILE_METRIC_GROUPS = listOf(
            setOf("posts", "postingan", "kiriman"),
            setOf("followers", "pengikut"),
            setOf("following", "mengikuti", "diikuti"),
        )
        private val INSTAGRAM_NAV_NOISE = setOf(
            "home",
            "reels",
            "message",
            "messages",
            "search and explore",
            "profile",
            "profil",
            "create",
            "buat",
        )
        private val RESERVED_ACCOUNT_MARKERS = setOf(
            "archive",
            "arsip",
            "comments",
            "instagram",
            "interactions",
            "komentar",
            "needed",
            "posts",
            "postingan",
            "profile",
            "profil",
            "replies",
            "balasan",
            "twitter",
        )

        private val EDIT_PROFILE_LABELS = listOf(
            "Edit profile",
            "Edit Profile",
            "Edit profil",
            "Edit Profil",
            "Sunting profil",
            "Ubah profil",
            "Ubah Profil",
        )
        private val INSTAGRAM_COMMENTS_CHROME_LABELS = setOf(
            "comments",
            "komentar",
            "select",
            "pilih",
            "back",
            "kembali",
            "newest to oldest",
            "terbaru ke terlama",
            "all dates",
            "semua tanggal",
            "all authors",
            "semua penulis",
            "semua pemb",
            "filter by date",
            "filter berdasarkan tanggal",
            "select multiple comments to delete",
            "pilih beberapa komentar untuk dihapus",
        )
        private val INSTAGRAM_COMMENTS_CHROME_FRAGMENTS = listOf(
            "newest to oldest",
            "terbaru ke terlama",
            "all dates",
            "semua tanggal",
            "all authors",
            "semua penulis",
            "filter by date",
            "filter berdasarkan",
            "select multiple",
            "pilih beberapa",
        )
        private val AUTH_WALL_LABELS = listOf(
            "Log in",
            "Log In",
            "Login",
            "Masuk",
            "Sign in",
            "Sign In",
            "Sign up",
            "Sign Up",
            "Create account",
            "Create Account",
            "Buat akun",
            "Buat Akun",
            "Daftar",
        )
        private val AUTH_WALL_FRAGMENTS = listOf(
            "use phone or email",
            "gunakan nomor ponsel atau email",
            "already have an account",
            "sudah punya akun",
            "forgot password",
            "lupa kata sandi",
            "create your account",
            "buat akun anda",
        )
        private val SHARE_PROFILE_LABELS = listOf(
            "Share profile",
            "Share Profile",
            "Share profil",
            "Share Profil",
            "Bagikan profil",
            "Bagikan Profil",
        )
        private val INSTAGRAM_PROFILE_METRIC_LABELS = listOf(
            "followers",
            "following",
            "posts",
            "postingan",
            "pengikut",
            "mengikuti",
            "diikuti",
            "Followers",
            "Following",
            "Posts",
            "Postingan",
            "Kiriman",
            "Pengikut",
            "Mengikuti",
            "Diikuti",
        )
        private val INSTAGRAM_PROFILE_LABELS = listOf("Profile", "Profil", "You", "Anda")
        private val INSTAGRAM_OWN_PROFILE_LABELS = listOf(
            "Your profile",
            "Profil Anda",
            "Profile Anda",
        )
        private val INSTAGRAM_PROFILE_DESC_FRAGMENTS = listOf(
            "Profile",
            "Profil",
            "Your profile",
            "profil Anda",
        )
        private val INSTAGRAM_OTHER_PROFILE_LABELS = listOf(
            "Follow",
            "Follow back",
            "Ikuti",
            "Ikuti balik",
        )
        private val INSTAGRAM_BLOCKING_DIALOG_DISMISS_LABELS = listOf(
            "Not now",
            "Nanti saja",
            "Cancel",
            "Batal",
            "Skip",
            "Lewati",
            "Close",
            "Tutup",
            "OK",
            "Mengerti",
        )
        private val INSTAGRAM_PROFILE_COACHMARK_FRAGMENTS = listOf(
            "Try sharing a song",
            "sharing a song",
            "Inspo needed",
            "Just curious",
            "Make this space yours",
            "Today's vibe",
        )
        private val INSTAGRAM_PROFILE_SCROLLED_AWAY_FRAGMENTS = listOf(
            "Complete your profile",
            "Add profile picture",
            "Add picture",
            "Edit name",
            "Add your name",
        )
        private val INSTAGRAM_PROFILE_RESOURCES = listOf(
            "profile_tab",
            "profile_tab_button",
            "profile_tab_icon",
            "tab_profile",
            "profile_tab_layout",
            "tab_avatar",
        )
        private val INSTAGRAM_PROFILE_HEADER_RESOURCES = listOf(
            "profile_header_container",
            "row_profile_header",
            "profile_header_fixed_list",
            "profile_header_actions_top_row",
            "profile_header_full_name_above_vanity",
            "profile_header_metrics_full_width",
        )
        private val INSTAGRAM_USERNAME_RESOURCES = listOf(
            "action_bar_title",
            "profile_header_user_name",
            "profile_header_username",
            "action_bar_large_title_auto_size",
        )
        private val INSTAGRAM_PROFILE_EVIDENCE_RESOURCES = (
            INSTAGRAM_USERNAME_RESOURCES + listOf(
                "profile_header_full_name_above_vanity",
                "profile_header_full_name",
                "profile_header_bio",
                "profile_bio",
                "profile_header_website",
                "profile_header_link",
                "profile_header_familiar_post_count_value",
                "profile_header_post_count_front_familiar",
                "profile_header_familiar_followers_value",
                "profile_header_followers_stacked_familiar",
                "profile_header_familiar_following_value",
                "profile_header_following_stacked_familiar",
            )
            ).distinct()
        private val INSTAGRAM_POST_USERNAME_RESOURCES = listOf(
            "row_feed_photo_profile_name",
            "row_feed_photo_profile_name_button",
            "row_feed_photo_profile_name_text_view",
            "feed_user_name",
        )
        private val INSTAGRAM_GRID_TAB_RESOURCES = listOf(
            "profile_grid_tab",
            "profile_tab_grid",
            "grid_tab",
            "profile_tab_icon_view",
        )
        private val INSTAGRAM_POST_ITEM_RESOURCES = listOf(
            "media_grid_item",
            "profile_grid_item",
            "image_button",
            "thumbnail",
            "image_view",
        )
        private val INSTAGRAM_POST_DESCRIPTION_FRAGMENTS = listOf(
            "Photo by",
            "Foto oleh",
            "Post by",
            "Postingan oleh",
        )
        private val INSTAGRAM_POST_DETAIL_LABELS = listOf(
            "Posts",
            "Post",
            "Postingan",
        )
        private val INSTAGRAM_POST_ACTION_LABELS = listOf(
            "Like",
            "Suka",
            "Comment",
            "Komentar",
            "Share",
            "Bagikan",
        )
        private val INSTAGRAM_POST_DETAIL_RESOURCES = listOf(
            "row_feed_photo_imageview",
            "row_feed_photo_profile_name",
            "media_group",
            "post_viewer_root",
        )
        private val INSTAGRAM_OPTIONS_LABELS = listOf(
            "Options",
            "Opsi",
            "Menu",
            "More options",
            "Opsi lainnya",
        )
        private val INSTAGRAM_OPTIONS_NORMALIZED_LABELS =
            INSTAGRAM_OPTIONS_LABELS.map { value -> value.lowercase(Locale.ROOT) }.toSet()
        private val INSTAGRAM_OPTIONS_RESOURCES = listOf(
            "profile_header_menu",
            "action_bar_overflow_icon",
            "menu_button",
        )
        private val INSTAGRAM_PROFILE_MENU_EXCLUDED_LABELS = listOf(
            "create",
            "buat",
            "highlight",
            "sorotan",
            "threads",
            "setting",
            "pengaturan",
            "professional",
            "profesional",
        )
        private val ARCHIVE_LABELS = listOf("Archive", "Arsip")
        private val ARCHIVE_NORMALIZED_LABELS =
            ARCHIVE_LABELS.map { value -> value.lowercase(Locale.ROOT) }.toSet()
        private val STORY_ARCHIVE_LABELS = listOf(
            "Stories archive",
            "Story archive",
            "Arsip cerita",
            "Arsip Cerita",
        )
        private val INSTAGRAM_HEADER_BACK_LABELS = listOf(
            "Back",
            "Kembali",
            "Navigate up",
            "Navigate back",
        )
        private const val INSTAGRAM_HEADER_BACK_RESOURCE = "action_bar_button_back"
        private val INSTAGRAM_COMMENTS_LIST_CHROME_LABELS = listOf(
            "Newest to oldest",
            "Oldest to newest",
            "All dates",
            "All authors",
            "Select",
            "Terbaru ke terlama",
            "Terlama ke terbaru",
            "Semua tanggal",
            "Semua penulis",
            "Pilih",
        )
        private val STORY_ARCHIVE_NORMALIZED_LABELS =
            STORY_ARCHIVE_LABELS.map { value -> value.lowercase(Locale.ROOT) }.toSet()
        private val INSTAGRAM_NON_STORY_ARCHIVE_LABELS = listOf(
            "Posts archive",
            "Arsip postingan",
            "Arsip Postingan",
            "Live archive",
            "Arsip siaran langsung",
            "Arsip Siaran Langsung",
        )
        private val INSTAGRAM_ARCHIVE_MODE_LABELS =
            STORY_ARCHIVE_LABELS + INSTAGRAM_NON_STORY_ARCHIVE_LABELS
        private val INSTAGRAM_ARCHIVE_PAGE_LABELS =
            INSTAGRAM_ARCHIVE_MODE_LABELS + ARCHIVE_LABELS
        private val INSTAGRAM_ARCHIVE_HEADER_NORMALIZED_LABELS =
            INSTAGRAM_ARCHIVE_PAGE_LABELS
                .map { value -> value.lowercase(Locale.ROOT) }
                .toSet()
        private val INSTAGRAM_ARCHIVE_ITEM_RESOURCES = listOf(
            "archive_grid_item",
            "story_archive_item",
            "reel_viewer_thumbnail",
            "thumbnail",
        )
        private val INSTAGRAM_ARCHIVE_DESCRIPTION_FRAGMENTS = listOf(
            "Story from",
            "Archived story",
            "Cerita dari",
            "Cerita yang diarsipkan",
        )
        private val YOUR_ACTIVITY_LABELS = listOf("Your activity", "Aktivitas Anda")
        private val INSTAGRAM_OPTIONS_MENU_COMPANION_LABELS = listOf(
            YOUR_ACTIVITY_LABELS,
            listOf("Saved", "Disimpan"),
            listOf("QR code", "Kode QR"),
            listOf("Settings and activity", "Pengaturan dan aktivitas", "Setelan dan aktivitas"),
            listOf("Settings and privacy", "Pengaturan dan privasi", "Setelan dan privasi"),
            listOf("Accounts Center", "Pusat Akun"),
            listOf("Close Friends", "Teman Dekat"),
        )
        private val INSTAGRAM_OPTIONS_MENU_COMPANION_FLAT =
            INSTAGRAM_OPTIONS_MENU_COMPANION_LABELS.flatten()
        private val INTERACTIONS_LABELS = listOf("Interactions", "Interaksi")
        private val COMMENTS_LABELS = listOf("Comments", "Komentar")
        private val X_PROFILE_LABELS = listOf("Profile", "Profil")
        private val X_USERNAME_RESOURCES = listOf(
            "screen_name",
            "username",
            "user_name",
        )
        private val X_POSTS_LABELS = listOf("Posts", "Postingan", "Kiriman", "Tweets", "Tweet")
        private val X_REPLIES_LABELS = listOf("Replies", "Balasan")
        private val X_SECONDARY_TAB_LABELS = listOf(
            "Highlights",
            "Sorotan",
            "Media",
            "Likes",
            "Suka",
            "Articles",
            "Artikel",
        )
        private val X_TIMELINE_RESOURCES = listOf(
            "row",
            "tweet_content_text",
            "outer_layout_row_view_tweet",
            "tweet",
            "tweet_text",
            "timeline",
            "timeline_content",
        )
        private val X_TIMELINE_NOISE = setOf(
            "posts",
            "postingan",
            "tweets",
            "tweet",
            "replies",
            "balasan",
            "reply",
            "repost",
            "like",
            "share",
            "views",
            "suka",
            "bagikan",
        )
        private val X_PROFILE_CHROME_NOISE = setOf(
            "navigate up",
            "profile image",
            "search button",
            "more options",
            "edit profile",
            "get verified",
            "posts",
            "replies",
            "highlights",
            "articles",
            "media",
            "likes",
            "following",
            "followers",
        )
        private val X_EMPTY_TIMELINE_LABELS = listOf(
            "No posts yet",
            "No replies yet",
            "Belum ada postingan",
            "Belum ada balasan",
            "hasn't posted",
            "hasn’t posted",
            "haven't posted",
            "haven’t posted",
            "You haven't posted yet",
            "You haven’t posted yet",
            "When you send posts or replies",
            "belum memposting",
            "Post now",
        )
        private val X_DETAIL_LABELS = listOf("Post", "Postingan")
        private val X_DETAIL_RESOURCES = listOf(
            "tweet",
            "tweet_text",
            "detail_content",
            "tweet_detail",
        )
        private val FACEBOOK_MENU_LABELS = listOf("Menu")
        private val FACEBOOK_OWN_PROFILE_LABELS = listOf(
            "See your profile",
            "Lihat profil Anda",
            "Go to profile",
            "Buka profil",
        )
        private val FACEBOOK_PROFILE_TAB_DESC = listOf(
            "Profile, tab",
            "Profil, tab",
            "Profile tab",
            "Tab Profil",
        )
        private val FACEBOOK_MORE_LABELS = listOf("More", "Lainnya")
        private val FACEBOOK_MORE_PROFILE_SETTINGS_LABELS = listOf(
            "More profile settings",
            "Pengaturan profil lainnya",
            "Setelan profil lainnya",
            "See more profile settings",
            "Lihat pengaturan profil lainnya",
            "Profile settings",
            "Pengaturan profil",
            "Setelan profil",
        )
        private val FACEBOOK_ACTIVITY_ALL_FILTER_LABELS = listOf("All", "Semua")
        private val FACEBOOK_COMMENTS_LABELS = listOf("Comments", "Komentar")
        private val FACEBOOK_LIKES_REACTIONS_LABELS = listOf(
            "Likes",
            "Suka",
            "Reactions",
            "Reaksi",
            "Likes and reactions",
            "Suka dan reaksi",
            "Likes and responses",
            "Suka dan tanggapan",
        )
        private val FACEBOOK_ACTIVITY_LOG_LABELS = listOf(
            "Activity log",
            "Activity Log",
            "Log aktivitas",
            "Log Aktivitas",
            "Your activity",
            "Aktivitas Anda",
            "Facebook activity",
            "Aktivitas Facebook",
        )
        private val FACEBOOK_YOUR_ACTIVITY_LABELS = listOf(
            "Your Facebook activity",
            "Aktivitas Facebook Anda",
            "Your activity",
            "Aktivitas Anda",
        )
        private val FACEBOOK_YOUR_ACTIVITY_DESC = listOf(
            "Your Facebook activity",
            "Aktivitas Facebook Anda",
        )
        private val FACEBOOK_COMMENTS_REACTIONS_LABELS = listOf(
            "Comments and reactions",
            "Komentar dan reaksi",
            "Comments and responses",
            "Komentar dan tanggapan",
            "Manage comments and reactions",
            "Manage comments and responses",
            "Kelola komentar dan reaksi",
            "Kelola komentar dan tanggapan",
        )
        private val FACEBOOK_COMMENTS_REACTIONS_DESC = listOf(
            "Comments and reactions",
            "Komentar dan reaksi",
            "Comments and responses",
            "Komentar dan tanggapan",
        )
        private const val FACEBOOK_ACTIVITY_ITEM_RESOURCE = "activity-log-item"
        private val FACEBOOK_ALL_FILTER_LABELS = listOf("All", "Semua")
        private val FACEBOOK_ALL_FILTER_DESC = listOf("All, 1 of", "Semua, 1 dari")
        private val FACEBOOK_METRIC_HINTS = listOf(
            "friends",
            "teman",
            "following",
            "mengikuti",
            "posts",
            "postingan",
        )
        private val FACEBOOK_EMPTY_COMMENTS_LABELS = listOf(
            "No items",
            "Tidak ada item",
            "No Comments",
            "Belum ada komentar",
            "No likes or reactions",
            "No reactions",
            "Belum ada suka atau reaksi",
            "Belum ada reaksi",
        )
        private val FACEBOOK_PROFILE_ONBOARDING_FRAGMENTS = listOf(
            "Selamat datang di profil Anda",
            "Welcome to your profile",
            "Pilih kota asal",
            "Choose your hometown",
            "Add your hometown",
            "Tambahkan kota sekarang",
            "menyiapkan profil Anda",
            "setting up your profile",
        )
        private val FACEBOOK_PROFILE_SETUP_STOP_FRAGMENTS = listOf(
            "Berhenti menyiapkan profil",
            "Stop setting up your profile",
        )
        private val FACEBOOK_PROFILE_PHOTO_SETUP_LABELS = listOf(
            "Edit foto profil",
            "Edit profile photo",
            "Tambahkan foto",
            "Add photo",
        )
        private val FACEBOOK_PROFILE_ONBOARDING_SKIP_LABELS = listOf(
            "Lewati",
            "Skip",
            "Batal",
            "Cancel",
            "Kembali",
            "Back",
        )
        private val FACEBOOK_PROFILE_SETUP_STOP_LABELS = listOf(
            "BERHENTI",
            "Berhenti",
            "Stop",
        )
        private val FACEBOOK_POST_ACTION_DESC = listOf(
            "Comment",
            "Komentar",
            "Like",
            "Suka",
            "Share",
            "Bagikan",
        )
        private val FACEBOOK_PROFILE_TAB_LABELS = listOf(
            listOf("Posts", "Postingan"),
            listOf("About", "Tentang"),
            listOf("Photos", "Foto"),
            listOf("Reels"),
            listOf("Mentions", "Sebutan"),
            listOf("All", "Semua"),
        )
        private val FB_RESERVED_DISPLAY_NAMES = setOf(
            "facebook",
            "edit profile",
            "edit profil",
            "add to story",
            "tambahkan ke cerita",
            "search",
            "cari",
            "menu",
            "friends",
            "following",
            "posts",
            "photos",
            "reels",
            "all",
            "people you may know",
            "personal details",
            "what's on your mind?",
            "what’s on your mind?",
            "create note: what’s on your mind?",
            "create note: what's on your mind?",
            "remove friend suggestion",
            "remove",
            "profile picture",
            "foto profil",
            "add",
            "camera",
        )
        private val FB_CHROME_NOISE = setOf(
            "menu",
            "facebook logo",
            "search facebook",
            "messaging",
            "story tray",
            "home",
            "reels",
            "friends",
            "groups",
            "notifications",
            "profile",
            "create",
        )
        private val FB_POST_NOISE = setOf(
            "like",
            "suka",
            "comment",
            "komentar",
            "share",
            "bagikan",
            "follow",
            "ikuti",
            "add friend",
            "tambah teman",
            "see translation",
            "lihat terjemahan",
            "sponsored",
            "disponsori",
            "all posts",
            "semua postingan",
            "filter",
            "reel",
            "reels",
            "live",
            "siaran langsung",
            "what's on your mind?",
            "what’s on your mind?",
            "apa yang anda pikirkan?",
            "apa yang anda pikirkan",
            "manage posts",
            "kelola postingan",
            "add to story",
            "tambahkan ke cerita",
        )
        private val FB_POST_NOISE_FRAGMENTS = setOf(
            "perbarui profil anda",
            "update your profile",
            "kami mempermudah anda",
            "we made it easier for you",
            "manfaatkan facebook dengan lebih maksimal",
            "get more out of facebook",
            "lengkapi profil anda",
            "complete your profile",
            "siapkan profil anda",
            "set up your profile",
        )
        private val FB_COMMENT_NOISE = setOf(
            "comments",
            "komentar",
            "comments and reactions",
            "komentar dan reaksi",
            "comments and responses",
            "komentar dan tanggapan",
            "likes",
            "suka",
            "reactions",
            "reaksi",
            "likes and reactions",
            "suka dan reaksi",
            "likes and responses",
            "suka dan tanggapan",
            "manage comments and reactions",
            "manage comments and responses",
            "kelola komentar dan reaksi",
            "kelola komentar dan tanggapan",
            "facebook comments",
            "facebook likes/reactions",
            "facebook comments/reactions",
            "all",
            "semua",
            "select all",
            "pilih semua",
            "archive",
            "arsip",
            "trash",
            "sampah",
            "activity log",
            "log aktivitas",
            "no items",
            "tidak ada item",
            "back",
            "kembali",
            "search",
            "cari",
            "delete",
            "hapus",
            "public",
            "publik",
            "learn more",
            "not all of your items may appear here. learn more.",
            "tidak semua item anda bisa ditampilkan di sini. pelajari selengkapnya.",
        )
        private val FB_XML_LABEL_ATTRIBUTE = Regex("""(?:text|content-desc)="([^"]*)"""")
        private val FB_INVISIBLE_FORMATTING = Regex("[\\u200B-\\u200F\\u2060\\uFEFF]")
        private val FB_CLOCK_PATTERN =
            Regex(
                """(?i)^\d{1,2}:\d{2}(\s*[ap]m)?$|^\d{1,2}\s*[ap]m$""",
            )
        private val FB_RELATIVE_TIME_PATTERN =
            Regex(
                """(?i)^(\d+\s*(s|m|h|d|w|min|mins|minute|minutes|hour|hours|hr|hrs|day|days|week|weeks|mo|mos|month|months|y|yr|yrs|year|years)|just now|yesterday|a (minute|hour|day|week|month|year) ago)$""",
            )
        private val FB_SHARED_WITH_PATTERN =
            Regex("""shared with|dibagikan ke|•""", RegexOption.IGNORE_CASE)
        private const val FB_CONTENT_WAIT_ATTEMPTS = 4
        private const val FB_SCROLL_IDLE_MS = 450L
        private const val MAX_FACEBOOK_PROFILE_METRIC_OBJECTS = 32
    }
}
