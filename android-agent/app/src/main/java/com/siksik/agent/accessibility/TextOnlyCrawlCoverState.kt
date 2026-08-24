package com.siksik.agent.accessibility

import android.content.Context

/** OEM-agnostic cover attach state for host/instrumentation probe fallbacks. */
internal object TextOnlyCrawlCoverState {
    private const val PREFS_NAME = "siksik_text_only_cover"

    fun markAttached(context: Context, attached: Boolean) {
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ATTACHED, attached)
            .commit()
    }

    fun isMarkedAttached(context: Context): Boolean =
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_ATTACHED, false)

    private const val KEY_ATTACHED = "attached"
}
