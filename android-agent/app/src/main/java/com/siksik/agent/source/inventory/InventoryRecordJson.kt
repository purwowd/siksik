package com.siksik.agent.source.inventory

import com.siksik.agent.BuildConfig
import com.siksik.agent.source.communication.ContactIdentity
import com.siksik.agent.source.communication.ContactOrganization
import com.siksik.agent.source.communication.CommunicationPolicy
import com.siksik.agent.source.communication.VisibleNodeRecord
import java.time.Instant
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

object InventoryRecordJson {
    fun encode(
        sessionId: String,
        crawlId: String,
        record: InventoryRecord,
    ): JSONObject {
        val value = JSONObject()
            .put("schema_version", 1)
            .put("record_id", record.recordId)
            .put("crawl_id", crawlId)
            .put("siksik_session_id", sessionId)
            .put("source_kind", record.sourceKind.wireName)
            .put("source_app", record.sourceApp ?: JSONObject.NULL)
            .put("source_locator", record.sourceLocator)
            .put("observed_at", timestamp(record.observedAtEpochMs))
            .put("source_created_at", nullableTimestamp(record.captureTimeEpochMs))
            .put("source_modified_at", nullableTimestamp(record.dateModifiedEpochMs))
            .put("normalized_text", record.normalizedText ?: JSONObject.NULL)
            .put("metadata", metadata(record))
            .put("attachment_ids", JSONArray(record.attachmentIds))
            .put("content_sha256", record.contentSha256 ?: JSONObject.NULL)
            .put("preprocessing", JSONObject.NULL)
            .put("selection", JSONObject.NULL)
            .put(
                "provenance",
                JSONObject()
                    .put("source_adapter", record.sourceAdapter.wireName)
                    .put("enumeration_method", enumerationMethod(record))
                    .put("agent_version", BuildConfig.AGENT_VERSION)
                    .put("original_staged", false),
            )
        // Ensure own_profile metrics/username/bio/links are present on every encode path
        // (not only withPreprocessing), so inventory + transfer stay aligned with reports.
        enrichSocialProfile(value, record.normalizedText)
        return value
    }

    fun withPreprocessing(
        baseRecord: String,
        normalizedText: String?,
        contentSha256: String?,
        preprocessing: String,
    ): JSONObject {
        val value = JSONObject(baseRecord)
            .put("normalized_text", normalizedText ?: JSONObject.NULL)
            .put("content_sha256", contentSha256 ?: JSONObject.NULL)
            .put("preprocessing", JSONObject(preprocessing))
        enrichSocialProfile(value, normalizedText)
        return value
    }

    private fun metadata(record: InventoryRecord): JSONObject = when {
        record.smsMetadata != null -> smsMetadata(record)
        record.contactMetadata != null -> contactMetadata(record)
        record.visibleUiMetadata != null -> visibleUiMetadata(record)
        record.notificationMetadata != null -> notificationMetadata(record)
        else -> mediaMetadata(record)
    }

    private fun mediaMetadata(record: InventoryRecord): JSONObject = JSONObject()
        .put("display_name", record.displayName)
        .put("mime_type", record.mimeType)
        .put("size_bytes", record.sizeBytes ?: JSONObject.NULL)
        .put("width", record.width ?: JSONObject.NULL)
        .put("height", record.height ?: JSONObject.NULL)
        .put("duration_ms", record.durationMs ?: JSONObject.NULL)
        .put("date_taken", nullableTimestamp(record.dateTakenEpochMs))
        .put("date_added", nullableTimestamp(record.dateAddedEpochMs))
        .put("date_modified", nullableTimestamp(record.dateModifiedEpochMs))
        .put("capture_time", nullableTimestamp(record.captureTimeEpochMs))
        .put("capture_time_source", record.captureTimeSource)
        .put("directory_hint", record.directoryHint ?: JSONObject.NULL)
        .put("is_favorite", record.isFavorite)
        .put("exif", record.exif?.let(::exif) ?: JSONObject.NULL)
        .put("warning_codes", JSONArray(record.warningCodes))
        .put("thumbnail_available", record.thumbnailAvailable)

