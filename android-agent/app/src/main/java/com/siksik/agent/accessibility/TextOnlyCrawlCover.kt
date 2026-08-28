package com.siksik.agent.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.view.WindowManager.LayoutParams
import android.widget.TextView
import com.siksik.agent.R

internal class TextOnlyCrawlCover(
    private val service: AccessibilityService,
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private var attachedWindowManager: WindowManager? = null
    private var sheet: View? = null
    @Volatile
    private var pinnedVisible = false

    fun onForegroundPackage(@Suppress("UNUSED_PARAMETER") packageName: String) {
        // A pin exists only for an active X/Facebook crawl. Keep the cover
        // attached across transient OEM/system windows until the host explicitly
        // unpins it after restoring the agent foreground.
        if (pinnedVisible) show() else hide()
    }

    fun setPinned(visible: Boolean) {
        pinnedVisible = visible
        if (visible) show() else hide()
    }

    fun show() {
        mainHandler.post { attach() }
    }

    fun hide() {
        mainHandler.post { detach() }
    }

    fun isAttached(): Boolean = sheet?.isAttachedToWindow == true

    private fun attach() {
        val existing = sheet
        if (existing?.isAttachedToWindow == true) {
            TextOnlyCrawlCoverState.markAttached(service.applicationContext, true)
            return
        }
        // AccessibilityService is constructed before onServiceConnected(). A
        // WindowManager cached in the constructor can therefore retain an OEM
        // window context without the live accessibility overlay token. Resolve
        // it only when the host actually requests the cover.
        sheet = null
        attachedWindowManager = null
        val wm = service.getSystemService(WindowManager::class.java)
        if (wm == null) {
            TextOnlyCrawlCoverState.markAttached(service.applicationContext, false)
            Log.w(LOG_TAG, "event=text_only_cover_show_failed reason=no_window_manager")
            return
        }
        val cover = LayoutInflater.from(service)
            .inflate(R.layout.activity_bootstrap, null, false)
            .apply {
                findViewById<TextView>(R.id.bootstrap_status)
                    .setText(R.string.text_only_cover_status)
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
                keepScreenOn = true
                if (Build.VERSION.SDK_INT >= 29) {
                    isForceDarkAllowed = false
                }
            }
        val flags = LayoutParams.FLAG_NOT_FOCUSABLE or
            LayoutParams.FLAG_NOT_TOUCHABLE or
            LayoutParams.FLAG_NOT_TOUCH_MODAL or
            LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            LayoutParams.FLAG_LAYOUT_NO_LIMITS or
            LayoutParams.FLAG_FULLSCREEN or
            LayoutParams.FLAG_HARDWARE_ACCELERATED
        val params = LayoutParams(
            LayoutParams.MATCH_PARENT,
            LayoutParams.MATCH_PARENT,
            LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            flags,
            PixelFormat.OPAQUE,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            alpha = 1f
            dimAmount = 0f
            screenBrightness = 1f
            buttonBrightness = 1f
            if (Build.VERSION.SDK_INT >= 28) {
                layoutInDisplayCutoutMode =
                    LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
            if (Build.VERSION.SDK_INT >= 30) {
                layoutInDisplayCutoutMode =
                    LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                fitInsetsTypes = 0
                fitInsetsSides = 0
                isFitInsetsIgnoringVisibility = true
            }
        }
        try {
            wm.addView(cover, params)
            sheet = cover
            attachedWindowManager = wm
            TextOnlyCrawlCoverState.markAttached(service.applicationContext, true)
            Log.i(LOG_TAG, "event=text_only_cover_shown")
        } catch (error: RuntimeException) {
            TextOnlyCrawlCoverState.markAttached(service.applicationContext, false)
            Log.w(
                LOG_TAG,
                "event=text_only_cover_show_failed type=${error.javaClass.simpleName} " +
                    "message=${error.message.orEmpty().replace(' ', '_').take(160)}",
                error,
            )
        }
    }

    private fun detach() {
        val cover = sheet
        val wm = attachedWindowManager
        sheet = null
        attachedWindowManager = null
        if (cover != null) {
            try {
                wm?.removeView(cover)
            } catch (error: RuntimeException) {
                Log.w(
                    LOG_TAG,
                    "event=text_only_cover_hide_failed type=${error.javaClass.simpleName}",
                )
            }
        }
        TextOnlyCrawlCoverState.markAttached(service.applicationContext, false)
        if (cover != null) Log.i(LOG_TAG, "event=text_only_cover_hidden")
    }

    companion object {
        private const val LOG_TAG = "SIKSIKAccessibility"
    }
}
