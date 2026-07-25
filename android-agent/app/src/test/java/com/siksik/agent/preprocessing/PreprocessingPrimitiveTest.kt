package com.siksik.agent.preprocessing

import java.io.ByteArrayInputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PreprocessingPrimitiveTest {
    @Test
    fun normalizationSeparatesWhitespaceChangesFromTruncation() {
        val whitespaceOnly = normalizeSearchTextBounded(
            "  Alpha   beta\r\n\r\n\tGamma  ",
            100,
        )
        assertEquals("Alpha beta\nGamma", whitespaceOnly.value)
        assertFalse(whitespaceOnly.truncated)

        val bounded = normalizeSearchTextBounded("123456", 5)
        assertEquals("12345", bounded.value)
        assertTrue(bounded.truncated)
    }

    @Test
    fun exactHashStreamsAndReportsCancellationAndBounds() {
        val completed = StreamingExactHashPreprocessor(maxBytes = 1024).process(
            input("abc".toByteArray()),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, completed.execution.status)
        assertEquals(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            completed.sha256,
        )
        assertEquals(3, completed.bytesRead)

        val cancelled = StreamingExactHashPreprocessor(maxBytes = 1024).process(
            input("abc".toByteArray()),
            CancellationToken { true },
        )
        assertEquals(ExecutionStatus.CANCELLED, cancelled.execution.status)
        assertNull(cancelled.sha256)

        val bounded = StreamingExactHashPreprocessor(maxBytes = 2).process(
            input("abc".toByteArray(), declaredSize = null),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.TRUNCATED, bounded.execution.status)
        assertNull(bounded.sha256)
    }

    @Test
    fun differenceHashIsDeterministic() {
        val pixels = IntArray(72)
        for (row in 0 until 8) {
            for (column in 0 until 9) {
                val value = 255 - column * 20
                pixels[row * 9 + column] =
                    0xff000000.toInt() or (value shl 16) or (value shl 8) or value
            }
        }
        assertEquals("ffffffffffffffff", DifferenceHash.fromArgb(pixels))
        assertEquals(64, DifferenceHash.hammingDistance("ffffffffffffffff", "0000000000000000"))
    }

    @Test
    fun duplicateGroupsAndRepresentativesAreStable() {
        val signals = listOf(
            DuplicateSignal("b", "a".repeat(64), "0000000000000001", 200, 20),
            DuplicateSignal("a", "a".repeat(64), "0000000000000000", 100, 10),
            DuplicateSignal("d", null, "ffffffffffffffff", 50, 5),
            DuplicateSignal("c", null, "fffffffffffffffe", 60, 6),
        )
        val forward = DuplicateClusterer(perceptualDistance = 1).cluster(signals)
        val reverse = DuplicateClusterer(perceptualDistance = 1).cluster(signals.reversed())
        assertEquals(forward, reverse)
        assertEquals("b", forward.first { it.recordId == "a" }.representativeRecordId)
        assertEquals("c", forward.first { it.recordId == "d" }.representativeRecordId)
        assertTrue(forward.all { it.exactGroupId != null || it.perceptualGroupId != null })
    }

    @Test(expected = IllegalArgumentException::class)
    fun duplicateClusteringRejectsWorkBeyondConfiguredBound() {
        DuplicateClusterer(maxSignals = 1).cluster(
            listOf(
                DuplicateSignal("a", null, null, 0, 0),
                DuplicateSignal("b", null, null, 0, 0),
            ),
        )
    }

    @Test
    fun anonymousFaceClustersDoNotExposeVectorInMembership() {
        val result = AnonymousFaceClusterer(minimumSimilarity = 0.9f).cluster(
            listOf(
                FaceSignal("record-b", 0, 0.9f, 20, floatArrayOf(1f, 0f)),
                FaceSignal("record-a", 0, 0.8f, 10, floatArrayOf(0.99f, 0.01f)),
                FaceSignal("record-c", 0, 0.7f, 10, floatArrayOf(0f, 1f)),
            ),
        )
        assertEquals(listOf("record-a", "record-b", "record-c"), result.map { it.recordId })
        assertEquals(result[0].clusterIds, result[1].clusterIds)
        assertTrue(result[2].clusterIds.isEmpty())
    }

    private fun input(bytes: ByteArray, declaredSize: Long? = bytes.size.toLong()) = PreprocessInput(
        recordId = "record-1",
        mimeType = "application/octet-stream",
        sizeBytes = declaredSize,
        width = null,
        height = null,
        streamProvider = { ByteArrayInputStream(bytes) },
    )
}