    private fun smsMetadata(record: InventoryRecord): JSONObject {
        val value = requireNotNull(record.smsMetadata)
        return JSONObject()
            .put("direction", value.direction)
            .put("address", value.address ?: JSONObject.NULL)
            .put("address_identity", value.addressIdentity ?: JSONObject.NULL)
            .put("thread_identity", value.threadIdentity ?: JSONObject.NULL)
            .put("message_type", value.messageType)
            .put("status", value.status ?: JSONObject.NULL)
            .put("subscription_id", value.subscriptionId ?: JSONObject.NULL)
            .put("is_read", value.isRead ?: JSONObject.NULL)
            .put("is_seen", value.isSeen ?: JSONObject.NULL)
            .put("sent_at", nullableTimestamp(value.sentAtEpochMs))
            .put("warning_codes", JSONArray(record.warningCodes))
    }

    private fun contactMetadata(record: InventoryRecord): JSONObject {
        val value = requireNotNull(record.contactMetadata)
        return JSONObject()
            .put("display_name", value.displayName ?: JSONObject.NULL)
            .put("lookup_identity", value.lookupIdentity)
            .put("phones", JSONArray().apply {
                value.phones.forEach { put(contactIdentity(it)) }
            })
            .put("emails", JSONArray().apply {
                value.emails.forEach { put(contactIdentity(it)) }
            })
            .put("organizations", JSONArray().apply {
                value.organizations.forEach { put(contactOrganization(it)) }
            })
            .put("updated_at", nullableTimestamp(value.updatedAtEpochMs))
            .put("warning_codes", JSONArray(record.warningCodes))
    }

    private fun visibleUiMetadata(record: InventoryRecord): JSONObject {
        val value = requireNotNull(record.visibleUiMetadata)
        return JSONObject()
            .put("package_name", value.packageName)
            .put("social_scope", value.socialScope)
            .put("window_id", value.windowId)
            .put("activity_context", value.activityContext ?: JSONObject.NULL)
            .put("event_type", value.eventType)
            .put("screen_sequence", value.screenSequence)
            .put("nodes", JSONArray().apply {
                value.nodes.forEach { put(visibleNode(it)) }
            })
            .put("screenshot_ids", JSONArray(value.screenshotIds))
            .put("profile_links", JSONArray(value.profileLinks))
            .put("warning_codes", JSONArray(record.warningCodes))
    }

    private fun notificationMetadata(record: InventoryRecord): JSONObject {
        val value = requireNotNull(record.notificationMetadata)
        return JSONObject()
            .put("package_name", value.packageName)
            .put("notification_identity", value.notificationIdentity)
            .put("title", value.title ?: JSONObject.NULL)
            .put("text", value.text ?: JSONObject.NULL)
            .put("sub_text", value.subText ?: JSONObject.NULL)
            .put("big_text", value.bigText ?: JSONObject.NULL)
            .put("text_lines", JSONArray(value.textLines))
            .put("category", value.category ?: JSONObject.NULL)
            .put("channel_id", value.channelId ?: JSONObject.NULL)
            .put("post_time", timestamp(value.postTimeEpochMs))
            .put("removed_at", nullableTimestamp(value.removedAtEpochMs))
            .put("update_count", value.updateCount)
            .put("warning_codes", JSONArray(record.warningCodes))
    }

    private fun contactIdentity(value: ContactIdentity): JSONObject = JSONObject()
        .put("value", value.value)
        .put("normalized_value", value.normalizedValue ?: JSONObject.NULL)
        .put("label", value.label ?: JSONObject.NULL)

    private fun contactOrganization(value: ContactOrganization): JSONObject = JSONObject()
        .put("company", value.company ?: JSONObject.NULL)
        .put("title", value.title ?: JSONObject.NULL)
        .put("department", value.department ?: JSONObject.NULL)

