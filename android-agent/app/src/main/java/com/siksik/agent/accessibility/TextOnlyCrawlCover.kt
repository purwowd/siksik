package com.siksik.agent.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.WindowManager.LayoutParams
import com.siksik.agent.source.communication.CommunicationPolicy

internal class TextOnlyCrawlCover(
    private val service: AccessibilityService,
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val windowManager = service.getSystemService(WindowManager::class.java)
    private var sheet: View? = null
    @Volatile
    private var pinnedVisible = false

    fun onForegroundPackage(packageName: String) {
        when {
            packageName == INSTAGRAM_PACKAGE -> hide()
            pinnedVisible -> show()
            CommunicationPolicy.usesTextOnlyCrawlCover(packageName) -> show()
            else -> hide()
        }
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

    private fun attach() {
        if (sheet != null) return
        val wm = windowManager ?: return
        val cover = View(service).apply {
            setBackgroundColor(Color.WHITE)
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
            Log.i(LOG_TAG, "event=text_only_cover_shown")
        } catch (_: RuntimeException) {
            Log.w(LOG_TAG, "event=text_only_cover_show_failed")
        }
    }

    private fun detach() {
        val cover = sheet ?: return
        sheet = null
        try {
            windowManager?.removeView(cover)
        } catch (_: RuntimeException) {}
        Log.i(LOG_TAG, "event=text_only_cover_hidden")
    }

    companion object {
        private const val LOG_TAG = "SIKSIKAccessibility"
        private const val INSTAGRAM_PACKAGE = "com.instagram.android"
    }
}
