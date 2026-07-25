package com.siksik.agent.source.inventory

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import java.util.UUID

class InventoryStateStore(context: Context) : AutoCloseable {
    private val database = Database(context.applicationContext).writableDatabase

    @Synchronized
    fun createRun(
        crawlId: String,
        sessionId: String,
        mode: InventoryMode,
        documentGrantId: String?,
        sources: List<Pair<SourceAdapter, SourceAvailability>>,
        now: Long,
    ): InventoryRun {
        require(SessionAuthenticator.SAFE_ID.matches(crawlId))
        require(SessionAuthenticator.SAFE_ID.matches(sessionId))
        require(documentGrantId == null || SessionAuthenticator.SAFE_ID.matches(documentGrantId))
        database.beginTransaction()
        try {
            database.insertOrThrow(
                "inventory_runs",
                null,
                ContentValues().apply {
                    put("crawl_id", crawlId)
                    put("session_id", sessionId)
                    put("mode", mode.wireName)
                    put("state", InventoryRunState.READY.wireName)
                    put("document_grant_id", documentGrantId)
                    put("started_at", now)
                    put("updated_at", now)
                    putNull("completed_at")
                    put("cancel_requested", 0)
                },
            )
            sources.forEach { (source, availability) ->
                database.insertOrThrow(
                    "inventory_sources",
                    null,
                    ContentValues().apply {
                        put("crawl_id", crawlId)
                        put("source", source.wireName)
                        put("state", availability.state.wireName)
                        put("scanned_count", 0)
                        put("discovered_count", 0)
                        put("duplicate_count", 0)
                        put("sampled", 0)
                        put("reason", availability.reason)
                        putNull("resume_cursor")
                    },
                )
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        refreshRunState(crawlId, now)
        return getRun(crawlId, sessionId)
    }

    @Synchronized
    fun getRun(crawlId: String, sessionId: String): InventoryRun {
        val row = database.query(
            "inventory_runs",
            RUN_COLUMNS,
            "crawl_id = ? AND session_id = ?",
            arrayOf(crawlId, sessionId),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                throw ApiException("not_found", "Crawl inventory tidak ditemukan.", 404)
            }
            RunRow(
                crawlId = cursor.getString(0),
                sessionId = cursor.getString(1),
                mode = InventoryMode.entries.first { it.wireName == cursor.getString(2) },
                state = InventoryRunState.entries.first { it.wireName == cursor.getString(3) },
                documentGrantId = cursor.getStringOrNull(4),
                startedAt = cursor.getLong(5),
                updatedAt = cursor.getLong(6),
                completedAt = cursor.getLongOrNull(7),
            )
        }
        return InventoryRun(
            crawlId = row.crawlId,
            sessionId = row.sessionId,
            mode = row.mode,
            state = row.state,
            documentGrantId = row.documentGrantId,
            startedAtEpochMs = row.startedAt,
            updatedAtEpochMs = row.updatedAt,
            completedAtEpochMs = row.completedAt,
            sources = sourceProgress(crawlId),
        )
    }

    @Synchronized
    fun latestForSession(sessionId: String): InventoryRun {
        val crawlId = database.query(
            "inventory_runs",
            arrayOf("crawl_id"),
            "session_id = ?",
            arrayOf(sessionId),
            null,
            null,
            "updated_at DESC, rowid DESC",
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                throw ApiException("not_found", "Crawl inventory belum tersedia.", 404)
            }
            cursor.getString(0)
        }
        return getRun(crawlId, sessionId)
    }

    @Synchronized
    fun tryLatestForSession(sessionId: String): InventoryRun? = try {
        latestForSession(sessionId)
    } catch (exception: ApiException) {
        if (exception.code == "not_found") null else throw exception
    }

    @Synchronized
    fun startSource(crawlId: String, source: SourceAdapter, now: Long) {
        updateSource(crawlId, source, ContentValues().apply {
            put("state", InventorySourceState.CRAWLING.wireName)
            putNull("reason")
        })
        updateRun(crawlId, InventoryRunState.CRAWLING, now, null)
    }

