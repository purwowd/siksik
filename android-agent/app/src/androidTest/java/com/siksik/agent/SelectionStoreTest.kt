package com.siksik.agent

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.model.ApiException
import com.siksik.agent.selection.HumanOverride
import com.siksik.agent.selection.SelectionEvaluation
import com.siksik.agent.selection.SelectionPolicy
import com.siksik.agent.selection.SelectionRunState
import com.siksik.agent.selection.SelectionStore
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SelectionStoreTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun ledgerRetainsBelowThresholdSupportsRevisionAndFreezesConfirmation() {
        val suffix = UUID.randomUUID().toString()
        val sessionId = "session_$suffix"
        val crawlId = "crawl_$suffix"
        val store = SelectionStore(context)
        try {
            store.clearSession(sessionId)
            val policy = policy()
            store.start(sessionId, crawlId, policy, true, 3, 1_000)
            store.appendEvaluations(
                sessionId,
                crawlId,
                listOf(
                    evaluation("record_a_$suffix", 7_000, true),
                    evaluation("record_b_$suffix", 5_500, true),
                    evaluation("record_c_$suffix", 5_499, false),
                ),
                1_001,
            )
            val frozen = store.freeze(sessionId, crawlId, policy, 1_002)

            assertEquals(SelectionRunState.AWAITING_REVIEW, frozen.state)
            assertEquals(3, frozen.totals.evaluated)
            assertEquals(2, frozen.totals.selected)
            assertEquals(1, frozen.totals.belowThreshold)
            assertNotNull(frozen.selectionFingerprint)
            val page = store.page(sessionId, crawlId, null, 100)
            assertEquals(3, page.candidates.size)
            assertTrue(page.candidates.any { !it.autoSelected && !it.selected })

            val changed = store.mutate(
                sessionId,
                crawlId,
                "record_c_$suffix",
                1,
                HumanOverride.INCLUDE,
                "operator_$suffix",
                1_003,
            )
            assertEquals(2, changed.run.revision)
            assertTrue(changed.candidate.selected)
            assertEquals(HumanOverride.INCLUDE, changed.candidate.humanOverride)
            assertFalse(changed.run.selectionFingerprint == frozen.selectionFingerprint)

            try {
                store.mutate(
                    sessionId,
                    crawlId,
                    "record_a_$suffix",
                    1,
                    HumanOverride.EXCLUDE,
                    "operator_$suffix",
                    1_004,
                )
                throw AssertionError("stale revision accepted")
            } catch (exception: ApiException) {
                assertEquals("selection_revision_conflict", exception.code)
            }

            val confirmed = store.confirm(sessionId, crawlId, 2, 1_005)
            val repeated = store.confirm(sessionId, crawlId, 2, 1_006)
            assertEquals(SelectionRunState.CONFIRMED, confirmed.state)
            assertEquals(confirmed.confirmedAtEpochMs, repeated.confirmedAtEpochMs)
            try {
                store.mutate(
                    sessionId,
                    crawlId,
                    "record_a_$suffix",
                    2,
                    HumanOverride.EXCLUDE,
                    "operator_$suffix",
                    1_007,
                )
                throw AssertionError("confirmed selection mutated")
            } catch (exception: ApiException) {
                assertEquals("selection_immutable", exception.code)
            }
        } finally {
            store.clearSession(sessionId)
            store.close()
        }
    }

    private fun policy() = SelectionPolicy(
        schemaVersion = 1,
        policyVersion = "fixture-v1",
        keywords = emptyList(),
        sourceWeights = emptyMap(),
        textSignalWeights = emptyMap(),
        faceWeightBasisPoints = 0,
        objectLabelWeights = emptyMap(),
        requiredSocialScopes = setOf("own_profile"),
        duplicateRepresentativePolicy = "representative_only",
        thresholdBasisPoints = 5_500,
        maximumCandidates = 3,
        maximumBytes = 1_000,
        policyFingerprint = "a".repeat(64),
        encodedJson = "{}",
    )

    private fun evaluation(recordId: String, score: Int, selected: Boolean) = SelectionEvaluation(
        recordId = recordId,
        sourceKind = "sms",
        sourceApp = null,
        evidenceText = "bounded fixture",
        scoreBasisPoints = score,
        thresholdBasisPoints = 5_500,
        autoSelected = selected,
        eligibleForAutomaticSelection = selected,
        matchedKeywords = if (selected) listOf("fixture") else emptyList(),
        matchedRules = if (selected) listOf("keyword:fixture") else emptyList(),
        modelSignals = emptyList(),
        reasons = listOf(if (selected) "threshold_met" else "threshold_not_met"),
        duplicateGroupId = null,
        representativeRecordId = null,
        sizeBytes = 100,
        thumbnailAvailable = false,
        decidedAtEpochMs = 1_000,
    )
}
