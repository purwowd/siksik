package com.siksik.agent.automation

import java.time.Instant
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SocialTimeScopeTest {
    private val now = Instant.parse("2026-08-14T10:00:00Z").toEpochMilli()

    @Test
    fun relativeAndLocalizedDatesRespectCutoff() {
        val quickCutoff = Instant.parse("2026-05-14T10:00:00Z").toEpochMilli()

        assertTrue(SocialTimeScope.evaluate(listOf("4mo"), quickCutoff, now).outOfScope)
        assertTrue(SocialTimeScope.evaluate(listOf("4mo."), quickCutoff, now).outOfScope)
        assertFalse(SocialTimeScope.evaluate(listOf("2 bulan"), quickCutoff, now).outOfScope)
        assertFalse(SocialTimeScope.evaluate(listOf("a month ago"), quickCutoff, now).outOfScope)
        assertFalse(SocialTimeScope.evaluate(listOf("sebulan lalu"), quickCutoff, now).outOfScope)
        assertTrue(SocialTimeScope.evaluate(listOf("a year ago"), quickCutoff, now).outOfScope)
        assertTrue(SocialTimeScope.evaluate(listOf("13 Mei 2026"), quickCutoff, now).outOfScope)
        assertFalse(SocialTimeScope.evaluate(listOf("Aug 10"), quickCutoff, now).outOfScope)
    }

    @Test
    fun unknownLabelsAreRetainedRatherThanMisclassified() {
        val cutoff = Instant.parse("2026-05-14T10:00:00Z").toEpochMilli()
        val decision = SocialTimeScope.evaluate(
            listOf("caption without a date", "42 likes"),
            cutoff,
            now,
        )

        assertFalse(decision.outOfScope)
        assertNull(decision.sourceTimeEpochMs)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsCutoffAtOrAfterReferenceTime() {
        SocialTimeScope.evaluate(emptyList(), now, now)
    }
}
