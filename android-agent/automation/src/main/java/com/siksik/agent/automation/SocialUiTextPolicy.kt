package com.siksik.agent.automation

import java.util.Locale

internal object SocialUiTextPolicy {
    private val facebookSuggestionFragments = setOf(
        "people you may know",
        "orang yang mungkin anda kenal",
        "friend suggestions",
        "saran teman",
        "remove friend suggestion",
        "hapus saran teman",
    )
    private val xProfileMetadataPrefixes = setOf(
        "born ",
        "lahir ",
        "joined ",
        "bergabung ",
        "followed by ",
        "diikuti oleh ",
    )

    fun isFacebookSuggestionText(value: String): Boolean {
        val normalized = value.trim().lowercase(Locale.ROOT)
        return facebookSuggestionFragments.any(normalized::contains)
    }

    fun isXProfileMetadataLine(value: String): Boolean {
        val normalized = value.trim().lowercase(Locale.ROOT)
        return xProfileMetadataPrefixes.any(normalized::startsWith)
    }
}
