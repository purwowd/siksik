package com.siksik.agent.source.communication

import com.siksik.agent.BuildConfig
import java.security.MessageDigest
import java.util.Locale

object CommunicationPolicy {
    val supportedSocialTargets = linkedSetOf(
        "com.twitter.android",
        "com.facebook.katana",
        "com.instagram.android",
    )

    private val socialScopesByPackage = linkedMapOf(
        "com.instagram.android" to setOf(
            "own_profile",
            "own_posts",
            "own_story_archive",
            "own_comments",
        ),
        "com.twitter.android" to setOf(
            "own_profile",
            "own_tweets",
            "own_replies",
        ),
        "com.facebook.katana" to setOf(
            "own_profile",
            "own_posts",
            "own_comments",
            // Archive kept allowlisted for future TEXT_ONLY story work; not in default strategy.
            "own_story_archive",
        ),
    )

    fun supportsSocialScope(packageName: String, socialScope: String): Boolean =
        socialScope in socialScopesByPackage[packageName].orEmpty()

    fun validateTargets(values: Collection<String>): Set<String> {
        require(values.size <= supportedSocialTargets.size) { "target package limit exceeded" }
        val normalized = values.map(String::trim).filter(String::isNotEmpty).toSet()
        require(normalized.all(supportedSocialTargets::contains)) { "target package is not allowed" }
        return normalized
    }

    fun boundedText(value: CharSequence?, limit: Int): String? {
        require(limit > 0)
        val normalized = value?.toString()
            ?.replace(Regex("[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F]"), " ")
            ?.trim()
            ?.takeIf(String::isNotEmpty)
            ?: return null
        return normalized.take(limit)
    }

    fun normalizedAddress(value: String?): String? = boundedText(value, 512)
        ?.lowercase(Locale.ROOT)
        ?.replace(Regex("[\\s()-]"), "")
        ?.takeIf(String::isNotEmpty)

    fun normalizedPhone(value: String?): String? = boundedText(value, 512)
        ?.replace(Regex("[^+0-9]"), "")
        ?.takeIf(String::isNotEmpty)

    fun normalizedEmail(value: String?): String? = boundedText(value, 1024)
        ?.lowercase(Locale.ROOT)

    fun recordId(namespace: String, identity: String): String =
        "record_${namespace}_${sha256(identity).take(40)}"

    fun scopedRecordId(namespace: String, scopeId: String, identity: String): String =
        recordId(namespace, "$scopeId\u001f$identity")

    fun sourceLocator(namespace: String, identity: String): String =
        "$namespace:${sha256(identity).take(48)}"

    fun identityHash(namespace: String, identity: String): String = sha256("$namespace:$identity")

    fun contentHash(vararg values: String?): String = sha256(
        values.joinToString("\u001f") { it.orEmpty() },
    )

    fun visibleUiContentHash(
        packageName: String,
        socialScope: String,
        nodes: List<VisibleNodeRecord>,
    ): String = contentHash(
        packageName,
        socialScope,
        nodes.joinToString("\u001e") { node ->
            listOf(
                node.depth,
                node.text,
                node.contentDescription,
                node.className,
                node.viewId,
                node.left,
                node.top,
                node.right,
                node.bottom,
                node.clickable,
                node.scrollable,
            ).joinToString("\u001f")
        },
    )

    fun joinedText(values: Iterable<String?>, limit: Int): String? = boundedText(
        values.filterNotNull().filter(String::isNotBlank).joinToString("\n"),
        limit,
    )

    fun profileLinks(nodes: List<VisibleNodeRecord>): List<String> = profileLinksFromText(
        nodes.asSequence()
            .flatMap { node -> sequenceOf(node.text, node.contentDescription) }
            .filterNotNull()
            .joinToString("\n"),
    )

    fun profileLinksFromText(value: String?): List<String> = value.orEmpty()
        .lineSequence()
        .flatMap { value -> PROFILE_LINK.findAll(value).map(MatchResult::value) }
        .map { value -> value.trim().trimEnd('.', ',', ';', ')', ']', '}') }
        .filter { value -> value.length in 4..MAX_PROFILE_LINK_LENGTH }
        .distinctBy { value -> value.lowercase(Locale.ROOT) }
        .take(MAX_PROFILE_LINKS)
        .toList()

    fun smsText(value: CharSequence?): String? = boundedText(value, BuildConfig.MAX_SMS_TEXT_LENGTH)

    fun contactText(values: Iterable<String?>): String? =
        joinedText(values, BuildConfig.MAX_CONTACT_TEXT_LENGTH)

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    private fun viewIdLeaf(viewId: String?): String? {
        val raw = viewId?.substringAfterLast('/')?.substringAfterLast(':') ?: return null
        return raw.takeIf(String::isNotEmpty)
    }

    private const val MAX_PROFILE_LINKS = 16
    private const val MAX_PROFILE_LINK_LENGTH = 2048
    private val PROFILE_LINK = Regex(
        "(?i)(?:https?://|www\\.)[^\\s<>{}\\[\\]\\\"']+|" +
            "(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\\.)" +
            "(?:com|net|org|id|co|me|io|app|link|bio)(?:/[^\\s<>{}\\[\\]\\\"']*)?",
    )
}
