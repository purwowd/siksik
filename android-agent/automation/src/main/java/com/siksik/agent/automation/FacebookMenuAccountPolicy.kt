package com.siksik.agent.automation

import java.util.Locale

/**
 * Infers the signed-in account display name from the Facebook hamburger menu header
 * and selects the correct accessibility node to tap (text row, not avatar).
 *
 * Marker inference is intentionally strict: only compound profile-row labels such as
 * "{display name}, lihat profil" are accepted. Generic menu shortcuts (Kabar Beranda,
 * Meta AI, etc.) must never be guessed via heuristic scoring.
 */
internal object FacebookMenuAccountPolicy {
    data class LabeledBounds(
        val labels: List<String>,
        val className: String,
        val left: Int,
        val top: Int,
        val width: Int,
        val height: Int,
    ) {
        val area: Int get() = (width.coerceAtLeast(0)) * (height.coerceAtLeast(0))
        val aspectRatio: Double get() = width.toDouble() / height.coerceAtLeast(1)
    }

    private val profileRowPhrases = listOf(
        "lihat profil",
        "see your profile",
        "view your profile",
        "voir votre profil",
        "ver tu perfil",
    )

    private val menuChromeNoise = listOf(
        "menu",
        "cari",
        "search",
        "pengaturan",
        "settings",
        "privasi",
        "privacy",
        "bantuan",
        "help",
        "support",
        "upgrade",
        "facebook plus",
        "pintasan",
        "shortcut",
        "eksistensi",
        "public presence",
        "buat halaman",
        "create page",
        "pengiriman pesan",
        "messenger",
        "notifikasi",
        "notification",
        "teman",
        "friends",
        "beranda",
        "home",
        "reels",
        "profil",
        "profile",
        "tab ",
        " dari ",
        " of ",
        "judul",
        "title",
        "tombol",
        "button",
        "ketuk untuk",
        "tap to",
        "double tap",
        "ketuk dua",
        "kabar",
        "meta ai",
        "tersimpan",
        "saved",
        "marketplace",
        "kenangan",
        "memories",
    )

    private val storyShortcutPhrases = listOf(
        "baki cerita",
        "buat cerita",
        "cerita baru",
        "create story",
        "add story",
        "your story",
        "new story",
        "story archive",
        "arsip cerita",
        "cerita anda",
        "your facebook activity",
        "aktivitas facebook",
        "meta ai",
        "tersimpan",
        "saved",
        "kabar beranda",
        "news feed",
    )

    private val menuNameRejectPhrases = listOf(
        "foto profil",
        "profile photo",
        "pengalih profil",
        "profile switcher",
        "lihat profil",
        "see your profile",
        "view your profile",
    )

    /** Username text rows are wide; story/create shortcuts are nearly square buttons. */
    private const val MIN_TEXT_ASPECT_RATIO = 2.0

    /**
     * Only accepts compound profile-row labels. Never guesses from generic menu text.
     */
    fun inferAccountName(labels: Iterable<String>): String? =
        labels.asSequence()
            .mapNotNull(::extractNameFromProfileRowLabel)
            .distinctBy { it.lowercase(Locale.ROOT) }
            .firstOrNull()

    fun inferAccountNameFromCandidates(candidates: Iterable<LabeledBounds>): String? =
        candidates.asSequence()
            .flatMap { candidate -> candidate.labels.asSequence() }
            .mapNotNull(::extractNameFromProfileRowLabel)
            .distinctBy { it.lowercase(Locale.ROOT) }
            .firstOrNull()

    /** Drawer is truly open when the profile-row compound label is visible (same as manual XML). */
    fun hasMenuProfileRow(candidates: Iterable<LabeledBounds>): Boolean =
        inferAccountNameFromCandidates(candidates) != null

    fun resolveMenuProfileUsernameTap(
        candidates: Iterable<LabeledBounds>,
    ): Pair<String, LabeledBounds>? {
        val marker = inferAccountNameFromCandidates(candidates) ?: return null
        val target = selectMenuNameTapTarget(candidates, marker) ?: return null
        return marker to target
    }

    fun extractNameFromProfileRowLabel(raw: String): String? {
        val trimmed = raw.trim()
        if (trimmed.isEmpty() || !trimmed.contains(',')) return null
        val lower = trimmed.lowercase(Locale.ROOT)
        if (!profileRowPhrases.any { phrase -> lower.contains(phrase) }) return null
        val name = trimmed.substringBefore(',').trim()
        if (name.length !in 2..48) return null
        if (isStoryOrShortcutChrome(name)) return null
        if (isMenuDrawerListItem(trimmed)) return null
        return name
    }

    fun isStoryOrShortcutChrome(label: String): Boolean {
        val lower = label.trim().lowercase(Locale.ROOT)
        if (lower.isEmpty()) return true
        if (storyShortcutPhrases.any { phrase -> lower == phrase || lower.startsWith("$phrase ") }) {
            return true
        }
        if (lower.contains("cerita") && lower.split(Regex("\\s+")).size <= 3) return true
        if (lower.contains("story") && lower.split(Regex("\\s+")).size <= 3) return true
        return false
    }

    fun isMenuDrawerListItem(label: String): Boolean {
        val lower = label.trim().lowercase(Locale.ROOT)
        if (lower.contains("tombol") && lower.contains(" dari ")) return true
        if (lower.contains("button") && lower.contains(" of ")) return true
        return false
    }

    fun selectMenuNameTapTarget(
        candidates: Iterable<LabeledBounds>,
        marker: String,
    ): LabeledBounds? = selectMenuNameTapTargets(candidates, marker, limit = 1).firstOrNull()

