package com.siksik.agent.selection

import com.siksik.agent.model.ApiException
import java.security.MessageDigest
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

data class KeywordPolicy(
    val keyword: String,
    val category: String,
    val matchTerms: List<String>,
    val weightBasisPoints: Int,
)

data class SelectionPolicy(
    val schemaVersion: Int,
    val policyVersion: String,
    val keywords: List<KeywordPolicy>,
    val sourceWeights: Map<String, Int>,
    val textSignalWeights: Map<String, Int>,
    val faceWeightBasisPoints: Int,
    val objectLabelWeights: Map<String, Int>,
    val requiredSocialScopes: Set<String>,
    val duplicateRepresentativePolicy: String,
    val thresholdBasisPoints: Int,
    val maximumCandidates: Int,
    val maximumBytes: Long,
    val policyFingerprint: String,
    val encodedJson: String,
)

data class SelectionModelSignal(
    val signal: String,
    val value: String,
    val weightBasisPoints: Int,
)

data class SelectionEvaluation(
    val recordId: String,
    val sourceKind: String,
    val sourceApp: String?,
    val evidenceText: String?,
    val scoreBasisPoints: Int,
    val thresholdBasisPoints: Int,
    val autoSelected: Boolean,
    val eligibleForAutomaticSelection: Boolean,
    val matchedKeywords: List<String>,
    val matchedRules: List<String>,
    val modelSignals: List<SelectionModelSignal>,
    val reasons: List<String>,
    val duplicateGroupId: String?,
    val representativeRecordId: String?,
    val sizeBytes: Long?,
    val thumbnailAvailable: Boolean,
    val decidedAtEpochMs: Long,
)

enum class SelectionRunState(val wireName: String) {
    RUNNING("running"),
    AWAITING_REVIEW("awaiting_review"),
    CONFIRMED("confirmed"),
    CANCELLED("cancelled"),
    FAILED("failed"),
}

enum class HumanOverride(val wireName: String) {
    NONE("none"),
    INCLUDE("include"),
    EXCLUDE("exclude");

    companion object {
        fun fromWireName(value: String): HumanOverride = entries.firstOrNull {
            it.wireName == value
        } ?: throw ApiException("validation_error", "Override selection tidak valid.", 422)
    }
}

data class SelectionTotals(
    val total: Int,
    val evaluated: Int,
    val candidates: Int,
    val autoSelected: Int,
    val selected: Int,
    val belowThreshold: Int,
    val selectedBytes: Long,
)

data class SelectionRun(
    val crawlId: String,
    val sessionId: String,
    val state: SelectionRunState,
    val policyVersion: String,
    val policyFingerprint: String,
    val revision: Int,
    val selectionFingerprint: String?,
    val reviewCandidates: Boolean,
    val totals: SelectionTotals,
    val startedAtEpochMs: Long,
    val updatedAtEpochMs: Long,
    val frozenAtEpochMs: Long?,
    val confirmedAtEpochMs: Long?,
    val failureReason: String?,
)

data class SelectionCandidate(
    val recordId: String,
    val sourceKind: String,
    val sourceApp: String?,
    val evidenceText: String?,
    val scoreBasisPoints: Int,
    val thresholdBasisPoints: Int,
    val autoSelected: Boolean,
    val selected: Boolean,
    val eligibleForAutomaticSelection: Boolean,
    val matchedKeywords: List<String>,
    val matchedRules: List<String>,
    val modelSignals: List<SelectionModelSignal>,
    val reasons: List<String>,
    val humanOverride: HumanOverride,
    val operatorId: String?,
    val decidedAtEpochMs: Long,
    val duplicateGroupId: String?,
    val representativeRecordId: String?,
    val sizeBytes: Long?,
    val thumbnailAvailable: Boolean,
)

data class SelectionCandidatePage(
    val candidates: List<SelectionCandidate>,
    val nextRecordId: String?,
)