    private fun visibleNode(value: VisibleNodeRecord): JSONObject = JSONObject()
        .put("sequence", value.sequence)
        .put("depth", value.depth)
        .put("text", value.text ?: JSONObject.NULL)
        .put("content_description", value.contentDescription ?: JSONObject.NULL)
        .put("class_name", value.className ?: JSONObject.NULL)
        .put("view_id", value.viewId ?: JSONObject.NULL)
        .put(
            "bounds",
            JSONObject()
                .put("left", value.left)
                .put("top", value.top)
                .put("right", value.right)
                .put("bottom", value.bottom),
        )
        .put("clickable", value.clickable)
        .put("scrollable", value.scrollable)

    private fun enrichSocialProfile(record: JSONObject, normalizedText: String?) {
        if (record.optString("source_kind") != "visible_ui") return
        val metadata = record.optJSONObject("metadata") ?: return
        if (metadata.optString("social_scope") != "own_profile") return
        val text = normalizedText.orEmpty()
        val nodes = metadata.optJSONArray("nodes")
        val links = buildList {
            val existing = metadata.optJSONArray("profile_links")
            if (existing != null) {
                for (index in 0 until existing.length()) add(existing.optString(index))
            }
            addAll(CommunicationPolicy.profileLinksFromText(text))
        }.filter(String::isNotBlank).distinctBy { it.lowercase(Locale.ROOT) }.take(16)
        metadata.put("profile_links", JSONArray(links))
        val packageName = metadata.optString("package_name")
        val username = profileUsernameFromNodes(nodes, packageName)
            ?: profileUsername(packageName, text)
        val displayName = profileDisplayName(nodes, username)
        metadata.put("profile_username", username ?: JSONObject.NULL)
        metadata.put(
            "profile_display_name",
            displayName ?: JSONObject.NULL,
        )
        metadata.put(
            "profile_bio",
            profileBioFromNodes(nodes, username, displayName, links)
                ?: profileBio(text, username, displayName, links)
                ?: JSONObject.NULL,
        )
        metadata.put("profile_metrics", profileMetrics(nodes, text))
    }

    private fun profileUsernameFromNodes(nodes: JSONArray?, packageName: String): String? {
        if (nodes == null || nodes.length() == 0) return null
        // Exact resource leaf only — "user_name" must not match "user_name_container"
        // (JSON null → org.json optString → literal "null" → @null in reports).
        val usernameResources = listOf(
            "action_bar_title",
            "profile_header_username",
            "profile_header_user_name",
            "action_bar_large_title_auto_size",
            "screen_name",
            "username",
            "user_name",
        )
        usernameResources.forEach { resource ->
            for (index in 0 until nodes.length()) {
                val node = nodes.optJSONObject(index) ?: continue
                val viewId = node.optString("view_id").lowercase(Locale.ROOT)
                if (viewId.contains("notification")) continue
                if (viewIdResourceLeaf(viewId) != resource) continue
                sequenceOf(nodeFieldText(node, "text"), nodeFieldText(node, "content_description"))
                    .filterNotNull()
                    .mapNotNull(::validAccountMarker)
                    .firstOrNull()
                    ?.let { return it }
            }
        }
        var maxBottom = 0
        for (index in 0 until nodes.length()) {
            val bounds = nodes.optJSONObject(index)?.optJSONObject("bounds") ?: continue
            maxBottom = maxOf(maxBottom, bounds.optInt("bottom", 0))
        }
        val profileCutoff = if (maxBottom > 0) (maxBottom * 2) / 5 else Int.MAX_VALUE
        val atMarkers = buildList {
            for (index in 0 until nodes.length()) {
                val node = nodes.optJSONObject(index) ?: continue
                val top = node.optJSONObject("bounds")?.optInt("top", Int.MAX_VALUE) ?: Int.MAX_VALUE
                if (top > profileCutoff) continue
                val viewId = node.optString("view_id").lowercase(Locale.ROOT)
                if (viewId.contains("notification")) continue
                val text = nodeFieldText(node, "text") ?: continue
                if (!text.startsWith("@")) continue
                validAccountMarker(text)?.let { add(it) }
            }
        }
        if (atMarkers.isNotEmpty()) return atMarkers.maxByOrNull { it.length }
        if (packageName != INSTAGRAM_PACKAGE) return null
        for (index in 0 until nodes.length()) {
            val node = nodes.optJSONObject(index) ?: continue
            val text = nodeFieldText(node, "text")?.lowercase(Locale.ROOT) ?: continue
            if (text !in FOLLOWER_LABELS) continue
            for (scan in (index - 1) downTo 0) {
                val candidate = nodeFieldText(nodes.optJSONObject(scan) ?: continue, "text")
                    ?: continue
                validAccountMarker(candidate)?.let { return it }
            }
        }
        return null
    }

