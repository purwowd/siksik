package com.siksik.agent

import android.content.Context
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.source.inventory.AdapterPage
import com.siksik.agent.source.inventory.InventoryController
import com.siksik.agent.source.inventory.InventoryMode
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventoryRun
import com.siksik.agent.source.inventory.InventoryRunState
import com.siksik.agent.source.inventory.InventorySource
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.InventorySourceState
import com.siksik.agent.source.inventory.SourceAdapter
import com.siksik.agent.source.inventory.SourceAvailability
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class InventoryControllerTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun fullModePaginatesEverySourceAndDeduplicatesOverlappingVisibility() {
        val sharedIdentity = "identity_shared"
        val sources = SourceAdapter.entries.map { adapter ->
            when (adapter) {
                SourceAdapter.PUBLIC_WHATSAPP -> FakeSource(
                    adapter,
                    listOf(record(adapter, "wa", sharedIdentity), record(adapter, "wa-two")),
                )
                SourceAdapter.MEDIA_IMAGE -> FakeSource(
                    adapter,
                    listOf(record(adapter, "image-duplicate", sharedIdentity)),
                )
                else -> FakeSource(adapter, emptyList())
            }
        }
        withController(sources) { controller, sessionId ->
            val started = controller.start(sessionId, InventoryMode.FULL, null)
            val completed = exhaust(controller, started, pageSize = 1)

            assertEquals(InventoryRunState.COMPLETE, completed.state)
            assertEquals(2, completed.sources.sumOf { it.discoveredCount })
            assertEquals(1, completed.sources.sumOf { it.duplicateCount })
            assertTrue(sources.all { (it as FakeSource).maxRequestedLimit <= 1 })
        }
    }

    @Test
    fun quickModeStopsAtDocumentedBoundWithoutGrowingPageMemory() {
        val sources = SourceAdapter.entries.map { adapter ->
            val records = if (adapter == SourceAdapter.PUBLIC_WHATSAPP) {
                (0 until 1_000).map { record(adapter, "record-$it") }
            } else {
                emptyList()
            }
            FakeSource(adapter, records)
        }
        withController(sources) { controller, sessionId ->
            val completed = exhaust(
                controller,
                controller.start(sessionId, InventoryMode.QUICK, null),
                pageSize = 100,
            )
            val sampled = completed.sources.first {
                it.source == SourceAdapter.PUBLIC_WHATSAPP
            }

            assertEquals(InventoryRunState.COMPLETE, completed.state)
            assertEquals(BuildConfig.QUICK_INVENTORY_ITEMS_PER_SOURCE, sampled.discoveredCount)
            assertTrue(sampled.sampled)
            assertEquals("quick_sample_limit", sampled.reason)
            assertTrue(sources.all { (it as FakeSource).maxRequestedLimit <= 100 })
        }
    }

    @Test
    fun cancellationPreservesResumeStateAndCanContinue() {
        val sources = SourceAdapter.entries.map { adapter ->
            FakeSource(adapter, listOf(record(adapter, adapter.wireName)))
        }
        withController(sources) { controller, sessionId ->
            val started = controller.start(sessionId, InventoryMode.FULL, null)
            val cancelled = controller.cancel(sessionId, started.crawlId)
            assertEquals(InventoryRunState.CANCELLED, cancelled.state)
            assertTrue(cancelled.sources.all { it.state == InventorySourceState.CANCELLED })

            val resumed = controller.resume(sessionId, started.crawlId)
            assertEquals(InventoryRunState.CRAWLING, resumed.state)
            val completed = exhaust(controller, resumed, pageSize = 2)
            assertEquals(InventoryRunState.COMPLETE, completed.state)
            assertEquals(SourceAdapter.entries.size, completed.sources.sumOf { it.discoveredCount })
        }
    }

    @Test
    fun deniedRestrictedAndUnsupportedSourcesProducePartialRun() {
        val states = listOf(
            InventorySourceState.DENIED,
            InventorySourceState.RESTRICTED,
            InventorySourceState.UNSUPPORTED,
        )
        val sources = SourceAdapter.entries.mapIndexed { index, adapter ->
            val state = states.getOrNull(index)
            FakeSource(
                adapter,
                emptyList(),
                state?.let { SourceAvailability(it, "fixture_${it.wireName}") }
                    ?: SourceAvailability(InventorySourceState.PENDING),
            )
        }
        withController(sources) { controller, sessionId ->
            val completed = exhaust(
                controller,
                controller.start(sessionId, InventoryMode.FULL, null),
                pageSize = 10,
            )

            assertEquals(InventoryRunState.PARTIAL, completed.state)
            assertEquals(InventorySourceState.DENIED, completed.sources[0].state)
            assertEquals(InventorySourceState.RESTRICTED, completed.sources[1].state)
            assertEquals(InventorySourceState.UNSUPPORTED, completed.sources[2].state)
        }
    }

    @Test
    fun permissionRevocationAndProviderDisappearanceKeepExactReasonAndCursor() {
        val sources = SourceAdapter.entries.map { adapter ->
            when (adapter) {
                SourceAdapter.MEDIA_AUDIO -> FakeSource(
                    adapter,
                    emptyList(),
                    pageTerminalState = InventorySourceState.DENIED,
                    terminalReason = "runtime_permission_revoked",
                )
                SourceAdapter.DOCUMENT_TREE -> FakeSource(
                    adapter,
                    listOf(record(adapter, "tree-record")),
                    pageTerminalState = InventorySourceState.PARTIAL,
                    terminalReason = "document_provider_disappeared",
                    terminalCheckpoint = "tree-resume",
                )
                else -> FakeSource(adapter, emptyList())
            }
        }
        withController(sources) { controller, sessionId ->
            val completed = exhaust(
                controller,
                controller.start(sessionId, InventoryMode.FULL, null),
                pageSize = 10,
            )
            val audio = completed.sources.first { it.source == SourceAdapter.MEDIA_AUDIO }
            val tree = completed.sources.first { it.source == SourceAdapter.DOCUMENT_TREE }

            assertEquals(InventoryRunState.PARTIAL, completed.state)
            assertEquals(InventorySourceState.DENIED, audio.state)
            assertEquals("runtime_permission_revoked", audio.reason)
            assertEquals(InventorySourceState.PARTIAL, tree.state)
            assertEquals("document_provider_disappeared", tree.reason)
            assertTrue(tree.resumeCursor?.startsWith("cursor_") == true)
        }
    }

    private fun exhaust(
        controller: InventoryController,
        started: InventoryRun,
        pageSize: Int,
    ): InventoryRun {
        started.sources.forEach { source ->
            if (source.state !in setOf(InventorySourceState.PENDING, InventorySourceState.CRAWLING)) {
                return@forEach
            }
            var cursor = source.resumeCursor
            while (true) {
                val page = controller.page(
                    started.sessionId,
                    started.crawlId,
                    source.source,
                    cursor,
                    pageSize,
                )
                if (page.sourceState != InventorySourceState.CRAWLING) break
                cursor = requireNotNull(page.nextCursor)
            }
        }
        return controller.status(started.sessionId, started.crawlId)
    }

    private fun withController(
        sources: List<InventorySource>,
        block: (InventoryController, String) -> Unit,
    ) {
        val sessionId = "session_${UUID.randomUUID()}"
        InventoryController(context, GrantGateway(context), sources = sources).use { controller ->
            try {
                block(controller, sessionId)
            } finally {
                controller.clearSession(sessionId)
            }
        }
    }

    private fun record(
        adapter: SourceAdapter,
        suffix: String,
        identity: String = "identity_$suffix",
    ) = InventoryRecord(
        recordId = "record_$suffix",
        identityHash = identity,
        dedupeHash = identity,
        sourceKind = InventorySourceKind.MEDIA_IMAGE,
        sourceAdapter = adapter,
        sourceApp = null,
        sourceLocator = "${adapter.wireName}:$suffix",
        displayName = "$suffix.jpg",
        mimeType = "image/jpeg",
        sizeBytes = 100,
        width = 10,
        height = 10,
        durationMs = null,
        dateTakenEpochMs = 1_700_000_000_000,
        dateAddedEpochMs = 1_700_000_000_000,
        dateModifiedEpochMs = 1_700_000_000_000,
        captureTimeEpochMs = 1_700_000_000_000,
        captureTimeSource = "date_taken",
        directoryHint = "Pictures/Fixture",
        exif = null,
        warningCodes = emptyList(),
        thumbnailAvailable = true,
        observedAtEpochMs = 1_700_000_000_000,
        contentUri = Uri.parse("content://fixture/$suffix"),
    )

    private class FakeSource(
        override val adapter: SourceAdapter,
        private val records: List<InventoryRecord>,
        private val sourceAvailability: SourceAvailability = SourceAvailability(
            InventorySourceState.PENDING,
        ),
        private val pageTerminalState: InventorySourceState = InventorySourceState.COMPLETE,
        private val terminalReason: String? = null,
        private val terminalCheckpoint: String? = null,
    ) : InventorySource {
        var maxRequestedLimit = 0
            private set

        override fun availability(
            sessionId: String,
            documentGrantId: String?,
        ): SourceAvailability = sourceAvailability

        override fun page(
            sessionId: String,
            documentGrantId: String?,
            checkpoint: String?,
            limit: Int,
            isCancelled: () -> Boolean,
        ): AdapterPage {
            maxRequestedLimit = maxOf(maxRequestedLimit, limit)
            if (isCancelled()) {
                return AdapterPage(
                    emptyList(),
                    checkpoint,
                    0,
                    InventorySourceState.CANCELLED,
                    "crawl_cancelled",
                )
            }
            val offset = checkpoint?.toInt() ?: 0
            val page = records.drop(offset).take(limit)
            val nextOffset = offset + page.size
            val next = nextOffset.toString().takeIf { nextOffset < records.size }
                ?: terminalCheckpoint
            return AdapterPage(
                page,
                next,
                page.size,
                pageTerminalState,
                terminalReason,
            )
        }
    }
}