    @Synchronized
    fun finishPage(
        crawlId: String,
        source: SourceAdapter,
        state: InventorySourceState,
        scannedDelta: Int,
        discoveredDelta: Int,
        duplicateDelta: Int,
        sampled: Boolean,
        reason: String?,
        resumeCursor: String?,
        now: Long,
    ) {
        require(scannedDelta >= 0 && discoveredDelta >= 0 && duplicateDelta >= 0)
        database.execSQL(
            """
            UPDATE inventory_sources SET
                state = ?,
                scanned_count = scanned_count + ?,
                discovered_count = discovered_count + ?,
                duplicate_count = duplicate_count + ?,
                sampled = ?,
                reason = ?,
                resume_cursor = ?
            WHERE crawl_id = ? AND source = ?
            """.trimIndent(),
            arrayOf<Any?>(
                state.wireName,
                scannedDelta,
                discoveredDelta,
                duplicateDelta,
                if (sampled) 1 else 0,
                reason,
                resumeCursor,
                crawlId,
                source.wireName,
            ),
        )
        refreshRunState(crawlId, now)
    }

    @Synchronized
    fun sourceProgress(crawlId: String): List<InventorySourceProgress> = database.query(
        "inventory_sources",
        SOURCE_COLUMNS,
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        "rowid ASC",
    ).use { cursor ->
        buildList {
            while (cursor.moveToNext()) {
                add(
                    InventorySourceProgress(
                        source = SourceAdapter.entries.first { it.wireName == cursor.getString(0) },
                        state = InventorySourceState.entries.first {
                            it.wireName == cursor.getString(1)
                        },
                        scannedCount = cursor.getInt(2),
                        discoveredCount = cursor.getInt(3),
                        duplicateCount = cursor.getInt(4),
                        sampled = cursor.getInt(5) == 1,
                        reason = cursor.getStringOrNull(6),
                        resumeCursor = cursor.getStringOrNull(7),
                    ),
                )
            }
        }
    }

    @Synchronized
    fun sourceProgress(crawlId: String, source: SourceAdapter): InventorySourceProgress =
        sourceProgress(crawlId).firstOrNull { it.source == source }
            ?: throw ApiException("not_found", "Sumber inventory tidak ditemukan.", 404)

    @Synchronized
    fun registerCursor(crawlId: String, source: SourceAdapter, checkpoint: String, now: Long): String {
        if (checkpoint.isEmpty() || checkpoint.length > MAX_CHECKPOINT_LENGTH) {
            throw ApiException("invalid_cursor", "Checkpoint inventory melewati batas.", 422)
        }
        val cursorId = "cursor_${UUID.randomUUID()}"
        database.insertOrThrow(
            "inventory_cursors",
            null,
            ContentValues().apply {
                put("cursor_id", cursorId)
                put("crawl_id", crawlId)
                put("source", source.wireName)
                put("checkpoint", checkpoint)
                put("created_at", now)
            },
        )
        database.execSQL(
            """
            DELETE FROM inventory_cursors
            WHERE crawl_id = ? AND cursor_id NOT IN (
                SELECT cursor_id FROM inventory_cursors
                WHERE crawl_id = ? ORDER BY created_at DESC LIMIT ?
            )
            """.trimIndent(),
            arrayOf<Any?>(crawlId, crawlId, MAX_CURSORS_PER_RUN),
        )
        return cursorId
    }