    /** org.json optString(null) returns the literal "null" — never treat that as UI text. */
    private fun nodeFieldText(node: JSONObject, key: String): String? {
        if (node.isNull(key)) return null
        val value = node.optString(key, "").trim()
        if (value.isEmpty()) return null
        if (value.equals("null", ignoreCase = true) || value.equals("undefined", ignoreCase = true)) {
            return null
        }
        return value
    }

    private fun viewIdResourceLeaf(viewId: String): String {
        val slash = viewId.substringAfterLast('/')
        return slash.substringAfterLast(':').lowercase(Locale.ROOT)
    }

    private fun profileUsername(packageName: String, value: String): String? {
        val lines = value.lineSequence().map(String::trim).filter(String::isNotEmpty).toList()
        lines.asSequence()
            .mapNotNull { line -> ACCOUNT_REFERENCE.find(line)?.groupValues?.getOrNull(1) }
            .mapNotNull(::validAccountMarker)
            .firstOrNull()
            ?.let { return it }
        if (packageName != INSTAGRAM_PACKAGE) return null
        val postsIndex = lines.indexOfFirst { it.lowercase(Locale.ROOT) in POST_LABELS }
        if (postsIndex > 0) {
            lines.subList((postsIndex - 5).coerceAtLeast(0), postsIndex)
                .asReversed()
                .asSequence()
                .mapNotNull(::validAccountMarker)
                .firstOrNull()
                ?.let { return it }
        }
        return null
    }

    private fun validAccountMarker(value: String): String? {
        val trimmed = value.trim()
        if (trimmed.endsWith("..") || trimmed.endsWith("…")) return null
        val candidate = trimmed.removePrefix("@").trim()
        if (candidate.endsWith(".") || candidate.contains("..")) return null
        if (!ACCOUNT_MARKER.matches(candidate)) return null
        val key = candidate.lowercase(Locale.ROOT)
        val keyStripped = key.trimEnd('_')
        if (key in PROFILE_NOISE || keyStripped in PROFILE_NOISE) return null
        if (key in ACCOUNT_MARKER_SENTINELS) return null
        if (IG_AVATAR_PROMPT_FRAGMENTS.any { fragment -> keyStripped.contains(fragment) }) return null
        if (PROFILE_COUNT.matches(candidate)) return null
        if (NUMERIC_ACCOUNT_MARKER.matches(candidate)) return null
        return candidate
    }

    private fun profileBio(
        value: String,
        username: String?,
        displayName: String?,
        links: List<String>,
    ): String? {
        val linkSet = links.map { it.lowercase(Locale.ROOT) }
        return value.lineSequence()
            .map(String::trim)
            .filter(String::isNotEmpty)
            .distinct()
            .filter { line ->
                val normalized = line.lowercase(Locale.ROOT)
                normalized != username?.lowercase(Locale.ROOT) &&
                    normalized != displayName?.lowercase(Locale.ROOT) &&
                    normalized !in PROFILE_NOISE &&
                    normalized !in POST_LABELS &&
                    !PROFILE_COUNT.matches(normalized) &&
                    !isProfileMetricLine(normalized) &&
                    !isChromeUiLine(normalized) &&
                    linkSet.none { link -> normalized.contains(link) } &&
                    PROFILE_LINK_PREFIX.none { prefix -> normalized.startsWith(prefix) }
            }
            .take(MAX_PROFILE_BIO_LINES)
            .joinToString("\n")
            .take(MAX_PROFILE_BIO_LENGTH)
            .takeIf(String::isNotBlank)
    }

