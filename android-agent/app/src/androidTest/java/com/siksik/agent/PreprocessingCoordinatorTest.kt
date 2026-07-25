package com.siksik.agent

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.preprocessing.CancellationToken
import com.siksik.agent.preprocessing.EngineCapability
import com.siksik.agent.preprocessing.PreprocessingCoordinator
import com.siksik.agent.preprocessing.PreprocessingProcessor
import com.siksik.agent.preprocessing.PreprocessingRecordState
import com.siksik.agent.preprocessing.PreprocessingRecordUpdate
import com.siksik.agent.preprocessing.PreprocessingRunState
import com.siksik.agent.preprocessing.PreprocessingStore
import com.siksik.agent.preprocessing.ResourcePolicy
import com.siksik.agent.preprocessing.StoredPreprocessRecord
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.SourceAdapter
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PreprocessingCoordinatorTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun schedulerIsBoundedPersistsResultsAndPagesWithOpaqueCursor() {
        val store = PreprocessingStore(context)
        val processor = CountingProcessor()
        val coordinator = PreprocessingCoordinator(
            context,
            store,
            processor = processor,
            resources = ResourcePolicy { null },
        )
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(5), System.currentTimeMillis())
            coordinator.start(SESSION_ID, CRAWL_ID)
            val completed = awaitTerminal(coordinator)
            assertEquals(PreprocessingRunState.COMPLETE, completed.state)
            assertEquals(5, completed.totals.completed)
            assertEquals(
                5,
                completed.preprocessorTotals.getValue("exact_hash").processed,
            )
            assertTrue(processor.maximumActive.get() <= BuildConfig.MAX_PREPROCESS_CONCURRENCY)

            val first = coordinator.records(SESSION_ID, CRAWL_ID, null, 3)
            assertEquals(3, first.records.size)
            assertNotNull(first.nextCursor)
            assertTrue(first.records.all { !it.isNull("preprocessing") })
            val second = coordinator.records(SESSION_ID, CRAWL_ID, first.nextCursor, 3)
            assertEquals(2, second.records.size)
            assertEquals(null, second.nextCursor)
        } finally {
            coordinator.close()
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun runningRecordReturnsToPendingWhenCoordinatorResumes() {
        val store = PreprocessingStore(context)
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(1), System.currentTimeMillis())
            val now = System.currentTimeMillis()
            store.startOrResume(SESSION_ID, CRAWL_ID, now, now + 60_000)
            assertEquals(1, store.claimPending(CRAWL_ID, 1, now).size)
            assertEquals(1, store.getRun(SESSION_ID, CRAWL_ID).totals.processing)

            val resumed = store.startOrResume(SESSION_ID, CRAWL_ID, now + 1, now + 60_000)
            assertEquals(1, resumed.totals.pending)
            assertEquals(0, resumed.totals.processing)
        } finally {
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun cancellationRemainsTerminalWhenAWorkerFinishesLate() {
        val store = PreprocessingStore(context)
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(1), System.currentTimeMillis())
            val now = System.currentTimeMillis()
            store.startOrResume(SESSION_ID, CRAWL_ID, now, now + 60_000)
            val claimed = store.claimPending(CRAWL_ID, 1, now)
            assertEquals(1, claimed.size)

            val cancelled = store.requestCancel(SESSION_ID, CRAWL_ID, now + 1)
            assertEquals(PreprocessingRunState.CANCELLED, cancelled.state)
            assertEquals(1, cancelled.totals.cancelled)

            store.completeRecord(
                CRAWL_ID,
                claimed.single().recordId,
                PreprocessingRecordUpdate(
                    PreprocessingRecordState.COMPLETED,
                    JSONObject()
                        .put("schema_version", 1)
                        .put("status", "completed")
                        .put("warnings", JSONArray())
                        .toString(),
                    null,
                    claimed.single().contentSha256,
                    null,
                    null,
                ),
                now + 2,
            )
            val attemptedOverwrite = store.finishRun(
                SESSION_ID,
                CRAWL_ID,
                PreprocessingRunState.COMPLETE,
                emptyList(),
                now + 3,
            )

            assertEquals(PreprocessingRunState.CANCELLED, attemptedOverwrite.state)
            assertEquals(0, attemptedOverwrite.totals.completed)
            assertEquals(1, attemptedOverwrite.totals.cancelled)
            val page = store.recordPage(SESSION_ID, CRAWL_ID, null, 1, now + 4)
            assertEquals(
                "cancelled",
                page.records.single().getJSONObject("preprocessing").getString("status"),
            )
        } finally {
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun itemTimeoutIsPersistedAsAnExplicitPartialFailure() {
        val store = PreprocessingStore(context)
        val coordinator = PreprocessingCoordinator(
            context,
            store,
            processor = CountingProcessor(delayMs = 250),
            resources = ResourcePolicy { null },
            itemTimeoutMs = 25,
        )
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(1), System.currentTimeMillis())
            coordinator.start(SESSION_ID, CRAWL_ID)

            val completed = awaitTerminal(coordinator)
            assertEquals(PreprocessingRunState.PARTIAL, completed.state)
            assertEquals(1, completed.totals.failed)
            val preprocessing = coordinator.records(SESSION_ID, CRAWL_ID, null, 1)
                .records.single()
                .getJSONObject("preprocessing")
            assertEquals("failed", preprocessing.getString("status"))
            assertEquals(
                "preprocessing_timeout",
                preprocessing.getJSONArray("warnings").getString(0),
            )
        } finally {
            coordinator.close()
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun sessionCleanupRemovesRecordsRunsCursorsAndPreprocessorTotals() {
        val store = PreprocessingStore(context)
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(2), System.currentTimeMillis())
            val now = System.currentTimeMillis()
            store.startOrResume(SESSION_ID, CRAWL_ID, now, now + 60_000)
            store.claimPending(CRAWL_ID, 2, now).forEach { claimed ->
                store.completeRecord(
                    CRAWL_ID,
                    claimed.recordId,
                    PreprocessingRecordUpdate(
                        PreprocessingRecordState.COMPLETED,
                        countedPayload("completed"),
                        null,
                        claimed.contentSha256,
                        null,
                        null,
                    ),
                    now + 1,
                )
            }
            assertEquals(
                2,
                store.getRun(SESSION_ID, CRAWL_ID)
                    .preprocessorTotals.getValue("exact_hash").processed,
            )
            assertNotNull(store.recordPage(SESSION_ID, CRAWL_ID, null, 1, now + 2).nextCursor)

            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(1), now + 3)
            val recreated = store.startOrResume(
                SESSION_ID,
                CRAWL_ID,
                now + 3,
                now + 60_000,
            )
            assertEquals(1, recreated.totals.pending)
            assertEquals(
                0,
                recreated.preprocessorTotals.getValue("exact_hash").attempted,
            )
        } finally {
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun coordinatorCancellationTerminalizesPendingAndActiveRecords() {
        val store = PreprocessingStore(context)
        val started = CountDownLatch(1)
        val processor = object : PreprocessingProcessor {
            override fun capabilities(): Map<String, EngineCapability> = emptyMap()

            override fun process(
                record: StoredPreprocessRecord,
                cancellation: CancellationToken,
            ): PreprocessingRecordUpdate {
                started.countDown()
                while (!cancellation.isCancelled()) {
                    Thread.sleep(5)
                }
                return PreprocessingRecordUpdate(
                    PreprocessingRecordState.CANCELLED,
                    countedPayload("cancelled"),
                    record.normalizedText,
                    record.contentSha256,
                    null,
                    null,
                )
            }

            override fun close() = Unit
        }
        val coordinator = PreprocessingCoordinator(
            context,
            store,
            processor = processor,
            resources = ResourcePolicy { null },
        )
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(3), System.currentTimeMillis())
            coordinator.start(SESSION_ID, CRAWL_ID)
            assertTrue(started.await(2, TimeUnit.SECONDS))

            val cancelled = coordinator.cancel(SESSION_ID, CRAWL_ID)
            assertEquals(PreprocessingRunState.CANCELLED, cancelled.state)
            assertEquals(3, cancelled.totals.cancelled)
            assertEquals(
                listOf("cancelled", "cancelled", "cancelled"),
                coordinator.records(SESSION_ID, CRAWL_ID, null, 3).records.map {
                    it.getJSONObject("preprocessing").getString("status")
                },
            )
        } finally {
            coordinator.close()
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    @Test
    fun resourcePauseEndsAsExplicitPartialWhenSessionDeadlineExpires() {
        val store = PreprocessingStore(context)
        val processor = CountingProcessor()
        val coordinator = PreprocessingCoordinator(
            context,
            store,
            processor = processor,
            resources = ResourcePolicy { "preprocessing_thermal_pause" },
            sessionDeadlineMs = 40,
            resourceRecheckMs = 5,
        )
        try {
            store.clearSession(SESSION_ID)
            store.persist(SESSION_ID, CRAWL_ID, records(1), System.currentTimeMillis())
            coordinator.start(SESSION_ID, CRAWL_ID)

            val completed = awaitTerminal(coordinator)
            assertEquals(PreprocessingRunState.PARTIAL, completed.state)
            assertEquals(1, completed.totals.skipped)
            assertTrue("preprocessing_deadline_exceeded" in completed.partialReasons)
            assertEquals(0, processor.maximumActive.get())
            val preprocessing = coordinator.records(SESSION_ID, CRAWL_ID, null, 1)
                .records.single()
                .getJSONObject("preprocessing")
            assertEquals("skipped", preprocessing.getString("status"))
            assertEquals(
                "preprocessing_deadline_exceeded",
                preprocessing.getJSONArray("warnings").getString(0),
            )
        } finally {
            coordinator.close()
            store.clearSession(SESSION_ID)
            store.close()
        }
    }

    private fun awaitTerminal(coordinator: PreprocessingCoordinator):
        com.siksik.agent.preprocessing.PreprocessingRun {
        val deadline = System.currentTimeMillis() + 5_000
        while (System.currentTimeMillis() < deadline) {
            val run = coordinator.status(SESSION_ID, CRAWL_ID)
            if (run.state != PreprocessingRunState.RUNNING) return run
            Thread.sleep(25)
        }
        throw AssertionError("preprocessing did not finish")
    }

    private fun records(count: Int): List<InventoryRecord> = (0 until count).map { index ->
        InventoryRecord(
            recordId = "record_fixture_${index.toString().padStart(3, '0')}",
            identityHash = index.toString().padStart(64, '0'),
            dedupeHash = index.toString().padStart(64, '0'),
            sourceKind = InventorySourceKind.MEDIA_AUDIO,
            sourceAdapter = SourceAdapter.MEDIA_AUDIO,
            sourceApp = null,
            sourceLocator = "media_audio:${index.toString().padStart(16, '0')}",
            displayName = "Fixture $index",
            mimeType = "audio/mpeg",
            sizeBytes = 100,
            width = null,
            height = null,
            durationMs = 1000,
            dateTakenEpochMs = null,
            dateAddedEpochMs = null,
            dateModifiedEpochMs = null,
            captureTimeEpochMs = null,
            captureTimeSource = "unknown",
            directoryHint = null,
            exif = null,
            warningCodes = emptyList(),
            thumbnailAvailable = false,
            observedAtEpochMs = System.currentTimeMillis(),
            contentUri = null,
            normalizedText = null,
            contentSha256 = index.toString().padStart(64, '0'),
        )
    }

    private class CountingProcessor(
        private val delayMs: Long = 40,
    ) : PreprocessingProcessor {
        private val active = AtomicInteger()
        val maximumActive = AtomicInteger()

        override fun capabilities(): Map<String, EngineCapability> = emptyMap()

        override fun process(
            record: StoredPreprocessRecord,
            cancellation: CancellationToken,
        ): PreprocessingRecordUpdate {
            val current = active.incrementAndGet()
            maximumActive.updateAndGet { previous -> maxOf(previous, current) }
            return try {
                Thread.sleep(delayMs)
                if (cancellation.isCancelled()) {
                    PreprocessingRecordUpdate(
                        PreprocessingRecordState.CANCELLED,
                        countedPayload("cancelled"),
                        null,
                        null,
                        null,
                        null,
                    )
                } else {
                    PreprocessingRecordUpdate(
                        PreprocessingRecordState.COMPLETED,
                        countedPayload("completed"),
                        record.normalizedText,
                        record.contentSha256,
                        null,
                        null,
                    )
                }
            } finally {
                active.decrementAndGet()
            }
        }

        override fun close() = Unit
    }

    companion object {
        private const val SESSION_ID = "session_preprocess_001"
        private const val CRAWL_ID = "crawl_preprocess_001"

        private fun countedPayload(state: String): String = JSONObject()
            .put("schema_version", 1)
            .put("status", state)
            .put("warnings", JSONArray())
            .put("exact_hash", JSONObject().put("status", state))
            .toString()
    }
}