object SelectionPolicyCodec {
    fun parse(value: JSONObject): SelectionPolicy {
        value.requireExactKeys(POLICY_KEYS)
        if (value.getInt("schema_version") != 1) invalidPolicy()
        val version = value.getString("policy_version")
        if (!SAFE_POLICY_VERSION.matches(version)) invalidPolicy()
        val keywordValues = value.getJSONArray("keywords")
        if (keywordValues.length() !in 1..MAX_KEYWORDS) invalidPolicy()
        val keywords = buildList {
            for (index in 0 until keywordValues.length()) {
                val item = keywordValues.getJSONObject(index)
                item.requireExactKeys(KEYWORD_KEYS)
                val keyword = item.getString("keyword")
                val category = item.getString("category")
                val terms = item.getJSONArray("match_terms").strings(MAX_MATCH_TERMS)
                val weight = item.getInt("weight_basis_points")
                if (
                    keyword.length !in 1..MAX_TERM_LENGTH ||
                    normalizeSelectionText(keyword) != keyword ||
                    !SAFE_CATEGORY.matches(category) ||
                    terms.isEmpty() ||
                    terms.toSet().size != terms.size ||
                    terms.any {
                        it.length !in 1..MAX_TERM_LENGTH || normalizeSelectionText(it) != it
                    } ||
                    weight !in 0..MAX_BASIS_POINTS
                ) {
                    invalidPolicy()
                }
                add(KeywordPolicy(keyword, category, terms, weight))
            }
        }
        if (keywords.map(KeywordPolicy::keyword).toSet().size != keywords.size) invalidPolicy()
        val sourceWeights = integerMap(
            value.getJSONObject("source_weights_basis_points"),
            SOURCE_KINDS,
        )
        val textWeights = integerMap(
            value.getJSONObject("text_signal_weights_basis_points"),
            TEXT_SIGNALS,
        )
        val objectWeightsObject = value.getJSONObject("object_label_weights_basis_points")
        if (objectWeightsObject.length() > MAX_OBJECT_LABELS) invalidPolicy()
        val objectWeights = buildMap {
            objectWeightsObject.keys().asSequence().sorted().forEach { label ->
                val weight = objectWeightsObject.getInt(label)
                if (!SAFE_OBJECT_LABEL.matches(label) || weight !in 0..MAX_BASIS_POINTS) {
                    invalidPolicy()
                }
                put(label, weight)
            }
        }
        val requiredSocialScopesArray = value.getJSONArray("required_social_scopes")
        val requiredSocialScopes = requiredSocialScopesArray
            .strings(MAX_SOCIAL_SCOPES)
            .toSet()
        if (
            requiredSocialScopes.size != requiredSocialScopesArray.length() ||
            requiredSocialScopes.any { it !in SOCIAL_SCOPES }
        ) {
            invalidPolicy()
        }
        val faceWeight = value.getInt("face_weight_basis_points")
        val threshold = value.getInt("threshold_basis_points")
        val maximumCandidates = value.getInt("maximum_candidates")
        val maximumBytes = value.getLong("maximum_bytes")
        val duplicatePolicy = value.getString("duplicate_representative_policy")
        val fingerprint = value.getString("policy_fingerprint")
        if (
            faceWeight !in 0..MAX_BASIS_POINTS ||
            threshold !in 0..MAX_BASIS_POINTS ||
            maximumCandidates !in 1..MAX_CANDIDATES ||
            maximumBytes !in 1..MAX_BYTES ||
            duplicatePolicy !in setOf("representative_only", "include_all") ||
            !SHA256.matches(fingerprint)
        ) {
            invalidPolicy()
        }
        val unsigned = JSONObject(value.toString()).apply { remove("policy_fingerprint") }
        if (sha256(canonicalJson(unsigned)) != fingerprint) {
            throw ApiException(
                "selection_policy_mismatch",
                "Fingerprint policy selection tidak sesuai.",
                409,
            )
        }
        return SelectionPolicy(
            1,
            version,
            keywords,
            sourceWeights,
            textWeights,
            faceWeight,
            objectWeights,
            requiredSocialScopes,
            duplicatePolicy,
            threshold,
            maximumCandidates,
            maximumBytes,
            fingerprint,
            value.toString(),
        )
    }