    @Synchronized
    fun resolveCursor(crawlId: String, source: SourceAdapter, cursorId: String): String {
        if (!SessionAuthenticator.SAFE_ID.matches(cursorId)) {
            throw ApiException("validation_error", "Cursor inventory tidak valid.", 422)
        }
        val checkpoint = database.query(
            "inventory_cursors",
            arrayOf("checkpoint"),
            "cursor_id = ? AND crawl_id = ? AND source = ?",
            arrayOf(cursorId, crawlId, source.wireName),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                throw ApiException("invalid_cursor", "Cursor inventory tidak tersedia.", 422)
            }
            cursor.getString(0)
        }
        return checkpoint
    }

    @Synchronized
    fun claimIdentity(
        crawlId: String,
        source: SourceAdapter,
        identityHash: String,
        recordId: String,
    ): Boolean {
        val values = ContentValues().apply {
            put("crawl_id", crawlId)
            put("source", source.wireName)
            put("identity_hash", identityHash)
            put("record_id", recordId)
        }
        return database.insertWithOnConflict(
            "inventory_seen",
            null,
            values,
            SQLiteDatabase.CONFLICT_IGNORE,
        ) != -1L
    }

    @Synchronized
    fun requestCancel(crawlId: String, sessionId: String, now: Long): InventoryRun {
        val current = getRun(crawlId, sessionId)
        if (current.state in TERMINAL_RUN_STATES) return current
        database.beginTransaction()
        try {
            database.execSQL(
                "UPDATE inventory_runs SET cancel_requested = 1, state = ?, " +
                    "updated_at = ?, completed_at = ? WHERE crawl_id = ? AND session_id = ?",
                arrayOf<Any?>(
                    InventoryRunState.CANCELLED.wireName,
                    now,
                    now,
                    crawlId,
                    sessionId,
                ),
            )
            database.execSQL(
                "UPDATE inventory_sources SET state = ?, reason = ? " +
                    "WHERE crawl_id = ? AND state IN (?, ?)",
                arrayOf(
                    InventorySourceState.CANCELLED.wireName,
                    "crawl_cancelled",
                    crawlId,
                    InventorySourceState.PENDING.wireName,
                    InventorySourceState.CRAWLING.wireName,
                ),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return getRun(crawlId, sessionId)
    }

    @Synchronized
    fun isCancellationRequested(crawlId: String): Boolean = database.query(
        "inventory_runs",
        arrayOf("cancel_requested"),
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        null,
        "1",
    ).use { cursor -> cursor.moveToFirst() && cursor.getInt(0) == 1 }

    @Synchronized
    fun resume(crawlId: String, sessionId: String, now: Long): InventoryRun {
        val current = getRun(crawlId, sessionId)
        if (current.state != InventoryRunState.CANCELLED) {
            throw ApiException("conflict", "Crawl inventory tidak dalam keadaan batal.", 409)
        }
        database.execSQL(
            "UPDATE inventory_runs SET cancel_requested = 0, state = ?, updated_at = ?, " +
                "completed_at = NULL WHERE crawl_id = ? AND session_id = ?",
            arrayOf<Any?>(InventoryRunState.READY.wireName, now, crawlId, sessionId),
        )
        database.execSQL(
            "UPDATE inventory_sources SET state = ?, reason = NULL " +
                "WHERE crawl_id = ? AND state = ?",
            arrayOf(
                InventorySourceState.PENDING.wireName,
                crawlId,
                InventorySourceState.CANCELLED.wireName,
            ),
        )
        refreshRunState(crawlId, now)
        return getRun(crawlId, sessionId)
    }

    @Synchronized
    fun refresh(crawlId: String, now: Long) {
        refreshRunState(crawlId, now)
    }

    @Synchronized
    fun clearSession(sessionId: String) {
        val ids = mutableListOf<String>()
        database.query(
            "inventory_runs",
            arrayOf("crawl_id"),
            "session_id = ?",
            arrayOf(sessionId),
            null,
            null,
            null,
        ).use { cursor -> while (cursor.moveToNext()) ids.add(cursor.getString(0)) }
        ids.forEach { crawlId ->
            database.delete("inventory_seen", "crawl_id = ?", arrayOf(crawlId))
            database.delete("inventory_cursors", "crawl_id = ?", arrayOf(crawlId))
            database.delete("inventory_sources", "crawl_id = ?", arrayOf(crawlId))
        }
        database.delete("inventory_runs", "session_id = ?", arrayOf(sessionId))
    }

    override fun close() {
        database.close()
    }

    private fun refreshRunState(crawlId: String, now: Long) {
        val states = sourceProgress(crawlId).map(InventorySourceProgress::state)
        val pending = states.any {
            it == InventorySourceState.PENDING || it == InventorySourceState.CRAWLING
        }
        val runState = when {
            isCancellationRequested(crawlId) -> InventoryRunState.CANCELLED
            pending -> InventoryRunState.CRAWLING
            states.isNotEmpty() && states.all { it == InventorySourceState.FAILED } ->
                InventoryRunState.FAILED
            states.any {
                it in setOf(
                    InventorySourceState.PARTIAL,
                    InventorySourceState.DENIED,
                    InventorySourceState.RESTRICTED,
                    InventorySourceState.UNSUPPORTED,
                    InventorySourceState.FAILED,
                )
            } -> InventoryRunState.PARTIAL
            else -> InventoryRunState.COMPLETE
        }
        updateRun(
            crawlId,
            runState,
            now,
            now.takeIf { runState in TERMINAL_RUN_STATES },
        )
    }

    private fun updateRun(
        crawlId: String,
        state: InventoryRunState,
        now: Long,
        completedAt: Long?,
    ) {
        database.update(
            "inventory_runs",
            ContentValues().apply {
                put("state", state.wireName)
                put("updated_at", now)
                if (completedAt == null) putNull("completed_at") else put("completed_at", completedAt)
            },
            "crawl_id = ?",
            arrayOf(crawlId),
        )
    }

    private fun updateSource(crawlId: String, source: SourceAdapter, values: ContentValues) {
        database.update(
            "inventory_sources",
            values,
            "crawl_id = ? AND source = ?",
            arrayOf(crawlId, source.wireName),
        )
    }

    private fun android.database.Cursor.getStringOrNull(index: Int): String? =
        if (isNull(index)) null else getString(index)

    private fun android.database.Cursor.getLongOrNull(index: Int): Long? =
        if (isNull(index)) null else getLong(index)

    private data class RunRow(
        val crawlId: String,
        val sessionId: String,
        val mode: InventoryMode,
        val state: InventoryRunState,
        val documentGrantId: String?,
        val startedAt: Long,
        val updatedAt: Long,
        val completedAt: Long?,
    )

    private class Database(context: Context) : SQLiteOpenHelper(
        context,
        "siksik_inventory.db",
        null,
        DATABASE_VERSION,
    ) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE inventory_runs (
                    crawl_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    document_grant_id TEXT,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE inventory_sources (
                    crawl_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    scanned_count INTEGER NOT NULL DEFAULT 0,
                    discovered_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    sampled INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    resume_cursor TEXT,
                    PRIMARY KEY (crawl_id, source)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE inventory_cursors (
                    cursor_id TEXT PRIMARY KEY,
                    crawl_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checkpoint TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE inventory_seen (
                    crawl_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    identity_hash TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    PRIMARY KEY (crawl_id, identity_hash)
                )
                """.trimIndent(),
            )
            db.execSQL("CREATE INDEX inventory_runs_session ON inventory_runs(session_id, updated_at)")
            db.execSQL("CREATE INDEX inventory_cursors_run ON inventory_cursors(crawl_id, created_at)")
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            if (oldVersion != newVersion) {
                throw IllegalStateException("Unsupported inventory database migration")
            }
        }
    }

    companion object {
        private const val DATABASE_VERSION = 1
        private const val MAX_CURSORS_PER_RUN = 64
        private const val MAX_CHECKPOINT_LENGTH = 8 * 1024 * 1024
        private val TERMINAL_RUN_STATES = setOf(
            InventoryRunState.COMPLETE,
            InventoryRunState.PARTIAL,
            InventoryRunState.CANCELLED,
            InventoryRunState.FAILED,
        )
        private val RUN_COLUMNS = arrayOf(
            "crawl_id",
            "session_id",
            "mode",
            "state",
            "document_grant_id",
            "started_at",
            "updated_at",
            "completed_at",
        )
        private val SOURCE_COLUMNS = arrayOf(
            "source",
            "state",
            "scanned_count",
            "discovered_count",
            "duplicate_count",
            "sampled",
            "reason",
            "resume_cursor",
        )
    }
}
