package com.siksik.agent.automation

import java.util.Locale

/**
 * Labels for own-profile timeline posts per [new-navigation.md]:
 * scroll until "all post" / "semua postingan" is visible, then collect posts.
 */
internal object FacebookPostsSurfacePolicy {
    val allPostsSurfaceLabels = listOf(
        "All posts",
        "All post",
        "Semua postingan",
    )

    val allPostsFilterChipLabels = listOf(
        "All",
        "Semua",
    )

    val allPostsFilterDescriptions = listOf(
        "All, 1 of",
        "Semua, 1 dari",
        "All posts",
        "Semua postingan",
    )

    /** Profile wall sub-tabs below the header (not the home bottom nav). */
    val profilePostsTabLabels = listOf(
        "Posts",
        "Postingan",
        "Kiriman",
    )

    val profilePhotosTabLabels = listOf(
        "Photos",
        "Foto",
    )

    val emptyPostsLabels = listOf(
        "No posts yet",
        "Belum ada postingan",
        "You haven't posted anything",
        "Anda belum memposting",
        "When you post",
        "Saat Anda memposting",
        "You haven't shared anything",
        "Anda belum membagikan",
    )

    fun looksLikeAllPostsSurface(labels: Iterable<String>): Boolean =
        labels.any(::looksLikeAllPostsSurfaceLabel)

    fun looksLikeAllPostsSurfaceLabel(label: String): Boolean {
        val normalized = label.trim().lowercase(Locale.ROOT)
        if (normalized.isEmpty()) return false
        return allPostsSurfaceLabels.any { candidate ->
            val needle = candidate.lowercase(Locale.ROOT)
            normalized == needle || normalized.contains(needle)
        } || allPostsFilterChipLabels.any { candidate ->
            val needle = candidate.lowercase(Locale.ROOT)
            normalized == needle ||
                normalized.startsWith("$needle,") ||
                normalized.startsWith("$needle ")
        }
    }

    fun looksLikeEmptyPosts(labels: Iterable<String>): Boolean =
        labels.any { label ->
            val normalized = label.trim().lowercase(Locale.ROOT)
            if (normalized.isEmpty()) return@any false
            emptyPostsLabels.any { candidate ->
                normalized.contains(candidate.lowercase(Locale.ROOT))
            }
        }

    fun looksLikeProfileMediaTabs(labels: Iterable<String>): Boolean {
        val normalized = labels.map { it.trim().lowercase(Locale.ROOT) }.filter { it.isNotEmpty() }
        if (normalized.isEmpty()) return false
        val hasPostsTab = profilePostsTabLabels.any { tab ->
            val needle = tab.lowercase(Locale.ROOT)
            normalized.any { label ->
                label == needle || label.startsWith("$needle,") || label.startsWith("$needle ")
            }
        }
        val hasPhotosTab = profilePhotosTabLabels.any { tab ->
            val needle = tab.lowercase(Locale.ROOT)
            normalized.any { label ->
                label == needle || label.startsWith("$needle,") || label.startsWith("$needle ")
            }
        }
        return hasPostsTab && hasPhotosTab
    }

    /** Photos sub-tab selected e.g. "Foto, 2 dari 3" while Posts tab exists. */
    fun looksLikeProfilePhotosSubtabActive(labels: Iterable<String>): Boolean {
        if (!looksLikeProfileMediaTabs(labels)) return false
        return labels.any { label ->
            val normalized = label.trim().lowercase(Locale.ROOT)
            profilePhotosTabLabels.any { tab ->
                val needle = tab.lowercase(Locale.ROOT)
                normalized.startsWith("$needle,") &&
                    (normalized.contains(" dari ") || normalized.contains(" of "))
            }
        }
    }
}
