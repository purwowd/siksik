package com.siksik.agent.automation

import java.util.Locale

/**
 * Detects the Facebook hamburger menu drawer (not the own-profile timeline).
 * Menu open + bottom tabs is a common false-positive for profile proof on Samsung/MIUI.
 */
internal object FacebookMenuSurfacePolicy {
    val menuDrawerLabels = listOf(
        "Settings & privacy",
        "Pengaturan & privasi",
        "Settings and privacy",
        "Pengaturan dan privasi",
        "Help & support",
        "Bantuan & dukungan",
        "All shortcuts",
        "Semua pintasan",
    )

    val menuShortcutLabels = listOf(
        "Upgrade",
        "Facebook Plus",
        "Eksistensi publik",
        "Public presence",
    )

    fun looksLikeMenuDrawer(labels: Iterable<String>): Boolean {
        val normalized = labels.map { it.trim().lowercase(Locale.ROOT) }.filter { it.isNotEmpty() }
        if (normalized.isEmpty()) return false
        val hasMenuChrome = normalized.any { label ->
            label == "menu" ||
                label.startsWith("menu,") ||
                menuDrawerLabels.any { needle ->
                    label.contains(needle.lowercase(Locale.ROOT))
                }
        }
        val hasShortcutChrome = normalized.any { label ->
            menuShortcutLabels.any { needle ->
                label.contains(needle.lowercase(Locale.ROOT))
            }
        }
        val hasBottomTab = normalized.any { label ->
            label.contains("tab ") && label.contains(" dari ") ||
                label.contains("tab ") && label.contains(" of ")
        }
        return hasMenuChrome && (hasShortcutChrome || hasBottomTab)
    }
}