    fun fingerprint(value: JSONObject): String = sha256(canonicalJson(value))

    fun canonicalJson(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().sorted().joinToString(",", "{", "}") { key ->
            "${JSONObject.quote(key)}:${canonicalJson(value.get(key))}"
        }
        is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { index ->
            canonicalJson(value.get(index))
        }
        is String -> JSONObject.quote(value)
        is Boolean -> value.toString()
        is Byte, is Short, is Int, is Long -> value.toString()
        else -> throw IllegalArgumentException("unsupported_canonical_json_value")
    }

    private fun integerMap(value: JSONObject, keys: Set<String>): Map<String, Int> {
        value.requireExactKeys(keys)
        return keys.sorted().associateWith { key ->
            value.getInt(key).also { if (it !in 0..MAX_BASIS_POINTS) invalidPolicy() }
        }
    }

    private fun JSONArray.strings(maximum: Int): List<String> {
        if (length() > maximum) invalidPolicy()
        return buildList { for (index in 0 until length()) add(getString(index)) }
    }

    private fun JSONObject.requireExactKeys(expected: Set<String>) {
        if (keys().asSequence().toSet() != expected) invalidPolicy()
    }

    private fun invalidPolicy(): Nothing = throw ApiException(
        "validation_error",
        "Policy selection tidak valid.",
        422,
    )

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { byte ->
            (byte.toInt() and 0xff).toString(16).padStart(2, '0')
        }

    private val POLICY_KEYS = setOf(
        "schema_version",
        "policy_version",
        "keywords",
        "source_weights_basis_points",
        "text_signal_weights_basis_points",
        "face_weight_basis_points",
        "object_label_weights_basis_points",
        "required_social_scopes",
        "duplicate_representative_policy",
        "threshold_basis_points",
        "maximum_candidates",
        "maximum_bytes",
        "policy_fingerprint",
    )
    private val KEYWORD_KEYS = setOf(
        "keyword",
        "category",
        "match_terms",
        "weight_basis_points",
    )
    val SOURCE_KINDS = setOf(
        "media_image",
        "media_video",
        "media_audio",
        "document",
        "sms",
        "contact",
        "visible_ui",
        "notification",
    )
    private val TEXT_SIGNALS = setOf(
        "ocr",
        "document_text",
        "sms",
        "visible_ui",
        "notification",
    )
    private val SOCIAL_SCOPES = setOf(
        "own_profile",
        "own_posts",
        "own_tweets",
        "own_story_archive",
        "own_comments",
        "own_replies",
    )
    private val SAFE_POLICY_VERSION = Regex("^[A-Za-z0-9._-]{1,64}$")
    private val SAFE_CATEGORY = Regex("^[a-z0-9_]{1,64}$")
    private val SAFE_OBJECT_LABEL = Regex("^[a-z0-9_.-]{1,64}$")
    private val SHA256 = Regex("^[0-9a-f]{64}$")
    private const val MAX_KEYWORDS = 256
    private const val MAX_MATCH_TERMS = 16
    private const val MAX_TERM_LENGTH = 128
    private const val MAX_OBJECT_LABELS = 64
    private const val MAX_SOCIAL_SCOPES = 6
    private const val MAX_BASIS_POINTS = 10_000
    private const val MAX_CANDIDATES = 1_000_000
    private const val MAX_BYTES = 4L * 1024L * 1024L * 1024L * 1024L
}

internal fun normalizeSelectionText(value: String): String = value
    .lowercase(Locale.ROOT)
    .trim()
    .replace(Regex("\\s+"), " ")