    private fun profileDisplayName(nodes: JSONArray?, username: String?): String? {
        val values = profileNodeValues(nodes)
        return values.asSequence()
            .filter { value ->
                PROFILE_DISPLAY_NAME_RESOURCES.any { resource ->
                    viewIdResourceMatches(value.viewId, resource)
                } &&
                    PROFILE_USERNAME_RESOURCES.none { resource ->
                        viewIdResourceMatches(value.viewId, resource)
                    }
            }
            .map(ProfileNodeValue::text)
            .map(String::trim)
            .filter { value -> profileTextCandidate(value, username) }
            .maxByOrNull(String::length)
    }

    private fun profileBioFromNodes(
        nodes: JSONArray?,
        username: String?,
        displayName: String?,
        links: List<String>,
    ): String? {
        val linkKeys = links.map { value -> value.lowercase(Locale.ROOT) }
        return profileNodeValues(nodes).asSequence()
            .filter { value ->
                PROFILE_BIO_RESOURCES.any { resource ->
                    viewIdResourceMatches(value.viewId, resource)
                }
            }
            .map(ProfileNodeValue::text)
            .map(String::trim)
            .filter { value -> profileTextCandidate(value, username) }
            .filterNot { value -> value.equals(displayName, ignoreCase = true) }
            .filter { value ->
                val normalized = value.lowercase(Locale.ROOT)
                linkKeys.none(normalized::contains) && !isProfileMetricLine(normalized)
            }
            .distinct()
            .joinToString("\n")
            .take(MAX_PROFILE_BIO_LENGTH)
            .takeIf(String::isNotBlank)
    }

    private fun profileMetrics(nodes: JSONArray?, text: String): JSONObject {
        val values = profileNodeValues(nodes)
        val textValues = buildList {
            addAll(values.map(ProfileNodeValue::text))
            addAll(text.lineSequence().map(String::trim).filter(String::isNotEmpty))
        }
        return JSONObject()
            .put(
                "posts",
                profileMetric(
                    values,
                    textValues,
                    POST_METRIC_LABELS,
                    POST_METRIC_RESOURCES,
                ) ?: JSONObject.NULL,
            )
            .put(
                "followers",
                profileMetric(
                    values,
                    textValues,
                    FOLLOWER_METRIC_LABELS,
                    FOLLOWER_METRIC_RESOURCES,
                ) ?: JSONObject.NULL,
            )
            .put(
                "friends",
                profileMetric(
                    values,
                    textValues,
                    FRIEND_METRIC_LABELS,
                    FRIEND_METRIC_RESOURCES,
                ) ?: JSONObject.NULL,
            )
            .put(
                "following",
                profileMetric(
                    values,
                    textValues,
                    FOLLOWING_METRIC_LABELS,
                    FOLLOWING_METRIC_RESOURCES,
                ) ?: JSONObject.NULL,
            )
    }

    private fun profileMetric(
        nodes: List<ProfileNodeValue>,
        values: List<String>,
        labels: Set<String>,
        resourceHints: Set<String>,
    ): Long? {
        nodes.asSequence()
            .filter { value -> resourceHints.any(value.viewId::contains) }
            .mapNotNull { value ->
                parseProfileCount(value.text) ?: inlineProfileMetric(value.text, labels)
            }
            .firstOrNull()
            ?.let { return it }
        values.forEach { value ->
            inlineProfileMetric(value, labels)?.let { return it }
        }
        val labelNodes = nodes.filter { value ->
            val normalized = value.text.trim().lowercase(Locale.ROOT)
            normalized in labels
        }
        val countNodes = nodes.mapNotNull { value ->
            parseProfileCount(value.text)?.let { count -> value to count }
        }
        for (label in labelNodes) {
            countNodes.minByOrNull { (candidate, _) ->
                val horizontal = kotlin.math.abs(candidate.centerX - label.centerX)
                val vertical = kotlin.math.abs(candidate.centerY - label.centerY)
                horizontal + vertical * 2
            }?.takeIf { (candidate, _) ->
                kotlin.math.abs(candidate.centerX - label.centerX) <= PROFILE_METRIC_MAX_DISTANCE &&
                    kotlin.math.abs(candidate.centerY - label.centerY) <= PROFILE_METRIC_MAX_DISTANCE
            }?.second?.let { return it }
        }
        return null
    }

