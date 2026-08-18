package com.siksik.agent.preprocessing

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.inventory.InventoryRecord
import com.siksik.agent.source.inventory.InventoryRecordJson
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

enum class PreprocessingRunState(val wireName: String) {
    RUNNING("running"),
    COMPLETE("complete"),
    PARTIAL("partial"),
    CANCELLED("cancelled"),
    FAILED("failed"),
}

enum class PreprocessingRecordState(val wireName: String) {
    PENDING("pending"),
    PROCESSING("processing"),
    COMPLETED("completed"),
    SKIPPED("skipped"),
    TRUNCATED("truncated"),
    FAILED("failed"),
    CANCELLED("cancelled"),
}

data class PreprocessingTotals(
    val total: Int,
    val pending: Int,
    val processing: Int,
    val completed: Int,
    val skipped: Int,
    val truncated: Int,
    val failed: Int,
    val cancelled: Int,
) {
    val terminal: Int
        get() = completed + skipped + truncated + failed + cancelled
}

data class PreprocessorTotals(
    val attempted: Int,
    val processed: Int,
    val skipped: Int,
    val truncated: Int,
    val failed: Int,
    val cancelled: Int,
)

data class PreprocessingRun(
    val crawlId: String,
    val sessionId: String,
    val state: PreprocessingRunState,
    val startedAtEpochMs: Long,
    val updatedAtEpochMs: Long,
    val completedAtEpochMs: Long?,
    val deadlineAtEpochMs: Long,
    val totals: PreprocessingTotals,
    val preprocessorTotals: Map<String, PreprocessorTotals>,
    val partialReasons: List<String>,
)

data class StoredPreprocessRecord(
    val sessionId: String,
    val crawlId: String,
    val recordId: String,
    val sourceKind: String,
    val mimeType: String,
    val sizeBytes: Long?,
    val width: Int?,
    val height: Int?,
    val contentUri: String?,
    val normalizedText: String?,
    val contentSha256: String?,
    val attachmentIds: List<String>,
    val displayName: String? = null,
    val directoryHint: String? = null,
)

data class PreprocessingRecordUpdate(
    val state: PreprocessingRecordState,
    val preprocessingJson: String,
    val normalizedText: String?,
    val contentSha256: String?,
    val perceptualHash: String?,
    val faceVectorsJson: String?,
)

data class PreprocessedRecordPage(
    val records: List<JSONObject>,
    val nextCursor: String?,
)

data class TransferPreprocessedRecord(
    val recordId: String,
    val sourceKind: String,
    val mimeType: String,
    val sizeBytes: Long?,
    val contentUri: String?,
    val displayName: String,
    val attachmentIds: List<String>,
    val payload: JSONObject,
)

data class PreprocessedSelectionInput(
    val recordId: String,
    val sourceKind: String,
    val sourceApp: String?,
    val socialScope: String?,
    val normalizedText: String?,
    val preprocessingJson: String,
    val sizeBytes: Long?,
    val thumbnailAvailable: Boolean,
)

data class PreprocessedSelectionPage(
    val records: List<PreprocessedSelectionInput>,
    val nextRecordId: String?,
)

fun interface InventoryRecordSink {
    fun persist(
        sessionId: String,
        crawlId: String,
        records: List<InventoryRecord>,
        now: Long,
    )

    companion object {
        val NONE = InventoryRecordSink { _, _, _, _ -> }
    }
}

class PreprocessingStore(context: Context) : InventoryRecordSink, AutoCloseable {
    private val database = Database(context.applicationContext).writableDatabase

