package com.siksik.agent.preprocessing

object OnDeviceVisionPolicy {
    fun shouldRunOcr(
        sourceKind: String,
        displayName: String?,
        directoryHint: String?,
    ): Boolean {
        if (sourceKind !in OCR_SOURCE_KINDS) return false
        return originLooksTextHeavy(displayName, directoryHint)
    }

    fun originLooksTextHeavy(displayName: String?, directoryHint: String?): Boolean {
        val hay = listOfNotNull(displayName, directoryHint)
            .joinToString(" ")
            .lowercase()
            .replace('\\', '/')
        if (hay.isBlank()) return false
        return ORIGIN_HINTS.any { hint -> hay.contains(hint) }
    }

    private val OCR_SOURCE_KINDS = setOf("media_image", "media_video")
    private val ORIGIN_HINTS = listOf(
        "screenshot",
        "screen_shot",
        "screen-shot",
        "screencap",
        "captures",
        "whatsapp",
        "telegram",
        "signal",
        "messenger",
        "/chat",
        "chat/",
        "notif",
        "documents",
        "document",
        "download",
        "screenshots",
    )
}