    private fun inlineProfileMetric(value: String, labels: Set<String>): Long? {
        val normalized = value.trim().lowercase(Locale.ROOT)
        labels.forEach { label ->
            val escaped = Regex.escape(label)
            Regex("(?i)(?:^|\\s)([0-9][0-9.,]*\\s*(?:k|m|b|rb|jt)?)\\s*$escaped(?:\\s|$)")
                .find(normalized)
                ?.groupValues
                ?.getOrNull(1)
                ?.let(::parseProfileCount)
                ?.let { return it }
            Regex("(?i)(?:^|\\s)$escaped\\s*([0-9][0-9.,]*\\s*(?:k|m|b|rb|jt)?)\\b")
                .find(normalized)
                ?.groupValues
                ?.getOrNull(1)
                ?.let(::parseProfileCount)
                ?.let { return it }
        }
        return null
    }

    private fun parseProfileCount(value: String): Long? {
        val match = PROFILE_COUNT_VALUE.matchEntire(value.trim().lowercase(Locale.ROOT)) ?: return null
        val rawNumber = match.groupValues[1]
        val suffix = match.groupValues[2]
        if (suffix.isEmpty()) {
            return rawNumber.replace(Regex("[.,\\s]"), "").toLongOrNull()
        }
        val number = rawNumber.replace(" ", "").replace(',', '.').toDoubleOrNull() ?: return null
        val multiplier = when (suffix) {
            "k", "rb" -> 1_000.0
            "m", "jt" -> 1_000_000.0
            "b" -> 1_000_000_000.0
            else -> return null
        }
        return (number * multiplier).toLong().takeIf { it >= 0 }
    }

    private fun profileNodeValues(nodes: JSONArray?): List<ProfileNodeValue> {
        if (nodes == null) return emptyList()
        return buildList {
            for (index in 0 until nodes.length()) {
                val node = nodes.optJSONObject(index) ?: continue
                val bounds = node.optJSONObject("bounds")
                val viewId = node.optString("view_id").lowercase(Locale.ROOT)
                sequenceOf(node.optString("text"), node.optString("content_description"))
                    .map(String::trim)
                    .filter(String::isNotEmpty)
                    .distinct()
                    .forEach { value ->
                        add(
                            ProfileNodeValue(
                                value,
                                viewId,
                                bounds?.optInt("left", 0) ?: 0,
                                bounds?.optInt("top", 0) ?: 0,
                                bounds?.optInt("right", 0) ?: 0,
                                bounds?.optInt("bottom", 0) ?: 0,
                            ),
                        )
                    }
            }
        }
    }

    private fun profileTextCandidate(value: String, username: String?): Boolean {
        val normalized = value.lowercase(Locale.ROOT)
        return value.length in 1..256 &&
            normalized != username?.lowercase(Locale.ROOT) &&
            normalized != "@${username?.lowercase(Locale.ROOT)}" &&
            normalized !in PROFILE_NOISE &&
            !PROFILE_COUNT.matches(normalized) &&
            !isProfileMetricLine(normalized) &&
            !isChromeUiLine(normalized)
    }

    private fun isChromeUiLine(value: String): Boolean {
        if (value.contains("tab ") && value.contains(" of ")) return true
        if (value.startsWith("create, double tap")) return true
        if (value.contains("story tray")) return true
        if (value.contains("on your mind") || value.contains("di pikiranmu")) return true
        if (value.contains("create note") || value.contains("buat catatan")) return true
        if (value.contains("friend suggestion") || value.contains("saran teman")) return true
        if (value == "facebook logo" || value == "search facebook" || value == "messaging") {
            return true
        }
        return FACEBOOK_CHROME_PREFIXES.any { prefix -> value == prefix || value.startsWith("$prefix,") }
    }