    @Synchronized
    override fun persist(
        sessionId: String,
        crawlId: String,
        records: List<InventoryRecord>,
        now: Long,
    ) {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        if (records.isEmpty()) return
        database.beginTransaction()
        try {
            records.forEach { record ->
                requireSafeId(record.recordId)
                val recordJson = InventoryRecordJson.encode(sessionId, crawlId, record).toString()
                require(recordJson.toByteArray(Charsets.UTF_8).size <= MAX_RECORD_JSON_BYTES)
                database.insertWithOnConflict(
                    "preprocessing_records",
                    null,
                    ContentValues().apply {
                        put("session_id", sessionId)
                        put("crawl_id", crawlId)
                        put("record_id", record.recordId)
                        put("source_kind", record.sourceKind.wireName)
                        put("mime_type", record.mimeType)
                        put("size_bytes", record.sizeBytes)
                        put("width", record.width)
                        put("height", record.height)
                        put("content_uri", record.contentUri?.toString())
                        put("base_record_json", recordJson)
                        put("original_text", record.normalizedText)
                        put("original_sha256", record.contentSha256)
                        put("state", PreprocessingRecordState.PENDING.wireName)
                        put("selection_state", SELECTION_BLOCKED)
                        putNull("preprocessing_json")
                        put("normalized_text", record.normalizedText)
                        put("content_sha256", record.contentSha256)
                        putNull("perceptual_hash")
                        putNull("face_vectors_json")
                        put("attempt_count", 0)
                        put("updated_at", now)
                    },
                    SQLiteDatabase.CONFLICT_IGNORE,
                )
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun startOrResume(
        sessionId: String,
        crawlId: String,
        startedAt: Long,
        deadlineAt: Long,
    ): PreprocessingRun {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        val total = recordCount(sessionId, crawlId)
        if (total == 0) {
            throw ApiException("preprocessing_empty", "Record crawl belum tersedia.", 409)
        }
        database.beginTransaction()
        try {
            database.execSQL(
                "UPDATE preprocessing_records SET state = ?, updated_at = ? " +
                    "WHERE session_id = ? AND crawl_id = ? AND state = ?",
                arrayOf<Any?>(
                    PreprocessingRecordState.PENDING.wireName,
                    startedAt,
                    sessionId,
                    crawlId,
                    PreprocessingRecordState.PROCESSING.wireName,
                ),
            )
            database.execSQL(
                "UPDATE preprocessing_records SET selection_state = ?, updated_at = ? " +
                    "WHERE session_id = ? AND crawl_id = ? AND selection_state = ?",
                arrayOf<Any?>(
                    SELECTION_PENDING,
                    startedAt,
                    sessionId,
                    crawlId,
                    SELECTION_PROCESSING,
                ),
            )
            val existing = runOrNull(sessionId, crawlId)
            if (existing == null) {
                database.insertOrThrow(
                    "preprocessing_runs",
                    null,
                    ContentValues().apply {
                        put("crawl_id", crawlId)
                        put("session_id", sessionId)
                        put("state", PreprocessingRunState.RUNNING.wireName)
                        put("started_at", startedAt)
                        put("updated_at", startedAt)
                        putNull("completed_at")
                        put("deadline_at", deadlineAt)
                        put("cancel_requested", 0)
                        put("partial_reasons", "[]")
                    },
                )
            } else if (existing.state !in TERMINAL_RUN_STATES) {
                database.execSQL(
                    "UPDATE preprocessing_runs SET state = ?, updated_at = ?, " +
                        "deadline_at = ?, cancel_requested = 0 WHERE crawl_id = ? AND session_id = ?",
                    arrayOf<Any?>(
                        PreprocessingRunState.RUNNING.wireName,
                        startedAt,
                        maxOf(existing.deadlineAtEpochMs, deadlineAt),
                        crawlId,
                        sessionId,
                    ),
                )
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun getRun(sessionId: String, crawlId: String): PreprocessingRun =
        runOrNull(sessionId, crawlId)
            ?: throw ApiException("not_found", "Preprocessing crawl belum tersedia.", 404)

    @Synchronized
    fun claimPending(crawlId: String, limit: Int, now: Long): List<StoredPreprocessRecord> {
        require(limit in 1..32)
        val rows = mutableListOf<StoredPreprocessRecord>()
        database.query(
            "preprocessing_records",
            STORED_COLUMNS,
            "crawl_id = ? AND state = ?",
            arrayOf(crawlId, PreprocessingRecordState.PENDING.wireName),
            null,
            null,
            "CASE source_kind " +
                "WHEN 'visible_ui' THEN 0 " +
                "WHEN 'sms' THEN 1 " +
                "WHEN 'notification' THEN 2 " +
                "WHEN 'contact' THEN 3 " +
                "WHEN 'document' THEN 4 " +
                "WHEN 'media_image' THEN 5 " +
                "WHEN 'media_video' THEN 6 " +
                "WHEN 'media_audio' THEN 7 ELSE 8 END, record_id",
            limit.toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val baseJson = cursor.getString(11)
                val origin = originFromBaseJson(baseJson)
                rows.add(
                    StoredPreprocessRecord(
                        cursor.getString(0),
                        cursor.getString(1),
                        cursor.getString(2),
                        cursor.getString(3),
                        cursor.getString(4),
                        cursor.longOrNull(5),
                        cursor.intOrNull(6),
                        cursor.intOrNull(7),
                        cursor.stringOrNull(8),
                        cursor.stringOrNull(9),
                        cursor.stringOrNull(10),
                        jsonStrings(
                            JSONObject(baseJson)
                                .getJSONArray("attachment_ids")
                                .toString(),
                        ),
                        origin.first,
                        origin.second,
                    ),
                )
            }
        }
        if (rows.isEmpty()) return rows
        database.beginTransaction()
        try {
            rows.forEach { row ->
                database.execSQL(
                    "UPDATE preprocessing_records SET state = ?, attempt_count = attempt_count + 1, " +
                        "updated_at = ? WHERE crawl_id = ? AND record_id = ? AND state = ?",
                    arrayOf<Any?>(
                        PreprocessingRecordState.PROCESSING.wireName,
                        now,
                        row.crawlId,
                        row.recordId,
                        PreprocessingRecordState.PENDING.wireName,
                    ),
                )
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return rows
    }

    @Synchronized
    fun completeRecord(
        crawlId: String,
        recordId: String,
        update: PreprocessingRecordUpdate,
        now: Long,
    ) {
        require(update.state in TERMINAL_RECORD_STATES)
        require(update.preprocessingJson.toByteArray(Charsets.UTF_8).size <= MAX_PREPROCESS_JSON_BYTES)
        database.beginTransaction()
        try {
            val changed = database.update(
                "preprocessing_records",
                ContentValues().apply {
                    put("state", update.state.wireName)
                    put("preprocessing_json", update.preprocessingJson)
                    put("normalized_text", update.normalizedText)
                    put("content_sha256", update.contentSha256)
                    put("perceptual_hash", update.perceptualHash)
                    put("face_vectors_json", update.faceVectorsJson)
                    put("updated_at", now)
                },
                "crawl_id = ? AND record_id = ? AND state = ?",
                arrayOf(crawlId, recordId, PreprocessingRecordState.PROCESSING.wireName),
            )
            if (changed == 1) {
                database.execSQL(
                    "UPDATE preprocessing_records SET selection_state = " +
                        "CASE WHEN source_kind IN (?, ?, ?, ?, ?) THEN ? ELSE ? END " +
                        "WHERE crawl_id = ? AND record_id = ?",
                    arrayOf<Any?>(
                        "sms",
                        "visible_ui",
                        "notification",
                        "contact",
                        "document",
                        SELECTION_PENDING,
                        SELECTION_BLOCKED,
                        crawlId,
                        recordId,
                    ),
                )
                updatePreprocessorTotals(crawlId, update.preprocessingJson)
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun markTimedOut(crawlId: String, recordId: String, now: Long) {
        val payload = JSONObject()
            .put("schema_version", 1)
            .put("status", "failed")
            .put("warnings", JSONArray(listOf("preprocessing_timeout")))
            .toString()
        completeRecord(
            crawlId,
            recordId,
            PreprocessingRecordUpdate(
                PreprocessingRecordState.FAILED,
                payload,
                null,
                null,
                null,
                null,
            ),
            now,
        )
    }

    @Synchronized
    fun markRemaining(
        sessionId: String,
        crawlId: String,
        state: PreprocessingRecordState,
        reason: String,
        now: Long,
    ) {
        require(state in setOf(PreprocessingRecordState.SKIPPED, PreprocessingRecordState.CANCELLED))
        require(SAFE_REASON.matches(reason))
        val payload = JSONObject()
            .put("schema_version", 1)
            .put("status", state.wireName)
            .put("warnings", JSONArray(listOf(reason)))
            .toString()
        database.execSQL(
            "UPDATE preprocessing_records SET state = ?, preprocessing_json = ?, " +
                "selection_state = ?, updated_at = ? " +
                "WHERE session_id = ? AND crawl_id = ? AND state IN (?, ?)",
            arrayOf<Any?>(
                state.wireName,
                payload,
                SELECTION_PENDING,
                now,
                sessionId,
                crawlId,
                PreprocessingRecordState.PENDING.wireName,
                PreprocessingRecordState.PROCESSING.wireName,
            ),
        )
    }

    @Synchronized
    fun requestCancel(sessionId: String, crawlId: String, now: Long): PreprocessingRun {
        val current = getRun(sessionId, crawlId)
        if (current.state in TERMINAL_RUN_STATES) return current
        val payload = JSONObject()
            .put("schema_version", 1)
            .put("status", PreprocessingRecordState.CANCELLED.wireName)
            .put("warnings", JSONArray(listOf("cancelled")))
            .toString()
        database.beginTransaction()
        try {
            database.execSQL(
                "UPDATE preprocessing_runs SET cancel_requested = 1, state = ?, updated_at = ?, " +
                    "completed_at = ?, partial_reasons = ? " +
                    "WHERE session_id = ? AND crawl_id = ?",
                arrayOf<Any?>(
                    PreprocessingRunState.CANCELLED.wireName,
                    now,
                    now,
                    JSONArray(listOf("cancelled")).toString(),
                    sessionId,
                    crawlId,
                ),
            )
            database.execSQL(
                "UPDATE preprocessing_records SET state = ?, preprocessing_json = ?, " +
                    "selection_state = ?, " +
                    "updated_at = ? WHERE session_id = ? AND crawl_id = ? AND state IN (?, ?)",
                arrayOf<Any?>(
                    PreprocessingRecordState.CANCELLED.wireName,
                    payload,
                    SELECTION_PENDING,
                    now,
                    sessionId,
                    crawlId,
                    PreprocessingRecordState.PENDING.wireName,
                    PreprocessingRecordState.PROCESSING.wireName,
                ),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun isCancelRequested(crawlId: String): Boolean = database.query(
        "preprocessing_runs",
        arrayOf("cancel_requested"),
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        null,
        "1",
    ).use { cursor -> cursor.moveToFirst() && cursor.getInt(0) == 1 }

    @Synchronized
    fun finishRun(
        sessionId: String,
        crawlId: String,
        state: PreprocessingRunState,
        reasons: List<String>,
        now: Long,
    ): PreprocessingRun {
        require(state in TERMINAL_RUN_STATES)
        val current = getRun(sessionId, crawlId)
        if (current.state in TERMINAL_RUN_STATES) return current
        val safeReasons = reasons.distinct().sorted().also { values ->
            require(values.size <= 32 && values.all(SAFE_REASON::matches))
        }
        database.execSQL(
            "UPDATE preprocessing_runs SET state = ?, updated_at = ?, completed_at = ?, " +
                "partial_reasons = ? WHERE session_id = ? AND crawl_id = ?",
            arrayOf<Any?>(
                state.wireName,
                now,
                now,
                JSONArray(safeReasons).toString(),
                sessionId,
                crawlId,
            ),
        )
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun clusteringSignals(crawlId: String): List<StoredClusterSignals> {
        val values = mutableListOf<StoredClusterSignals>()
        database.query(
            "preprocessing_records",
            arrayOf(
                "record_id",
                "content_sha256",
                "perceptual_hash",
                "width",
                "height",
                "size_bytes",
                "face_vectors_json",
            ),
            "crawl_id = ? AND state IN (?, ?)",
            arrayOf(
                crawlId,
                PreprocessingRecordState.COMPLETED.wireName,
                PreprocessingRecordState.TRUNCATED.wireName,
            ),
            null,
            null,
            "record_id",
        ).use { cursor ->
            while (cursor.moveToNext()) {
                values.add(
                    StoredClusterSignals(
                        cursor.getString(0),
                        cursor.stringOrNull(1),
                        cursor.stringOrNull(2),
                        cursor.intOrNull(3),
                        cursor.intOrNull(4),
                        cursor.longOrNull(5),
                        cursor.stringOrNull(6),
                    ),
                )
            }
        }
        return values
    }

    @Synchronized
    fun applyMemberships(
        crawlId: String,
        duplicates: Map<String, DuplicateMembership>,
        faces: Map<String, FaceClusterMembership>,
        now: Long,
    ) {
        val records = mutableListOf<Pair<String, String>>()
        database.query(
            "preprocessing_records",
            arrayOf("record_id", "preprocessing_json"),
            "crawl_id = ? AND preprocessing_json IS NOT NULL",
            arrayOf(crawlId),
            null,
            null,
            null,
        ).use { cursor ->
            while (cursor.moveToNext()) {
                records.add(cursor.getString(0) to cursor.getString(1))
            }
        }
        database.beginTransaction()
        try {
            records.forEach { (recordId, encodedPayload) ->
                val payload = JSONObject(encodedPayload)
                duplicates[recordId]?.let { value ->
                    payload.put(
                        "duplicate_membership",
                        JSONObject()
                            .put("exact_group_id", value.exactGroupId ?: JSONObject.NULL)
                            .put(
                                "perceptual_group_id",
                                value.perceptualGroupId ?: JSONObject.NULL,
                            )
                            .put(
                                "representative_record_id",
                                value.representativeRecordId ?: JSONObject.NULL,
                            ),
                    )
                }
                faces[recordId]?.let { value ->
                    payload.put("face_cluster_ids", JSONArray(value.clusterIds))
                }
                database.update(
                    "preprocessing_records",
                    ContentValues().apply {
                        put("preprocessing_json", payload.toString())
                        putNull("face_vectors_json")
                        put("updated_at", now)
                    },
                    "crawl_id = ? AND record_id = ?",
                    arrayOf(crawlId, recordId),
                )
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun releaseSelectionInputs(crawlId: String, now: Long) {
        requireSafeId(crawlId)
        database.execSQL(
            "UPDATE preprocessing_records SET selection_state = ?, updated_at = ? " +
                "WHERE crawl_id = ? AND preprocessing_json IS NOT NULL " +
                "AND selection_state = ?",
            arrayOf<Any?>(SELECTION_PENDING, now, crawlId, SELECTION_BLOCKED),
        )
    }

    @Synchronized
    fun resetClaimedSelectionInputs(sessionId: String, crawlId: String, now: Long) {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        database.execSQL(
            "UPDATE preprocessing_records SET selection_state = ?, updated_at = ? " +
                "WHERE session_id = ? AND crawl_id = ? AND selection_state = ?",
            arrayOf<Any?>(
                SELECTION_PENDING,
                now,
                sessionId,
                crawlId,
                SELECTION_PROCESSING,
            ),
        )
    }

    @Synchronized
    fun claimSelectionInputs(
        sessionId: String,
        crawlId: String,
        limit: Int,
        now: Long,
    ): List<PreprocessedSelectionInput> {
        require(limit in 1..MAX_SELECTION_INPUT_PAGE_SIZE)
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        val output = mutableListOf<PreprocessedSelectionInput>()
        database.query(
            "preprocessing_records",
            arrayOf(
                "record_id",
                "source_kind",
                "base_record_json",
                "normalized_text",
                "preprocessing_json",
                "size_bytes",
            ),
            "session_id = ? AND crawl_id = ? AND preprocessing_json IS NOT NULL " +
                "AND selection_state = ?",
            arrayOf(sessionId, crawlId, SELECTION_PENDING),
            null,
            null,
            "record_id",
            limit.toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val base = JSONObject(cursor.getString(2))
                val metadata = base.getJSONObject("metadata")
                output.add(
                    PreprocessedSelectionInput(
                        recordId = cursor.getString(0),
                        sourceKind = cursor.getString(1),
                        sourceApp = base.nullableString("source_app"),
                        socialScope = metadata.nullableString("social_scope"),
                        normalizedText = cursor.stringOrNull(3),
                        preprocessingJson = cursor.getString(4),
                        sizeBytes = cursor.longOrNull(5),
                        thumbnailAvailable = metadata.optBoolean("thumbnail_available", false) ||
                            (metadata.optJSONArray("screenshot_ids")?.length() ?: 0) > 0,
                    ),
                )
            }
        }
        if (output.isEmpty()) return output
        database.beginTransaction()
        try {
            output.forEach { record ->
                val changed = database.update(
                    "preprocessing_records",
                    ContentValues().apply {
                        put("selection_state", SELECTION_PROCESSING)
                        put("updated_at", now)
                    },
                    "session_id = ? AND crawl_id = ? AND record_id = ? " +
                        "AND selection_state = ?",
                    arrayOf(sessionId, crawlId, record.recordId, SELECTION_PENDING),
                )
                check(changed == 1) { "selection_input_claim_conflict" }
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return output
    }

    @Synchronized
    fun markSelectionInputsEvaluated(
        sessionId: String,
        crawlId: String,
        recordIds: List<String>,
        now: Long,
    ) {
        if (recordIds.isEmpty()) return
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        database.beginTransaction()
        try {
            recordIds.forEach { recordId ->
                requireSafeId(recordId)
                val changed = database.update(
                    "preprocessing_records",
                    ContentValues().apply {
                        put("selection_state", SELECTION_EVALUATED)
                        put("updated_at", now)
                    },
                    "session_id = ? AND crawl_id = ? AND record_id = ? " +
                        "AND selection_state = ?",
                    arrayOf(sessionId, crawlId, recordId, SELECTION_PROCESSING),
                )
                check(changed == 1) { "selection_input_completion_conflict" }
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun selectionInputsRemaining(sessionId: String, crawlId: String): Int {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        return database.rawQuery(
            "SELECT COUNT(*) FROM preprocessing_records WHERE session_id = ? " +
                "AND crawl_id = ? AND selection_state != ?",
            arrayOf(sessionId, crawlId, SELECTION_EVALUATED),
        ).use { cursor ->
            check(cursor.moveToFirst())
            cursor.getInt(0)
        }
    }

    @Synchronized
    fun recordPage(
        sessionId: String,
        crawlId: String,
        cursorId: String?,
        limit: Int,
        now: Long,
    ): PreprocessedRecordPage {
        require(limit in 1..MAX_RESULT_PAGE_SIZE)
        getRun(sessionId, crawlId)
        val after = cursorId?.let { resolveCursor(sessionId, crawlId, it) }
        val records = mutableListOf<Pair<String, JSONObject>>()
        val selection = if (after == null) {
            "session_id = ? AND crawl_id = ? AND preprocessing_json IS NOT NULL"
        } else {
            "session_id = ? AND crawl_id = ? AND preprocessing_json IS NOT NULL AND record_id > ?"
        }
        val args = if (after == null) {
            arrayOf(sessionId, crawlId)
        } else {
            arrayOf(sessionId, crawlId, after)
        }
        database.query(
            "preprocessing_records",
            arrayOf(
                "record_id",
                "base_record_json",
                "normalized_text",
                "content_sha256",
                "preprocessing_json",
            ),
            selection,
            args,
            null,
            null,
            "record_id",
            (limit + 1).toString(),
        ).use { values ->
            while (values.moveToNext()) {
                records.add(
                    values.getString(0) to InventoryRecordJson.withPreprocessing(
                        values.getString(1),
                        values.stringOrNull(2),
                        values.stringOrNull(3),
                        values.getString(4),
                    ),
                )
            }
        }
        val hasMore = records.size > limit
        val page = records.take(limit)
        val next = if (hasMore && page.isNotEmpty()) {
            registerCursor(sessionId, crawlId, page.last().first, now)
        } else {
            null
        }
        return PreprocessedRecordPage(page.map { it.second }, next)
    }

    @Synchronized
    fun selectionInputPage(
        sessionId: String,
        crawlId: String,
        afterRecordId: String?,
        limit: Int,
    ): PreprocessedSelectionPage {
        require(limit in 1..MAX_SELECTION_INPUT_PAGE_SIZE)
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        afterRecordId?.let(::requireSafeId)
        getRun(sessionId, crawlId)
        val output = mutableListOf<PreprocessedSelectionInput>()
        val selection = if (afterRecordId == null) {
            "session_id = ? AND crawl_id = ? AND preprocessing_json IS NOT NULL"
        } else {
            "session_id = ? AND crawl_id = ? AND preprocessing_json IS NOT NULL AND record_id > ?"
        }
        val arguments = if (afterRecordId == null) {
            arrayOf(sessionId, crawlId)
        } else {
            arrayOf(sessionId, crawlId, afterRecordId)
        }
        database.query(
            "preprocessing_records",
            arrayOf(
                "record_id",
                "source_kind",
                "base_record_json",
                "normalized_text",
                "preprocessing_json",
                "size_bytes",
            ),
            selection,
            arguments,
            null,
            null,
            "record_id",
            (limit + 1).toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val base = JSONObject(cursor.getString(2))
                val metadata = base.getJSONObject("metadata")
                output.add(
                    PreprocessedSelectionInput(
                        recordId = cursor.getString(0),
                        sourceKind = cursor.getString(1),
                        sourceApp = base.nullableString("source_app"),
                        socialScope = metadata.nullableString("social_scope"),
                        normalizedText = cursor.stringOrNull(3),
                        preprocessingJson = cursor.getString(4),
                        sizeBytes = cursor.longOrNull(5),
                        thumbnailAvailable = metadata.optBoolean("thumbnail_available", false) ||
                            (metadata.optJSONArray("screenshot_ids")?.length() ?: 0) > 0,
                    ),
                )
            }
        }
        val hasMore = output.size > limit
        val records = output.take(limit)
        return PreprocessedSelectionPage(
            records,
            records.lastOrNull()?.recordId.takeIf { hasMore },
        )
    }

    @Synchronized
    fun transferRecord(
        sessionId: String,
        crawlId: String,
        recordId: String,
    ): TransferPreprocessedRecord {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        requireSafeId(recordId)
        getRun(sessionId, crawlId)
        return database.query(
            "preprocessing_records",
            arrayOf(
                "source_kind",
                "mime_type",
                "size_bytes",
                "content_uri",
                "base_record_json",
                "normalized_text",
                "content_sha256",
                "preprocessing_json",
            ),
            "session_id = ? AND crawl_id = ? AND record_id = ? " +
                "AND preprocessing_json IS NOT NULL",
            arrayOf(sessionId, crawlId, recordId),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                throw ApiException(
                    "not_found",
                    "Record preprocessing untuk transfer tidak ditemukan.",
                    404,
                )
            }
            val payload = InventoryRecordJson.withPreprocessing(
                cursor.getString(4),
                cursor.stringOrNull(5),
                cursor.stringOrNull(6),
                cursor.getString(7),
            )
            val metadata = payload.getJSONObject("metadata")
            val attachments = payload.getJSONArray("attachment_ids")
            TransferPreprocessedRecord(
                recordId = recordId,
                sourceKind = cursor.getString(0),
                mimeType = cursor.getString(1),
                sizeBytes = cursor.longOrNull(2),
                contentUri = cursor.stringOrNull(3),
                displayName = metadata.optString("display_name")
                    .takeIf(String::isNotBlank)
                    ?: recordId,
                attachmentIds = buildList {
                    for (index in 0 until attachments.length()) {
                        add(attachments.getString(index))
                    }
                },
                payload = payload,
            )
        }
    }

    @Synchronized
    fun clearSession(sessionId: String) {
        database.delete("preprocessing_cursors", "session_id = ?", arrayOf(sessionId))
        database.execSQL(
            "DELETE FROM preprocessing_preprocessor_totals WHERE crawl_id IN " +
                "(SELECT crawl_id FROM preprocessing_runs WHERE session_id = ?)",
            arrayOf(sessionId),
        )
        database.delete("preprocessing_records", "session_id = ?", arrayOf(sessionId))
        database.delete("preprocessing_runs", "session_id = ?", arrayOf(sessionId))
    }

    override fun close() {
        database.close()
    }

    private fun runOrNull(sessionId: String, crawlId: String): PreprocessingRun? = database.query(
        "preprocessing_runs",
        RUN_COLUMNS,
        "session_id = ? AND crawl_id = ?",
        arrayOf(sessionId, crawlId),
        null,
        null,
        null,
        "1",
    ).use { cursor ->
        if (!cursor.moveToFirst()) return@use null
        PreprocessingRun(
            cursor.getString(0),
            cursor.getString(1),
            PreprocessingRunState.entries.first { it.wireName == cursor.getString(2) },
            cursor.getLong(3),
            cursor.getLong(4),
            cursor.longOrNull(5),
            cursor.getLong(6),
            totals(cursor.getString(0)),
            preprocessorTotals(cursor.getString(0)),
            jsonStrings(cursor.getString(7)),
        )
    }

    private fun totals(crawlId: String): PreprocessingTotals {
        val counts = mutableMapOf<String, Int>()
        database.rawQuery(
            "SELECT state, COUNT(*) FROM preprocessing_records WHERE crawl_id = ? GROUP BY state",
            arrayOf(crawlId),
        ).use { cursor ->
            while (cursor.moveToNext()) counts[cursor.getString(0)] = cursor.getInt(1)
        }
        fun count(state: PreprocessingRecordState) = counts[state.wireName] ?: 0
        return PreprocessingTotals(
            counts.values.sum(),
            count(PreprocessingRecordState.PENDING),
            count(PreprocessingRecordState.PROCESSING),
            count(PreprocessingRecordState.COMPLETED),
            count(PreprocessingRecordState.SKIPPED),
            count(PreprocessingRecordState.TRUNCATED),
            count(PreprocessingRecordState.FAILED),
            count(PreprocessingRecordState.CANCELLED),
        )
    }

    private fun preprocessorTotals(crawlId: String): Map<String, PreprocessorTotals> {
        val totals = PREPROCESSOR_KEYS.associateWith {
            PreprocessorTotals(0, 0, 0, 0, 0, 0)
        }.toMutableMap()
        database.query(
            "preprocessing_preprocessor_totals",
            arrayOf(
                "preprocessor",
                "attempted",
                "processed",
                "skipped",
                "truncated",
                "failed",
                "cancelled",
            ),
            "crawl_id = ?",
            arrayOf(crawlId),
            null,
            null,
            "preprocessor",
        ).use { cursor ->
            while (cursor.moveToNext()) {
                totals[cursor.getString(0)] = PreprocessorTotals(
                    cursor.getInt(1),
                    cursor.getInt(2),
                    cursor.getInt(3),
                    cursor.getInt(4),
                    cursor.getInt(5),
                    cursor.getInt(6),
                )
            }
        }
        return totals
    }

    private fun updatePreprocessorTotals(crawlId: String, preprocessingJson: String) {
        val payload = JSONObject(preprocessingJson)
        PREPROCESSOR_KEYS.forEach { preprocessor ->
            val status = payload.optJSONObject(preprocessor)?.optString("status")
                ?.takeIf(String::isNotBlank)
                ?: return@forEach
            val column = when (status) {
                "completed" -> "processed"
                "skipped" -> "skipped"
                "truncated" -> "truncated"
                "failed" -> "failed"
                "cancelled" -> "cancelled"
                else -> return@forEach
            }
            database.insertWithOnConflict(
                "preprocessing_preprocessor_totals",
                null,
                ContentValues().apply {
                    put("crawl_id", crawlId)
                    put("preprocessor", preprocessor)
                    put("attempted", 0)
                    put("processed", 0)
                    put("skipped", 0)
                    put("truncated", 0)
                    put("failed", 0)
                    put("cancelled", 0)
                },
                SQLiteDatabase.CONFLICT_IGNORE,
            )
            database.execSQL(
                "UPDATE preprocessing_preprocessor_totals SET attempted = attempted + 1, " +
                    "$column = $column + 1 WHERE crawl_id = ? AND preprocessor = ?",
                arrayOf(crawlId, preprocessor),
            )
        }
    }

    private fun recordCount(sessionId: String, crawlId: String): Int = database.rawQuery(
        "SELECT COUNT(*) FROM preprocessing_records WHERE session_id = ? AND crawl_id = ?",
        arrayOf(sessionId, crawlId),
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }

    private fun registerCursor(
        sessionId: String,
        crawlId: String,
        afterRecordId: String,
        now: Long,
    ): String {
        val id = "pre_cursor_${UUID.randomUUID()}"
        database.insertOrThrow(
            "preprocessing_cursors",
            null,
            ContentValues().apply {
                put("cursor_id", id)
                put("session_id", sessionId)
                put("crawl_id", crawlId)
                put("after_record_id", afterRecordId)
                put("created_at", now)
            },
        )
        database.execSQL(
            "DELETE FROM preprocessing_cursors WHERE crawl_id = ? AND cursor_id NOT IN " +
                "(SELECT cursor_id FROM preprocessing_cursors WHERE crawl_id = ? " +
                "ORDER BY created_at DESC LIMIT ?)",
            arrayOf<Any?>(crawlId, crawlId, MAX_CURSORS),
        )
        return id
    }

    private fun resolveCursor(sessionId: String, crawlId: String, cursorId: String): String {
        requireSafeId(cursorId)
        return database.query(
            "preprocessing_cursors",
            arrayOf("after_record_id"),
            "cursor_id = ? AND session_id = ? AND crawl_id = ?",
            arrayOf(cursorId, sessionId, crawlId),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                throw ApiException("invalid_cursor", "Cursor preprocessing tidak tersedia.", 422)
            }
            cursor.getString(0)
        }
    }

    private fun requireSafeId(value: String) {
        require(SessionAuthenticator.SAFE_ID.matches(value))
    }

    private fun originFromBaseJson(raw: String): Pair<String?, String?> {
        val metadata = JSONObject(raw).optJSONObject("metadata") ?: return null to null
        return metadata.optString("display_name").takeIf(String::isNotBlank) to
            metadata.optString("directory_hint").takeIf(String::isNotBlank)
    }

    private fun jsonStrings(value: String): List<String> {
        val array = JSONArray(value)
        return buildList { for (index in 0 until array.length()) add(array.getString(index)) }
    }

    private fun JSONObject.nullableString(key: String): String? =
        if (!has(key) || isNull(key)) null else getString(key)

    private fun android.database.Cursor.stringOrNull(index: Int): String? =
        if (isNull(index)) null else getString(index)

    private fun android.database.Cursor.intOrNull(index: Int): Int? =
        if (isNull(index)) null else getInt(index)

    private fun android.database.Cursor.longOrNull(index: Int): Long? =
        if (isNull(index)) null else getLong(index)

    private class Database(context: Context) : SQLiteOpenHelper(
        context,
        "siksik_preprocessing.db",
        null,
        DATABASE_VERSION,
    ) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE preprocessing_runs (
                    crawl_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    deadline_at INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    partial_reasons TEXT NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE preprocessing_records (
                    session_id TEXT NOT NULL,
                    crawl_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER,
                    width INTEGER,
                    height INTEGER,
                    content_uri TEXT,
                    base_record_json TEXT NOT NULL,
                    original_text TEXT,
                    original_sha256 TEXT,
                    state TEXT NOT NULL,
                    selection_state TEXT NOT NULL DEFAULT 'blocked',
                    preprocessing_json TEXT,
                    normalized_text TEXT,
                    content_sha256 TEXT,
                    perceptual_hash TEXT,
                    face_vectors_json TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (crawl_id, record_id)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE preprocessing_cursors (
                    cursor_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    crawl_id TEXT NOT NULL,
                    after_record_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL(
                "CREATE INDEX preprocessing_records_state " +
                    "ON preprocessing_records(crawl_id, state, record_id)",
            )
            db.execSQL(
                "CREATE INDEX preprocessing_records_selection " +
                    "ON preprocessing_records(crawl_id, selection_state, record_id)",
            )
            db.execSQL(
                "CREATE INDEX preprocessing_runs_session " +
                    "ON preprocessing_runs(session_id, updated_at)",
            )
            createPreprocessorTotalsTable(db)
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            if (oldVersion == 1 && newVersion >= 2) {
                createPreprocessorTotalsTable(db)
            }
            if (oldVersion < 3 && newVersion >= 3) {
                db.execSQL(
                    "ALTER TABLE preprocessing_records ADD COLUMN " +
                        "selection_state TEXT NOT NULL DEFAULT 'blocked'",
                )
                db.execSQL(
                    "UPDATE preprocessing_records SET selection_state = 'pending' " +
                        "WHERE preprocessing_json IS NOT NULL AND (" +
                        "source_kind IN ('sms', 'visible_ui', 'notification', 'contact') OR " +
                        "crawl_id IN (SELECT crawl_id FROM preprocessing_runs " +
                        "WHERE state IN ('complete', 'partial', 'cancelled', 'failed')))"
                )
                db.execSQL(
                    "CREATE INDEX preprocessing_records_selection " +
                        "ON preprocessing_records(crawl_id, selection_state, record_id)",
                )
            }
            if (newVersion > DATABASE_VERSION) {
                throw IllegalStateException("Unsupported preprocessing database migration")
            }
        }

        private fun createPreprocessorTotalsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS preprocessing_preprocessor_totals (
                    crawl_id TEXT NOT NULL,
                    preprocessor TEXT NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (crawl_id, preprocessor)
                )
                """.trimIndent(),
            )
        }
    }

    companion object {
        private const val DATABASE_VERSION = 3
        private const val MAX_RECORD_JSON_BYTES = 1024 * 1024
        private const val MAX_PREPROCESS_JSON_BYTES = 512 * 1024
        private const val MAX_RESULT_PAGE_SIZE = 20
        private const val MAX_SELECTION_INPUT_PAGE_SIZE = 100
        private const val MAX_CURSORS = 128
        private const val SELECTION_BLOCKED = "blocked"
        private const val SELECTION_PENDING = "pending"
        private const val SELECTION_PROCESSING = "processing"
        private const val SELECTION_EVALUATED = "evaluated"
        private val SAFE_REASON = Regex("^[a-z0-9_]{1,128}$")
        private val PREPROCESSOR_KEYS = listOf(
            "exact_hash",
            "perceptual_hash",
            "ocr",
            "document_text",
            "face",
            "objects",
        )
        private val TERMINAL_RUN_STATES = setOf(
            PreprocessingRunState.COMPLETE,
            PreprocessingRunState.PARTIAL,
            PreprocessingRunState.CANCELLED,
            PreprocessingRunState.FAILED,
        )
        private val TERMINAL_RECORD_STATES = setOf(
            PreprocessingRecordState.COMPLETED,
            PreprocessingRecordState.SKIPPED,
            PreprocessingRecordState.TRUNCATED,
            PreprocessingRecordState.FAILED,
            PreprocessingRecordState.CANCELLED,
        )
        private val STORED_COLUMNS = arrayOf(
            "session_id",
            "crawl_id",
            "record_id",
            "source_kind",
            "mime_type",
            "size_bytes",
            "width",
            "height",
            "content_uri",
            "original_text",
            "original_sha256",
            "base_record_json",
        )
        private val RUN_COLUMNS = arrayOf(
            "crawl_id",
            "session_id",
            "state",
            "started_at",
            "updated_at",
            "completed_at",
            "deadline_at",
            "partial_reasons",
        )
    }
}

data class StoredClusterSignals(
    val recordId: String,
    val exactSha256: String?,
    val perceptualHash: String?,
    val width: Int?,
    val height: Int?,
    val sizeBytes: Long?,
    val faceVectorsJson: String?,
)
