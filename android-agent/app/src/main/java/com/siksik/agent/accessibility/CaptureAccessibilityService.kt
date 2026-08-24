package com.siksik.agent.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import com.siksik.agent.BuildConfig
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.CaptureEventGate
import com.siksik.agent.source.communication.VisibleUiSnapshotter

class CaptureAccessibilityService : AccessibilityService() {
    private lateinit var store: CommunicationCaptureStore
    private lateinit var textOnlyCover: TextOnlyCrawlCover

    @Volatile
    private var destroyed = false
    private val eventGate = CaptureEventGate(MIN_CAPTURE_INTERVAL_MS) {
        SystemClock.elapsedRealtime()
    }

    override fun onCreate() {
        super.onCreate()
        store = CommunicationCaptureStore(applicationContext)
        textOnlyCover = TextOnlyCrawlCover(this)
        // An overlay window cannot survive a service-process recreation. Clear
        // any persisted attach marker before accepting host probe results.
        TextOnlyCrawlCoverState.markAttached(applicationContext, false)
    }

    override fun onServiceConnected() {
        activeService = this
        updatePackageFilter()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val value = event ?: return
        val packageName = value.packageName?.toString() ?: return
        textOnlyCover.onForegroundPackage(packageName)
        val active = store.activeSession() ?: return
        if (packageName !in active.targetPackages) return
        if (packageName in CommunicationPolicy.supportedSocialTargets) return
        val socialScope = store.activeVerifiedSocialScope(active.crawlId, packageName) ?: return
        if (!eventGate.allow(packageName, active.targetPackages)) return
        updatePackageFilter(active.targetPackages)
        val root = value.source ?: rootInActiveWindow ?: return
        val nodes = VisibleUiSnapshotter.snapshot(root)
        if (nodes.isEmpty()) return
        val activityContext = CommunicationPolicy.boundedText(value.className, 512)
        val contentHash = CommunicationPolicy.visibleUiContentHash(
            packageName,
            socialScope,
            nodes,
        )
        val normalizedText = CommunicationPolicy.joinedText(
            nodes.flatMap { listOf(it.text, it.contentDescription) },
            BuildConfig.MAX_SMS_TEXT_LENGTH,
        )
        store.recordVisibleSnapshot(
            packageName = packageName,
            windowId = value.windowId,
            activityContext = activityContext,
            eventType = value.eventType,
            eventTime = value.eventTime.takeIf { it > 0 } ?: System.currentTimeMillis(),
            nodes = nodes,
            normalizedText = normalizedText,
            contentHash = contentHash,
            socialScope = socialScope,
            screenshotIds = emptyList(),
            now = System.currentTimeMillis(),
        )
    }

    override fun onInterrupt() {
        if (destroyed || !::store.isInitialized) return
        store.markAccessibilityIssue("accessibility_interrupted", System.currentTimeMillis())
        eventGate.clear()
    }

    override fun onUnbind(intent: Intent?): Boolean {
        if (!destroyed && ::store.isInitialized && store.activeSession() != null) {
            store.markAccessibilityIssue("accessibility_disconnected", System.currentTimeMillis())
        }
        if (activeService === this) activeService = null
        textOnlyCover.hide()
        eventGate.clear()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        destroyed = true
        if (activeService === this) activeService = null
        if (::textOnlyCover.isInitialized) textOnlyCover.hide()
        if (::store.isInitialized) store.close()
        super.onDestroy()
    }

    private fun dispatchTap(x: Int, y: Int): Boolean {
        val metrics = resources.displayMetrics
        if (x !in 0 until metrics.widthPixels || y !in 0 until metrics.heightPixels) return false
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, TAP_DURATION_MS))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    private fun dispatchSwipe(
        xFrom: Int,
        yFrom: Int,
        xTo: Int,
        yTo: Int,
        durationMs: Long,
    ): Boolean {
        val metrics = resources.displayMetrics
        if (
            xFrom !in 0 until metrics.widthPixels || xTo !in 0 until metrics.widthPixels ||
            yFrom !in 0 until metrics.heightPixels || yTo !in 0 until metrics.heightPixels
        ) {
            return false
        }
        val path = Path().apply {
            moveTo(xFrom.toFloat(), yFrom.toFloat())
            lineTo(xTo.toFloat(), yTo.toFloat())
        }
        val gesture = GestureDescription.Builder()
            .addStroke(
                GestureDescription.StrokeDescription(
                    path,
                    0L,
                    durationMs.coerceIn(MIN_SWIPE_DURATION_MS, MAX_SWIPE_DURATION_MS),
                ),
            )
            .build()
        return dispatchGesture(gesture, null, null)
    }

    private fun updatePackageFilter(targets: Set<String>? = null) {
        val packages = targets ?: store.activeSession()?.targetPackages ?: emptySet()
        val current = serviceInfo ?: return
        current.packageNames = packages.toTypedArray()
        serviceInfo = current
    }

    companion object {
        @Volatile
        private var activeService: CaptureAccessibilityService? = null

        fun applyTextOnlyCover(visible: Boolean): Boolean {
            val service = activeService ?: return false
            service.textOnlyCover.setPinned(visible)
            return true
        }

        fun isTextOnlyCoverAttached(): Boolean =
            activeService?.textOnlyCover?.isAttached() == true

        fun applyAccessibilityTap(x: Int, y: Int): Boolean =
            activeService?.dispatchTap(x, y) ?: false

        fun applyAccessibilitySwipe(
            xFrom: Int,
            yFrom: Int,
            xTo: Int,
            yTo: Int,
            durationMs: Long,
        ): Boolean = activeService?.dispatchSwipe(
            xFrom,
            yFrom,
            xTo,
            yTo,
            durationMs,
        ) ?: false

        fun applyAccessibilityBack(): Boolean =
            activeService?.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK) ?: false

        fun isServiceConnected(): Boolean = activeService != null

        private const val MIN_CAPTURE_INTERVAL_MS = 500L
        private const val TAP_DURATION_MS = 80L
        private const val MIN_SWIPE_DURATION_MS = 120L
        private const val MAX_SWIPE_DURATION_MS = 1_500L
    }
}
