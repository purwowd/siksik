package com.siksik.agent

import com.siksik.agent.model.ApiException
import com.siksik.agent.preprocessing.PreprocessedSelectionInput
import com.siksik.agent.selection.SelectionPolicyCodec
import com.siksik.agent.selection.SelectionScorer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SelectionPolicyScorerTest {
    @Test
    fun canonicalFingerprintIsDeterministicAndMismatchIsRejected() {
        val first = policyJson()
        val reordered = JSONObject(first.toString())
        assertEquals(
            SelectionPolicyCodec.fingerprint(unsigned(first)),
            SelectionPolicyCodec.fingerprint(unsigned(reordered)),
        )
        SelectionPolicyCodec.parse(first)

        val tampered = JSONObject(first.toString()).put("threshold_basis_points", 5_501)
        try {
            SelectionPolicyCodec.parse(tampered)
            throw AssertionError("tampered policy accepted")
        } catch (exception: ApiException) {
            assertEquals("selection_policy_mismatch", exception.code)
        }
    }

    @Test
    fun wordBoundariesAndEqualThresholdAreDeterministic() {
        val scorer = SelectionScorer(SelectionPolicyCodec.parse(policyJson())) { 1_700_000_000_000 }
        val below = scorer.evaluate(input("noscobom", "sms"))
        val equal = scorer.evaluate(input("BOM!", "sms"))

        assertFalse(below.autoSelected)
        assertTrue(equal.autoSelected)
        assertEquals(5_500, equal.scoreBasisPoints)
        assertEquals(listOf("bom"), equal.matchedKeywords)
        assertEquals(1_700_000_000_000, equal.decidedAtEpochMs)
    }

    @Test
    fun nonRepresentativeDuplicateRetainsTraceButIsNotAutomaticallyEligible() {
        val preprocessing = JSONObject()
            .put("schema_version", 1)
            .put("status", "completed")
            .put(
                "duplicate_membership",
                JSONObject()
                    .put("exact_group_id", "duplicate_fixture")
                    .put("perceptual_group_id", JSONObject.NULL)
                    .put("representative_record_id", "record_representative"),
            )
        val evaluated = SelectionScorer(SelectionPolicyCodec.parse(policyJson())).evaluate(
            input("bom", "sms", preprocessing),
        )

        assertTrue(evaluated.autoSelected)
        assertFalse(evaluated.eligibleForAutomaticSelection)
        assertTrue("duplicate_non_representative" in evaluated.reasons)
        assertEquals("record_representative", evaluated.representativeRecordId)
    }

    private fun input(
        text: String,
        source: String,
        preprocessing: JSONObject = JSONObject()
            .put("schema_version", 1)
            .put("status", "completed"),
    ) = PreprocessedSelectionInput(
        recordId = "record_fixture",
        sourceKind = source,
        sourceApp = null,
        socialScope = null,
        normalizedText = text,
        preprocessingJson = preprocessing.toString(),
        sizeBytes = 100,
        thumbnailAvailable = false,
    )

    private fun policyJson(): JSONObject {
        val value = JSONObject()
            .put("schema_version", 1)
            .put("policy_version", "fixture-v1")
            .put(
                "keywords",
                JSONArray().put(
                    JSONObject()
                        .put("keyword", "bom")
                        .put("category", "fixture")
                        .put("match_terms", JSONArray().put("bom"))
                        .put("weight_basis_points", 4_000),
                ),
            )
            .put(
                "source_weights_basis_points",
                JSONObject()
                    .put("media_image", 300)
                    .put("media_video", 300)
                    .put("media_audio", 100)
                    .put("document", 400)
                    .put("sms", 600)
                    .put("contact", 0)
                    .put("visible_ui", 700)
                    .put("notification", 500),
            )
            .put(
                "text_signal_weights_basis_points",
                JSONObject()
                    .put("ocr", 1_000)
                    .put("document_text", 1_100)
                    .put("sms", 900)
                    .put("visible_ui", 1_000)
                    .put("notification", 1_000),
            )
            .put("face_weight_basis_points", 400)
            .put("object_label_weights_basis_points", JSONObject().put("knife", 1_500))
            .put("required_social_scopes", JSONArray().put("own_profile"))
            .put("duplicate_representative_policy", "representative_only")
            .put("threshold_basis_points", 5_500)
            .put("maximum_candidates", 100)
            .put("maximum_bytes", 1_000_000)
        return value.put("policy_fingerprint", SelectionPolicyCodec.fingerprint(value))
    }

    private fun unsigned(value: JSONObject): JSONObject = JSONObject(value.toString()).apply {
        remove("policy_fingerprint")
    }
}