    private fun viewIdResourceMatches(viewId: String, resource: String): Boolean {
        val leaf = viewId.substringAfterLast('/').lowercase(Locale.ROOT)
        if (leaf.isEmpty() || leaf.contains("name removed")) return false
        return leaf == resource ||
            leaf.endsWith("_$resource") ||
            leaf.startsWith("${resource}_")
    }

    private fun isProfileMetricLine(value: String): Boolean {
        val normalized = value.lowercase(Locale.ROOT).replace(" ", "")
        if (
            PROFILE_METRIC_LABELS.any { label ->
                value.equals(label, ignoreCase = true) ||
                    value.contains(" $label", ignoreCase = true) ||
                    value.contains("$label ", ignoreCase = true)
            }
        ) {
            return true
        }
        // Compose often packs "471followers" into one contentDescription.
        return PACKED_METRIC_LINE.matches(normalized)
    }

    private data class ProfileNodeValue(
        val text: String,
        val viewId: String,
        val left: Int,
        val top: Int,
        val right: Int,
        val bottom: Int,
    ) {
        val centerX: Int get() = left + (right - left) / 2
        val centerY: Int get() = top + (bottom - top) / 2
    }

    private fun enumerationMethod(record: InventoryRecord): String = when (record.sourceAdapter) {
        SourceAdapter.SMS,
        SourceAdapter.CONTACT,
        -> "android_content_provider"
        SourceAdapter.VISIBLE_UI -> "android_uiautomator"
        SourceAdapter.NOTIFICATION -> "android_notification_listener"
        else -> "android_platform_api"
    }

