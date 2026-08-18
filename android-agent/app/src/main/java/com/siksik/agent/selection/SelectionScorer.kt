package com.siksik.agent.selection

import com.siksik.agent.preprocessing.PreprocessedSelectionInput
import java.util.Locale
import org.json.JSONObject

class SelectionScorer(
    private val policy: SelectionPolicy,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val keywordMatchers = policy.keywords.map { rule ->
        rule to rule.matchTerms.map(::phrasePattern)
    }

    fun evaluate(input: PreprocessedSelectionInput): SelectionEvaluation {
        require(input.sourceKind in SelectionPolicyCodec.SOURCE_KINDS)
        val preprocessing = JSONObject(input.preprocessingJson)
        val normalizedText = normalizeSelectionText(input.normalizedText.orEmpty())
        val textSignal = textSignal(input.sourceKind, preprocessing, normalizedText)
        val matchedKeywordMatches = if (textSignal == null) emptyList() else keywordMatches(
            normalizedText,
        )
        val matchedKeywordRules = matchedKeywordMatches.map { it.first }
        val matchedKeywords = matchedKeywordMatches.map { it.second }.distinct().sorted()
        val matchedRules = mutableSetOf<String>()
        val modelSignals = mutableListOf<SelectionModelSignal>()
        var score = policy.sourceWeights.getValue(input.sourceKind)
        if (score > 0) matchedRules.add("source:${input.sourceKind}")

        matchedKeywordRules.forEach { rule ->
            score += rule.weightBasisPoints
            matchedRules.add("keyword:${rule.category}")
        }
        if (matchedKeywordRules.isNotEmpty() && textSignal != null) {
            val weight = policy.textSignalWeights.getValue(textSignal)
            score += weight
            matchedRules.add("text:$textSignal")
        }

        val faceCount = preprocessing.optJSONObject("face")?.optInt("signal_count", 0) ?: 0
        if (faceCount > 0 && policy.faceWeightBasisPoints > 0) {
            score += policy.faceWeightBasisPoints
            matchedRules.add("face:present")
            modelSignals.add(
                SelectionModelSignal(
                    "face_count",
                    faceCount.toString(),
                    policy.faceWeightBasisPoints,
                ),
            )
        }

        bestObjectSignal(preprocessing)?.let { signal ->
            score += signal.weightBasisPoints
            matchedRules.add("object:${signal.signal.substringAfter(':')}")
            modelSignals.add(signal)
        }

        val duplicate = preprocessing.optJSONObject("duplicate_membership")
        val exactGroup = duplicate.nullableString("exact_group_id")
        val perceptualGroup = duplicate.nullableString("perceptual_group_id")
        val duplicateGroup = exactGroup ?: perceptualGroup
        val representative = duplicate.nullableString("representative_record_id")
        val isNonRepresentative = duplicateGroup != null && representative != null &&
            representative != input.recordId
        val requiredSocialScope = input.sourceKind == "visible_ui" &&
            input.socialScope in policy.requiredSocialScopes
        val inWindowMedia = input.sourceKind in IN_WINDOW_MEDIA_KINDS
        if (requiredSocialScope) matchedRules.add("scope:${input.socialScope}")
        if (inWindowMedia) matchedRules.add("in_window_media")
        if (duplicateGroup != null) {
            matchedRules.add(
                if (isNonRepresentative) "duplicate:non_representative" else
                    "duplicate:representative",
            )
        }

        val boundedScore = score.coerceIn(0, MAX_BASIS_POINTS)
        val thresholdMet = boundedScore >= policy.thresholdBasisPoints
        val autoSelected = thresholdMet || requiredSocialScope || inWindowMedia
        val reasons = buildList {
            add(if (thresholdMet) "threshold_met" else "threshold_not_met")
            if (requiredSocialScope) add("required_social_scope")
            if (inWindowMedia) add("in_window_media")
            if (matchedKeywords.isNotEmpty()) add("keyword_match")
            if (modelSignals.isNotEmpty()) add("model_signal")
            if (isNonRepresentative) add("duplicate_non_representative")
            if (duplicateGroup != null && !isNonRepresentative) add("duplicate_representative")
        }
        return SelectionEvaluation(
            recordId = input.recordId,
            sourceKind = input.sourceKind,
            sourceApp = input.sourceApp,
            evidenceText = input.normalizedText?.let(::boundedEvidence),
            scoreBasisPoints = boundedScore,
            thresholdBasisPoints = policy.thresholdBasisPoints,
            autoSelected = autoSelected,
            eligibleForAutomaticSelection = autoSelected &&
                (!isNonRepresentative || requiredSocialScope),
            matchedKeywords = matchedKeywords,
            matchedRules = matchedRules.sorted(),
            modelSignals = modelSignals.sortedBy(SelectionModelSignal::signal),
            reasons = reasons,
            duplicateGroupId = duplicateGroup,
            representativeRecordId = representative,
            sizeBytes = input.sizeBytes,
            thumbnailAvailable = input.thumbnailAvailable,
            decidedAtEpochMs = clock(),
        )
    }

    private fun textSignal(
        sourceKind: String,
        preprocessing: JSONObject,
        normalizedText: String,
    ): String? {
        if (normalizedText.isEmpty()) return null
        if (preprocessing.optString("status") !in SUCCESS_STATUSES) return null
        return when (sourceKind) {
            "media_image", "media_video" -> preprocessing.optJSONObject("ocr")
                ?.optString("status")
                ?.takeIf { it in SUCCESS_STATUSES }
                ?.let { "ocr" }
            "document" -> preprocessing.optJSONObject("document_text")
                ?.optString("status")
                ?.takeIf { it in SUCCESS_STATUSES }
                ?.let { "document_text" }
            "sms" -> "sms"
            "visible_ui" -> "visible_ui"
            "notification" -> "notification"
            else -> null
        }
    }

    private fun bestObjectSignal(preprocessing: JSONObject): SelectionModelSignal? {
        val values = preprocessing.optJSONObject("objects")?.optJSONArray("labels") ?: return null
        return buildList {
            for (index in 0 until values.length()) {
                val item = values.optJSONObject(index) ?: continue
                val label = item.optString("label").lowercase(Locale.ROOT)
                val configuredWeight = policy.objectLabelWeights[label] ?: continue
                val confidence = item.optDouble("confidence", 0.0)
                if (confidence < MIN_OBJECT_CONFIDENCE || confidence > 1.0) continue
                val weight = (configuredWeight * confidence).toInt().coerceIn(0, MAX_BASIS_POINTS)
                add(
                    SelectionModelSignal(
                        "object:$label",
                        "%.3f".format(Locale.ROOT, confidence),
                        weight,
                    ),
                )
            }
        }.sortedWith(
            compareByDescending<SelectionModelSignal> { it.weightBasisPoints }
                .thenBy { it.signal },
        ).firstOrNull()
    }

    private fun keywordMatches(normalizedText: String): List<Pair<KeywordPolicy, String>> {
        val seenTerms = mutableSetOf<String>()
        return buildList {
            keywordMatchers.forEach { (rule, patterns) ->
                val phraseMatched = patterns.first().containsMatchIn(normalizedText)
                if (phraseMatched) {
                    if (seenTerms.add(rule.matchTerms.first())) {
                        add(rule to rule.matchTerms.first())
                    }
                    return@forEach
                }
                val tokenIndex = (1 until patterns.size).firstOrNull { index ->
                    rule.matchTerms[index] !in seenTerms &&
                        patterns[index].containsMatchIn(normalizedText)
                }
                if (tokenIndex != null) {
                    val term = rule.matchTerms[tokenIndex]
                    seenTerms.add(term)
                    add(rule to term)
                }
            }
        }
    }

    private fun phrasePattern(term: String): Regex {
        val parts = term.split(' ').filter(String::isNotEmpty).map(Regex::escape)
        val body = parts.joinToString("[\\s\\-_/\\.]+")
        return Regex("(?<![a-z0-9])$body(?![a-z0-9])")
    }

    private fun boundedEvidence(value: String): String? = normalizeSelectionText(value)
        .take(MAX_EVIDENCE_CHARACTERS)
        .takeIf(String::isNotEmpty)

    private fun JSONObject?.nullableString(key: String): String? = when {
        this == null || !has(key) || isNull(key) -> null
        else -> getString(key)
    }

    companion object {
        private val SUCCESS_STATUSES = setOf("completed", "truncated")
        private val IN_WINDOW_MEDIA_KINDS = setOf("media_image", "media_video", "document")
        private const val MIN_OBJECT_CONFIDENCE = 0.5
        private const val MAX_EVIDENCE_CHARACTERS = 512
        private const val MAX_BASIS_POINTS = 10_000
    }
}
