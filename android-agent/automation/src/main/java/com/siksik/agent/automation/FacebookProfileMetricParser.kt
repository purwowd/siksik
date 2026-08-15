package com.siksik.agent.automation

internal enum class FacebookProfileMetricKind {
    FRIENDS,
    FOLLOWING,
    POSTS,
}

internal data class FacebookProfileMetricToken(
    val value: String,
    val kind: FacebookProfileMetricKind,
)

internal object FacebookProfileMetricParser {
    private val tokenPattern = Regex(
        """(?i)(?<![A-Za-z0-9])([0-9][0-9.,]*\s*(?:k|m|b|rb|jt)?)\s*""" +
            """(friends?|teman|following|mengikuti|posts?|postingan|kiriman)\b""",
    )
    private val separatorPattern = Regex("""[\s·•|,;/]+""")

    fun parse(value: String): List<FacebookProfileMetricToken> = tokenPattern
        .findAll(value)
        .map { match ->
            val label = match.groupValues[2].lowercase()
            val kind = when {
                label.startsWith("friend") || label == "teman" ->
                    FacebookProfileMetricKind.FRIENDS
                label == "following" || label == "mengikuti" ->
                    FacebookProfileMetricKind.FOLLOWING
                else -> FacebookProfileMetricKind.POSTS
            }
            FacebookProfileMetricToken(match.value.trim(), kind)
        }
        .distinctBy(FacebookProfileMetricToken::kind)
        .toList()

    fun isMetricLine(value: String): Boolean {
        if (parse(value).isEmpty()) return false
        return tokenPattern.replace(value, "").replace(separatorPattern, "").isEmpty()
    }
}