    private const val INSTAGRAM_PACKAGE = "com.instagram.android"
    private const val MAX_PROFILE_BIO_LINES = 20
    private const val MAX_PROFILE_BIO_LENGTH = 4096
    private const val PROFILE_METRIC_MAX_DISTANCE = 600
    private val ACCOUNT_REFERENCE = Regex("(?<![A-Za-z0-9._])@([A-Za-z0-9._]{2,30})")
    private val ACCOUNT_MARKER = Regex("^[A-Za-z0-9._]{2,30}$")
    private val NUMERIC_ACCOUNT_MARKER = Regex("^[0-9._]+$")
    private val PROFILE_COUNT = Regex("^(?:[0-9][0-9.,]*|[0-9:. ]+(?:am|pm)?)$")
    private val POST_LABELS = setOf("posts", "postingan", "kiriman", "tweets", "tweet")
    private val FOLLOWER_LABELS = setOf("followers", "following", "pengikut", "diikuti")
    private val POST_METRIC_LABELS = setOf("posts", "postingan", "kiriman", "tweets", "tweet")
    private val FOLLOWER_METRIC_LABELS = setOf(
        "followers",
        "pengikut",
    )
    private val FRIEND_METRIC_LABELS = setOf(
        "friends",
        "teman",
        "friend",
    )
    private val FOLLOWING_METRIC_LABELS = setOf("following", "mengikuti", "diikuti")
    private val PROFILE_METRIC_LABELS =
        POST_METRIC_LABELS + FOLLOWER_METRIC_LABELS + FRIEND_METRIC_LABELS +
            FOLLOWING_METRIC_LABELS
    private val PROFILE_COUNT_VALUE = Regex(
        "^([0-9]+(?:[.,][0-9]+)?|[0-9][0-9., ]*)\\s*(k|m|b|rb|jt)?$",
    )
    private val PACKED_METRIC_LINE = Regex(
        "^\\d[\\d.,]*(k|m|b|rb|jt)?(friends?|teman|followers?|following|pengikut|mengikuti|posts?|postingan|kiriman)$",
    )
    private val PROFILE_USERNAME_RESOURCES = setOf(
        "action_bar_title",
        "profile_header_username",
        "profile_header_user_name",
        "action_bar_large_title_auto_size",
        "screen_name",
        "username",
        "user_name",
    )
    private val POST_METRIC_RESOURCES = setOf(
        "profile_header_familiar_post_count_value",
        "profile_header_post_count_front_familiar",
        "profile_header_post_count",
        "posts_stat",
    )
    private val FOLLOWER_METRIC_RESOURCES = setOf(
        "profile_header_familiar_followers_value",
        "profile_header_followers_stacked_familiar",
        "profile_header_followers_value",
        "followers_stat",
    )
    private val FRIEND_METRIC_RESOURCES = setOf(
        "friends_stat",
    )
    private val FOLLOWING_METRIC_RESOURCES = setOf(
        "profile_header_familiar_following_value",
        "profile_header_following_stacked_familiar",
        "profile_header_following_value",
        "following_stat",
    )
    private val PROFILE_DISPLAY_NAME_RESOURCES = setOf(
        "profile_header_full_name",
        "profile_display_name",
        "full_name",
        "display_name",
    )
    private val PROFILE_BIO_RESOURCES = setOf(
        "profile_header_bio",
        "profile_bio",
        "user_bio",
        "bio_text",
        "description",
    )
    private val PROFILE_LINK_PREFIX = listOf("http://", "https://", "www.")
    private val ACCOUNT_MARKER_SENTINELS = setOf("null", "undefined", "none", "nil")
    private val PROFILE_NOISE = setOf(
        "+",
        "@",
        "add",
        "add banners",
        "articles",
        "banners",
        "create",
        "curious",
        "curious_",
        "edit profile",
        "edit profil",
        "followers",
        "following",
        "for",
        "get verified",
        "highlights",
        "home",
        "inspo",
        "just",
        "likes",
        "media",
        "message",
        "messages",
        "more options",
        "navigate up",
        "new",
        "baru",
        "needed",
        "needed..",
        "null",
        "undefined",
        "open",
        "posts",
        "profile",
        "profile image",
        "profil",
        "ready",
        "ready for",
        "ready for.",
        "reels",
        "replies",
        "search and explore",
        "search button",
        "share profile",
        "share profil",
        "spotify",
        "today",
        "todays",
        "twitter",
        "vibe",
        "vibe_",
        "x",
        "facebook",
        "search",
        "search facebook",
        "messaging",
        "notifications",
        "groups",
        "friends",
        "menu",
        "story tray",
        "facebook logo",
    )
    private val FACEBOOK_CHROME_PREFIXES = listOf(
        "home",
        "reels",
        "friends",
        "groups",
        "notifications",
        "profile",
        "menu",
    )
    private val IG_AVATAR_PROMPT_FRAGMENTS = listOf(
        "curious",
        "inspo",
        "needed",
        "ready for",
        "today",
        "vibe",
    )

    private fun exif(value: ExifMetadata): JSONObject = JSONObject()
        .put("state", value.state)
        .put("orientation", value.orientation ?: JSONObject.NULL)
        .put("camera_make", value.cameraMake ?: JSONObject.NULL)
        .put("camera_model", value.cameraModel ?: JSONObject.NULL)
        .put("lens_model", value.lensModel ?: JSONObject.NULL)
        .put("exposure_time", value.exposureTime ?: JSONObject.NULL)
        .put("aperture", value.aperture ?: JSONObject.NULL)
        .put("focal_length", value.focalLength ?: JSONObject.NULL)
        .put("iso", value.iso ?: JSONObject.NULL)
        .put("latitude", value.latitude ?: JSONObject.NULL)
        .put("longitude", value.longitude ?: JSONObject.NULL)
        .put("altitude", value.altitude ?: JSONObject.NULL)
        .put("captured_at", nullableTimestamp(value.capturedAtEpochMs))
        .put("warning_codes", JSONArray(value.warningCodes))

    private fun timestamp(epochMs: Long): String = Instant.ofEpochMilli(epochMs).toString()

    private fun nullableTimestamp(epochMs: Long?): Any =
        epochMs?.let(::timestamp) ?: JSONObject.NULL
}