    /**
     * Ranked username text rows for the runtime marker. On mis-tap the driver tries the next
     * candidate before closing and reopening the drawer — no device-specific coordinates.
     */
    fun selectMenuNameTapTargets(
        candidates: Iterable<LabeledBounds>,
        marker: String,
        limit: Int = 3,
    ): List<LabeledBounds> {
        val normalizedMarker = marker.trim()
        if (normalizedMarker.isEmpty() || limit <= 0) return emptyList()
        return candidates.asSequence()
            .filter { candidate -> hasExactMarkerLabel(candidate.labels, normalizedMarker) }
            .filter { candidate -> !isMenuNameTapRejected(candidate) }
            .sortedWith(
                compareByDescending<LabeledBounds> { it.aspectRatio }
                    .thenBy { it.area },
            )
            .take(limit)
            .toList()
    }

    /**
     * Tap the text-heavy zone of a ranked username row. Avatar sits left of the header row;
     * center-of-bounds can still land on the switcher on some layouts.
     */
    fun preferredMenuNameTapPoint(candidate: LabeledBounds): Pair<Int, Int> {
        val tapX = candidate.left + (candidate.width * 2 / 3)
        val tapY = candidate.top + candidate.height / 2
        return tapX to tapY
    }

    fun selectTopmostMenuButton(
        candidates: Iterable<LabeledBounds>,
        maxLeft: Int? = null,
    ): LabeledBounds? =
        candidates.asSequence()
            .filter(::looksLikeMenuButton)
            .filter { candidate -> !isCloseMenuChrome(candidate) }
            .filter { candidate -> maxLeft == null || candidate.left < maxLeft }
            .sortedWith(compareBy({ it.top }, { it.left }))
            .firstOrNull()

    /** Drawer open state exposes a close-menu control on the right — not the hamburger. */
    fun isCloseMenuChrome(candidate: LabeledBounds): Boolean =
        candidate.labels.any { label ->
            val normalized = label.trim().lowercase(Locale.ROOT)
            normalized == "tutup menu" ||
                normalized == "close menu" ||
                normalized.startsWith("tutup menu,") ||
                normalized.startsWith("close menu,")
        }

    fun hasExactMarkerLabel(labels: Iterable<String>, marker: String): Boolean =
        labels.any { label -> label.equals(marker.trim(), ignoreCase = true) }

    fun isMenuNameTapRejected(candidate: LabeledBounds): Boolean {
        if (candidate.className.contains("ImageView", ignoreCase = true)) return true
        if (candidate.aspectRatio < MIN_TEXT_ASPECT_RATIO) return true
        if (candidate.top > 600) return true
        if (candidate.labels.any { isStoryOrShortcutChrome(it) }) return true
        if (candidate.labels.any { isMenuDrawerListItem(it) }) return true
        val joined = candidate.labels.joinToString(" ").lowercase(Locale.ROOT)
        if (joined.contains("postingan") || joined.contains("shared") || joined.contains("dibagikan")) return true
        if (menuNameRejectPhrases.any { phrase -> joined.contains(phrase) }) return true
        if (
            candidate.labels.any { label ->
                label.contains(',') &&
                    menuNameRejectPhrases.any { phrase ->
                        label.lowercase(Locale.ROOT).contains(phrase)
                    }
            }
        ) {
            return true
        }
        return false
    }

    fun looksLikeMenuButton(candidate: LabeledBounds): Boolean {
        val cls = candidate.className
        val clickableClass = cls.contains("Button", ignoreCase = true) ||
            cls.contains("ImageButton", ignoreCase = true)
        if (!clickableClass) return false
        return candidate.labels.any { label ->
            val normalized = label.trim().lowercase(Locale.ROOT)
            normalized == "menu" ||
                normalized.startsWith("menu,") ||
                normalized.startsWith("menu ")
        } && candidate.labels.none { label ->
            val normalized = label.lowercase(Locale.ROOT)
            normalized.contains("tab ") &&
                (normalized.contains(" dari ") || normalized.contains(" of "))
        }
    }

    fun parseShellDumpCandidates(
        xml: String,
        packageFilter: String? = null,
    ): List<LabeledBounds> {
        if (!xml.contains("<node")) return emptyList()
        val nodeRe = Regex("""<node\b[^>]*>""")
        val boundsRe = Regex("""bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"""")
        val results = ArrayList<LabeledBounds>()
        for (match in nodeRe.findAll(xml)) {
            val tag = match.value
            if (packageFilter != null) {
                val pkg = shellDumpAttr(tag, "package").trim()
                if (pkg.isNotEmpty() && pkg != packageFilter) continue
            }
            val labels = listOfNotNull(
                shellDumpAttr(tag, "text").trim().takeIf { it.isNotEmpty() },
                shellDumpAttr(tag, "content-desc").trim().takeIf { it.isNotEmpty() },
            ).distinct()
            if (labels.isEmpty()) continue
            val bounds = boundsRe.find(tag) ?: continue
            val left = bounds.groupValues[1].toInt()
            val top = bounds.groupValues[2].toInt()
            val right = bounds.groupValues[3].toInt()
            val bottom = bounds.groupValues[4].toInt()
            val width = right - left
            val height = bottom - top
            if (width <= 0 || height <= 0) continue
            results.add(
                LabeledBounds(
                    labels = labels,
                    className = shellDumpAttr(tag, "class"),
                    left = left,
                    top = top,
                    width = width,
                    height = height,
                ),
            )
        }
        return results
    }

    private fun shellDumpAttr(tag: String, name: String): String {
        val pattern = Regex("""$name="([^"]*)"""")
        return pattern.find(tag)?.groupValues?.getOrNull(1).orEmpty()
    }
}
