package com.siksik.agent.automation

/**
 * Universal profile-wall detection from accessibility labels (device/account agnostic).
 * Mirrors the proof layers that worked in commit 48a4548 while allowing early entry
 * when the display name is visible before "Edit profile" scrolls into view.
 */
internal object FacebookProfileProofPolicy {
    fun hasEditProfileLabel(
        labels: Iterable<String>,
        editProfileLabels: Iterable<String>,
    ): Boolean = labels.any { label ->
        editProfileLabels.any { it.equals(label.trim(), ignoreCase = true) }
    }

    /** 48a4548-style proof: Edit profile + friends/metric line or profile-only chrome. */
    fun hasClassicProfileProof(
        labels: Iterable<String>,
        editProfileLabels: Iterable<String>,
        hasMetricSignal: Boolean,
        hasProfileChrome: Boolean,
        hasMediaTabs: Boolean,
    ): Boolean {
        if (!hasEditProfileLabel(labels, editProfileLabels)) return false
        return hasMetricSignal || hasProfileChrome || hasMediaTabs
    }

    /** Early wall proof: runtime marker + profile chrome without Edit profile on-screen. */
    fun hasEarlyProfileWallProof(
        labels: Iterable<String>,
        marker: String,
        hasProfileChrome: Boolean,
        hasMediaTabs: Boolean,
        hasMetricLine: Boolean,
    ): Boolean {
        val normalizedMarker = marker.trim()
        if (normalizedMarker.isEmpty()) return false
        if (!labels.any { it.equals(normalizedMarker, ignoreCase = true) }) return false
        return hasProfileChrome || hasMediaTabs || hasMetricLine
    }

    fun captureHasFriendsMetric(vararg textSources: Iterable<String>): Boolean =
        textSources.flatMap { source -> source.toList() }.any { blob ->
            FacebookProfileMetricParser.parse(blob).any { token ->
                token.kind == FacebookProfileMetricKind.FRIENDS
            }
        }

    fun captureHasDisplayName(
        texts: Iterable<String>,
        marker: String,
        hasProfileDisplayNameViewId: Boolean = false,
    ): Boolean {
        val normalizedMarker = marker.trim()
        if (normalizedMarker.isEmpty()) return false
        if (hasProfileDisplayNameViewId) return true
        return texts.any { it.equals(normalizedMarker, ignoreCase = true) }
    }
}
