package com.siksik.agent.selection

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import org.json.JSONArray
import org.json.JSONObject

data class SelectionMutation(
    val run: SelectionRun,
    val candidate: SelectionCandidate,
)

data class TransferSelectionSnapshot(
    val run: SelectionRun,
    val candidates: List<SelectionCandidate>,
)

data class LiveSelectedCandidate(
    val sequence: Long,
    val candidate: SelectionCandidate,
)

data class LiveSelectedCandidatePage(
    val records: List<LiveSelectedCandidate>,
    val nextSequence: Long?,
)

class SelectionStore(context: Context) : AutoCloseable {
    private val database = Database(context.applicationContext).writableDatabase

    @Synchronized
    fun start(
        sessionId: String,
        crawlId: String,
        policy: SelectionPolicy,
        reviewCandidates: Boolean,
        totalRecords: Int,
        now: Long,
    ): SelectionRun {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        require(totalRecords > 0)
        val existing = runOrNull(sessionId, crawlId)
        if (existing != null) {
            if (
                existing.policyFingerprint != policy.policyFingerprint ||
                existing.reviewCandidates != reviewCandidates
            ) {
                throw ApiException(
                    "selection_policy_mismatch",
                    "Policy selection tidak sama dengan crawl aktif.",
                    409,
                )
            }
            if (existing.state in setOf(
                    SelectionRunState.RUNNING,
                    SelectionRunState.AWAITING_REVIEW,
                    SelectionRunState.CONFIRMED,
                )
            ) {
                if (existing.totals.total != totalRecords) {
                    database.update(
                        "selection_runs",
                        ContentValues().apply { put("total_records", totalRecords) },
                        "session_id = ? AND crawl_id = ?",
                        arrayOf(sessionId, crawlId),
                    )
                }
                return getRun(sessionId, crawlId)
            }
        }
        database.beginTransaction()
        try {
            database.delete("selection_candidates", "crawl_id = ?", arrayOf(crawlId))
            database.insertWithOnConflict(
                "selection_runs",
                null,
                ContentValues().apply {
                    put("crawl_id", crawlId)
                    put("session_id", sessionId)
                    put("state", SelectionRunState.RUNNING.wireName)
                    put("policy_version", policy.policyVersion)
                    put("policy_fingerprint", policy.policyFingerprint)
                    put("maximum_candidates", policy.maximumCandidates)
                    put("maximum_bytes", policy.maximumBytes)
                    put("total_records", totalRecords)
                    put("revision", 1)
                    putNull("selection_fingerprint")
                    put("review_candidates", if (reviewCandidates) 1 else 0)
                    put("started_at", now)
                    put("updated_at", now)
                    putNull("frozen_at")
                    putNull("confirmed_at")
                    putNull("failure_reason")
                    put("cancel_requested", 0)
                },
                SQLiteDatabase.CONFLICT_REPLACE,
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun appendEvaluations(
        sessionId: String,
        crawlId: String,
        evaluations: List<SelectionEvaluation>,
        now: Long,
    ) {
        if (evaluations.isEmpty()) return
        val run = getRun(sessionId, crawlId)
        if (run.state != SelectionRunState.RUNNING) return
        val limits = selectionLimits(crawlId)
        var selectedCount = run.totals.selected
        var selectedBytes = run.totals.selectedBytes
        database.beginTransaction()
        try {
            evaluations.forEach { evaluation ->
                requireSafeId(evaluation.recordId)
                val size = evaluation.sizeBytes ?: 0L
                val withinCount = selectedCount < limits.first
                val withinBytes = size <= limits.second - selectedBytes
                val selected = !run.reviewCandidates &&
                    evaluation.eligibleForAutomaticSelection &&
                    withinCount && withinBytes
                val reasons = evaluation.reasons.toMutableList()
                if (evaluation.eligibleForAutomaticSelection && !selected && !run.reviewCandidates) {
                    reasons.add(
                        if (!withinCount) "candidate_budget_exceeded" else
                            "candidate_byte_budget_exceeded",
                    )
                }
                val inserted = database.insertWithOnConflict(
                    "selection_candidates",
                    null,
                    evaluationValues(sessionId, crawlId, evaluation, selected, reasons.distinct()),
                    SQLiteDatabase.CONFLICT_IGNORE,
                )
                if (inserted != -1L && selected) {
                    selectedCount += 1
                    selectedBytes += size
                }
            }
            database.execSQL(
                "UPDATE selection_runs SET updated_at = ? WHERE session_id = ? AND crawl_id = ?",
                arrayOf<Any?>(now, sessionId, crawlId),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun freeze(
        sessionId: String,
        crawlId: String,
        policy: SelectionPolicy,
        now: Long,
    ): SelectionRun {
        val current = getRun(sessionId, crawlId)
        if (current.state != SelectionRunState.RUNNING) return current
        database.beginTransaction()
        try {
            var selectedCount = current.totals.selected
            var selectedBytes = current.totals.selectedBytes
            database.query(
                "selection_candidates",
                arrayOf(
                    "record_id",
                    "eligible_auto",
                    "size_bytes",
                    "reasons_json",
                    "selected",
                ),
                "session_id = ? AND crawl_id = ?",
                arrayOf(sessionId, crawlId),
                null,
                null,
                "score_basis_points DESC, record_id",
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val eligible = cursor.getInt(1) == 1
                    val size = cursor.longOrNull(2) ?: 0L
                    val alreadySelected = cursor.getInt(4) == 1
                    val withinCount = selectedCount < policy.maximumCandidates
                    val withinBytes = size <= policy.maximumBytes - selectedBytes
                    val selected = alreadySelected || (eligible && withinCount && withinBytes)
                    val reasons = jsonStrings(cursor.getString(3)).toMutableList()
                    if (eligible && !selected && reasons.none { it.endsWith("budget_exceeded") }) {
                        reasons.add(
                            if (!withinCount) "candidate_budget_exceeded" else
                                "candidate_byte_budget_exceeded",
                        )
                    }
                    if (selected && !alreadySelected) {
                        selectedCount += 1
                        selectedBytes += size
                    }
                    database.update(
                        "selection_candidates",
                        ContentValues().apply {
                            put("baseline_selected", if (selected) 1 else 0)
                            put("selected", if (selected) 1 else 0)
                            put("reasons_json", JSONArray(reasons).toString())
                        },
                        "crawl_id = ? AND record_id = ?",
                        arrayOf(crawlId, cursor.getString(0)),
                    )
                }
            }
            val state = if (current.reviewCandidates) {
                SelectionRunState.AWAITING_REVIEW
            } else {
                SelectionRunState.CONFIRMED
            }
            val fingerprint = selectionFingerprint(crawlId, current.policyFingerprint, 1)
            database.update(
                "selection_runs",
                ContentValues().apply {
                    put("state", state.wireName)
                    put("selection_fingerprint", fingerprint)
                    put("updated_at", now)
                    put("frozen_at", now)
                    if (state == SelectionRunState.CONFIRMED) put("confirmed_at", now)
                },
                "session_id = ? AND crawl_id = ? AND state = ?",
                arrayOf(sessionId, crawlId, SelectionRunState.RUNNING.wireName),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun getRun(sessionId: String, crawlId: String): SelectionRun =
        runOrNull(sessionId, crawlId)
            ?: throw ApiException("not_found", "Selection crawl belum tersedia.", 404)

    @Synchronized
    fun page(
        sessionId: String,
        crawlId: String,
        afterRecordId: String?,
        limit: Int,
    ): SelectionCandidatePage {
        require(limit in 1..MAX_PAGE_SIZE)
        afterRecordId?.let(::requireSafeId)
        getRun(sessionId, crawlId)
        val rows = mutableListOf<SelectionCandidate>()
        val where = if (afterRecordId == null) {
            "session_id = ? AND crawl_id = ?"
        } else {
            "session_id = ? AND crawl_id = ? AND record_id > ?"
        }
        val args = if (afterRecordId == null) {
            arrayOf(sessionId, crawlId)
        } else {
            arrayOf(sessionId, crawlId, afterRecordId)
        }
        database.query(
            "selection_candidates",
            CANDIDATE_COLUMNS,
            where,
            args,
            null,
            null,
            "record_id",
            (limit + 1).toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) rows.add(candidateFromCursor(cursor))
        }
        val hasMore = rows.size > limit
        val page = rows.take(limit)
        return SelectionCandidatePage(
            page,
            page.lastOrNull()?.recordId.takeIf { hasMore },
        )
    }

    @Synchronized
    fun liveSelectedPage(
        sessionId: String,
        crawlId: String,
        afterSequence: Long,
        limit: Int,
    ): LiveSelectedCandidatePage {
        require(afterSequence >= 0)
        require(limit in 1..MAX_PAGE_SIZE)
        val run = getRun(sessionId, crawlId)
        if (run.reviewCandidates) {
            return LiveSelectedCandidatePage(emptyList(), null)
        }
        val rows = mutableListOf<LiveSelectedCandidate>()
        database.query(
            "selection_candidates",
            arrayOf("rowid AS event_sequence", *CANDIDATE_COLUMNS),
            "session_id = ? AND crawl_id = ? AND selected = 1 AND rowid > ?",
            arrayOf(sessionId, crawlId, afterSequence.toString()),
            null,
            null,
            "rowid ASC",
            (limit + 1).toString(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                rows.add(
                    LiveSelectedCandidate(
                        cursor.getLong(cursor.getColumnIndexOrThrow("event_sequence")),
                        candidateFromCursor(cursor),
                    ),
                )
            }
        }
        val page = rows.take(limit)
        return LiveSelectedCandidatePage(
            page,
            page.lastOrNull()?.sequence,
        )
    }

    @Synchronized
    fun mutate(
        sessionId: String,
        crawlId: String,
        recordId: String,
        expectedRevision: Int,
        override: HumanOverride,
        operatorId: String,
        now: Long,
    ): SelectionMutation {
        requireSafeId(recordId)
        requireSafeId(operatorId)
        database.beginTransaction()
        try {
            val run = getRun(sessionId, crawlId)
            ensureRevision(run, expectedRevision)
            if (run.state == SelectionRunState.CONFIRMED) immutable()
            if (run.state != SelectionRunState.AWAITING_REVIEW) {
                throw ApiException(
                    "selection_not_reviewable",
                    "Selection belum siap direview.",
                    409,
                )
            }
            val row = candidateCursor(sessionId, crawlId, recordId)
                ?: throw ApiException("not_found", "Candidate selection tidak ditemukan.", 404)
            val baselineSelected = row.getInt(row.getColumnIndexOrThrow("baseline_selected")) == 1
            val wasSelected = row.getInt(row.getColumnIndexOrThrow("selected")) == 1
            val size = row.longOrNull(row.getColumnIndexOrThrow("size_bytes")) ?: 0L
            row.close()
            val selected = when (override) {
                HumanOverride.INCLUDE -> true
                HumanOverride.EXCLUDE -> false
                HumanOverride.NONE -> baselineSelected
            }
            if (selected && !wasSelected) enforceBudgets(run, size)
            val nextRevision = run.revision + 1
            database.update(
                "selection_candidates",
                ContentValues().apply {
                    put("selected", if (selected) 1 else 0)
                    put("human_override", override.wireName)
                    if (override == HumanOverride.NONE) putNull("operator_id") else put(
                        "operator_id",
                        operatorId,
                    )
                    put("decided_at", now)
                },
                "session_id = ? AND crawl_id = ? AND record_id = ?",
                arrayOf(sessionId, crawlId, recordId),
            )
            val fingerprint = selectionFingerprint(
                crawlId,
                run.policyFingerprint,
                nextRevision,
            )
            database.update(
                "selection_runs",
                ContentValues().apply {
                    put("revision", nextRevision)
                    put("selection_fingerprint", fingerprint)
                    put("updated_at", now)
                },
                "session_id = ? AND crawl_id = ?",
                arrayOf(sessionId, crawlId),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return SelectionMutation(
            getRun(sessionId, crawlId),
            candidate(sessionId, crawlId, recordId),
        )
    }

    @Synchronized
    fun confirm(
        sessionId: String,
        crawlId: String,
        expectedRevision: Int,
        now: Long,
    ): SelectionRun {
        val run = getRun(sessionId, crawlId)
        ensureRevision(run, expectedRevision)
        if (run.state == SelectionRunState.CONFIRMED) return run
        if (run.state != SelectionRunState.AWAITING_REVIEW) {
            throw ApiException(
                "selection_not_reviewable",
                "Selection belum siap dikonfirmasi.",
                409,
            )
        }
        database.update(
            "selection_runs",
            ContentValues().apply {
                put("state", SelectionRunState.CONFIRMED.wireName)
                put("updated_at", now)
                put("confirmed_at", now)
            },
            "session_id = ? AND crawl_id = ? AND state = ?",
            arrayOf(sessionId, crawlId, SelectionRunState.AWAITING_REVIEW.wireName),
        )
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun cancel(sessionId: String, crawlId: String, now: Long): SelectionRun {
        val run = getRun(sessionId, crawlId)
        if (run.state in TERMINAL_STATES) return run
        database.update(
            "selection_runs",
            ContentValues().apply {
                put("state", SelectionRunState.CANCELLED.wireName)
                put("cancel_requested", 1)
                put("updated_at", now)
            },
            "session_id = ? AND crawl_id = ?",
            arrayOf(sessionId, crawlId),
        )
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun fail(sessionId: String, crawlId: String, reason: String, now: Long): SelectionRun {
        require(SAFE_REASON.matches(reason))
        database.update(
            "selection_runs",
            ContentValues().apply {
                put("state", SelectionRunState.FAILED.wireName)
                put("failure_reason", reason)
                put("updated_at", now)
            },
            "session_id = ? AND crawl_id = ? AND state = ?",
            arrayOf(sessionId, crawlId, SelectionRunState.RUNNING.wireName),
        )
        return getRun(sessionId, crawlId)
    }

    @Synchronized
    fun isCancelRequested(crawlId: String): Boolean = database.query(
        "selection_runs",
        arrayOf("cancel_requested"),
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        null,
        "1",
    ).use { it.moveToFirst() && it.getInt(0) == 1 }

    @Synchronized
    fun selectedForTransfer(
        sessionId: String,
        crawlId: String,
        revision: Int,
        selectionFingerprint: String,
    ): TransferSelectionSnapshot {
        val run = getRun(sessionId, crawlId)
        if (
            run.state != SelectionRunState.CONFIRMED ||
            run.revision != revision ||
            run.selectionFingerprint != selectionFingerprint
        ) {
            throw ApiException(
                "selection_not_ready",
                "Selection terkonfirmasi tidak sesuai permintaan transfer.",
                409,
            )
        }
        val candidates = mutableListOf<SelectionCandidate>()
        database.query(
            "selection_candidates",
            CANDIDATE_COLUMNS,
            "session_id = ? AND crawl_id = ? AND selected = 1",
            arrayOf(sessionId, crawlId),
            null,
            null,
            "record_id",
        ).use { cursor ->
            while (cursor.moveToNext()) candidates.add(candidateFromCursor(cursor))
        }
        if (candidates.size != run.totals.selected) {
            throw ApiException(
                "selection_not_ready",
                "Ledger selection berubah sebelum transfer.",
                409,
            )
        }
        return TransferSelectionSnapshot(run, candidates)
    }

    @Synchronized
    fun clearSession(sessionId: String) {
        database.delete("selection_candidates", "session_id = ?", arrayOf(sessionId))
        database.delete("selection_runs", "session_id = ?", arrayOf(sessionId))
    }

    override fun close() {
        database.close()
    }

    private fun runOrNull(sessionId: String, crawlId: String): SelectionRun? {
        requireSafeId(sessionId)
        requireSafeId(crawlId)
        val values = database.query(
            "selection_runs",
            RUN_COLUMNS,
            "session_id = ? AND crawl_id = ?",
            arrayOf(sessionId, crawlId),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (!cursor.moveToFirst()) return null
            RunValues(
                cursor.getString(0),
                cursor.getString(1),
                SelectionRunState.entries.first { it.wireName == cursor.getString(2) },
                cursor.getString(3),
                cursor.getString(4),
                cursor.getInt(5),
                cursor.stringOrNull(6),
                cursor.getInt(7),
                cursor.getInt(8) == 1,
                cursor.getLong(9),
                cursor.getLong(10),
                cursor.longOrNull(11),
                cursor.longOrNull(12),
                cursor.stringOrNull(13),
            )
        }
        return SelectionRun(
            values.crawlId,
            values.sessionId,
            values.state,
            values.policyVersion,
            values.policyFingerprint,
            values.revision,
            values.selectionFingerprint,
            values.reviewCandidates,
            totals(values.sessionId, values.crawlId, values.totalRecords),
            values.startedAt,
            values.updatedAt,
            values.frozenAt,
            values.confirmedAt,
            values.failureReason,
        )
    }

    private fun totals(
        sessionId: String,
        crawlId: String,
        totalRecords: Int = totalRecords(crawlId),
    ): SelectionTotals = database.rawQuery(
        """
        SELECT COUNT(*),
            COALESCE(SUM(CASE WHEN eligible_auto = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN auto_selected = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN selected = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN auto_selected = 0 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN selected = 1 THEN COALESCE(size_bytes, 0) ELSE 0 END), 0)
        FROM selection_candidates WHERE session_id = ? AND crawl_id = ?
        """.trimIndent(),
        arrayOf(sessionId, crawlId),
    ).use { cursor ->
        check(cursor.moveToFirst())
        val evaluated = cursor.getInt(0)
        SelectionTotals(
            totalRecords,
            evaluated,
            cursor.getInt(1),
            cursor.getInt(2),
            cursor.getInt(3),
            cursor.getInt(4),
            cursor.getLong(5),
        )
    }

    private fun candidate(
        sessionId: String,
        crawlId: String,
        recordId: String,
    ): SelectionCandidate = candidateCursor(sessionId, crawlId, recordId).use { cursor ->
        if (cursor == null || !cursor.moveToFirst()) {
            throw ApiException("not_found", "Candidate selection tidak ditemukan.", 404)
        }
        candidateFromCursor(cursor)
    }

    private fun candidateCursor(
        sessionId: String,
        crawlId: String,
        recordId: String,
    ): Cursor? {
        val cursor = database.query(
            "selection_candidates",
            CANDIDATE_COLUMNS_WITH_BASELINE,
            "session_id = ? AND crawl_id = ? AND record_id = ?",
            arrayOf(sessionId, crawlId, recordId),
            null,
            null,
            null,
            "1",
        )
        return if (cursor.moveToFirst()) cursor else {
            cursor.close()
            null
        }
    }

    private fun candidateFromCursor(cursor: Cursor): SelectionCandidate = SelectionCandidate(
        recordId = cursor.getString(cursor.getColumnIndexOrThrow("record_id")),
        sourceKind = cursor.getString(cursor.getColumnIndexOrThrow("source_kind")),
        sourceApp = cursor.stringOrNull(cursor.getColumnIndexOrThrow("source_app")),
        evidenceText = cursor.stringOrNull(cursor.getColumnIndexOrThrow("evidence_text")),
        scoreBasisPoints = cursor.getInt(cursor.getColumnIndexOrThrow("score_basis_points")),
        thresholdBasisPoints = cursor.getInt(
            cursor.getColumnIndexOrThrow("threshold_basis_points"),
        ),
        autoSelected = cursor.getInt(cursor.getColumnIndexOrThrow("auto_selected")) == 1,
        selected = cursor.getInt(cursor.getColumnIndexOrThrow("selected")) == 1,
        eligibleForAutomaticSelection = cursor.getInt(
            cursor.getColumnIndexOrThrow("eligible_auto"),
        ) == 1,
        matchedKeywords = jsonStrings(
            cursor.getString(cursor.getColumnIndexOrThrow("matched_keywords_json")),
        ),
        matchedRules = jsonStrings(
            cursor.getString(cursor.getColumnIndexOrThrow("matched_rules_json")),
        ),
        modelSignals = modelSignals(
            cursor.getString(cursor.getColumnIndexOrThrow("model_signals_json")),
        ),
        reasons = jsonStrings(cursor.getString(cursor.getColumnIndexOrThrow("reasons_json"))),
        humanOverride = HumanOverride.fromWireName(
            cursor.getString(cursor.getColumnIndexOrThrow("human_override")),
        ),
        operatorId = cursor.stringOrNull(cursor.getColumnIndexOrThrow("operator_id")),
        decidedAtEpochMs = cursor.getLong(cursor.getColumnIndexOrThrow("decided_at")),
        duplicateGroupId = cursor.stringOrNull(
            cursor.getColumnIndexOrThrow("duplicate_group_id"),
        ),
        representativeRecordId = cursor.stringOrNull(
            cursor.getColumnIndexOrThrow("representative_record_id"),
        ),
        sizeBytes = cursor.longOrNull(cursor.getColumnIndexOrThrow("size_bytes")),
        thumbnailAvailable = cursor.getInt(
            cursor.getColumnIndexOrThrow("thumbnail_available"),
        ) == 1,
    )

    private fun evaluationValues(
        sessionId: String,
        crawlId: String,
        item: SelectionEvaluation,
        selected: Boolean,
        reasons: List<String>,
    ) = ContentValues().apply {
        put("crawl_id", crawlId)
        put("session_id", sessionId)
        put("record_id", item.recordId)
        put("source_kind", item.sourceKind)
        put("source_app", item.sourceApp)
        put("evidence_text", item.evidenceText)
        put("score_basis_points", item.scoreBasisPoints)
        put("threshold_basis_points", item.thresholdBasisPoints)
        put("auto_selected", if (item.autoSelected) 1 else 0)
        put("eligible_auto", if (item.eligibleForAutomaticSelection) 1 else 0)
        put("baseline_selected", if (selected) 1 else 0)
        put("selected", if (selected) 1 else 0)
        put("matched_keywords_json", JSONArray(item.matchedKeywords).toString())
        put("matched_rules_json", JSONArray(item.matchedRules).toString())
        put(
            "model_signals_json",
            JSONArray().apply {
                item.modelSignals.forEach { signal ->
                    put(
                        JSONObject()
                            .put("signal", signal.signal)
                            .put("value", signal.value)
                            .put("weight_basis_points", signal.weightBasisPoints),
                    )
                }
            }.toString(),
        )
        put("reasons_json", JSONArray(reasons).toString())
        put("human_override", HumanOverride.NONE.wireName)
        putNull("operator_id")
        put("decided_at", item.decidedAtEpochMs)
        put("duplicate_group_id", item.duplicateGroupId)
        put("representative_record_id", item.representativeRecordId)
        put("size_bytes", item.sizeBytes)
        put("thumbnail_available", if (item.thumbnailAvailable) 1 else 0)
    }

    private fun selectionFingerprint(
        crawlId: String,
        policyFingerprint: String,
        revision: Int,
    ): String {
        val records = JSONArray()
        database.query(
            "selection_candidates",
            arrayOf("record_id", "human_override", "operator_id"),
            "crawl_id = ? AND selected = 1",
            arrayOf(crawlId),
            null,
            null,
            "record_id",
        ).use { cursor ->
            while (cursor.moveToNext()) {
                records.put(
                    JSONObject()
                        .put("record_id", cursor.getString(0))
                        .put("human_override", cursor.getString(1))
                        .put("operator_id", cursor.stringOrNull(2) ?: JSONObject.NULL),
                )
            }
        }
        return SelectionPolicyCodec.fingerprint(
            JSONObject()
                .put("policy_fingerprint", policyFingerprint)
                .put("revision", revision)
                .put("selected_records", records),
        )
    }

    private fun enforceBudgets(run: SelectionRun, addedBytes: Long) {
        val row = selectionLimits(run.crawlId)
        if (run.totals.selected + 1 > row.first || addedBytes > row.second - run.totals.selectedBytes) {
            throw ApiException(
                "selection_budget_exceeded",
                "Perubahan candidate melampaui batas selection.",
                409,
            )
        }
    }

    private fun selectionLimits(crawlId: String): Pair<Int, Long> = database.query(
            "selection_runs",
            arrayOf("maximum_candidates", "maximum_bytes"),
            "crawl_id = ?",
            arrayOf(crawlId),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            check(cursor.moveToFirst())
            cursor.getInt(0) to cursor.getLong(1)
        }

    private fun totalRecords(crawlId: String): Int = database.query(
        "selection_runs",
        arrayOf("total_records"),
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        null,
        "1",
    ).use { cursor ->
        check(cursor.moveToFirst())
        cursor.getInt(0)
    }

    private fun ensureRevision(run: SelectionRun, expected: Int) {
        if (run.revision != expected) {
            throw ApiException(
                "selection_revision_conflict",
                "Revision selection telah berubah.",
                409,
            )
        }
    }

    private fun immutable(): Nothing = throw ApiException(
        "selection_immutable",
        "Selection yang dikonfirmasi tidak dapat diubah.",
        409,
    )

    private fun requireSafeId(value: String) {
        if (!SessionAuthenticator.SAFE_ID.matches(value)) {
            throw ApiException("validation_error", "ID selection tidak valid.", 422)
        }
    }

    private fun jsonStrings(value: String): List<String> = JSONArray(value).let { values ->
        buildList { for (index in 0 until values.length()) add(values.getString(index)) }
    }

    private fun modelSignals(value: String): List<SelectionModelSignal> = JSONArray(value).let {
        values ->
        buildList {
            for (index in 0 until values.length()) {
                val signal = values.getJSONObject(index)
                add(
                    SelectionModelSignal(
                        signal.getString("signal"),
                        signal.getString("value"),
                        signal.getInt("weight_basis_points"),
                    ),
                )
            }
        }
    }

    private fun Cursor.stringOrNull(index: Int): String? = if (isNull(index)) null else getString(index)

    private fun Cursor.longOrNull(index: Int): Long? = if (isNull(index)) null else getLong(index)

    private data class RunValues(
        val crawlId: String,
        val sessionId: String,
        val state: SelectionRunState,
        val policyVersion: String,
        val policyFingerprint: String,
        val revision: Int,
        val selectionFingerprint: String?,
        val totalRecords: Int,
        val reviewCandidates: Boolean,
        val startedAt: Long,
        val updatedAt: Long,
        val frozenAt: Long?,
        val confirmedAt: Long?,
        val failureReason: String?,
    )

    private class Database(context: Context) : SQLiteOpenHelper(
        context,
        "siksik_selection.db",
        null,
        DATABASE_VERSION,
    ) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE selection_runs (
                    crawl_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    maximum_candidates INTEGER NOT NULL,
                    maximum_bytes INTEGER NOT NULL,
                    total_records INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    selection_fingerprint TEXT,
                    review_candidates INTEGER NOT NULL,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    frozen_at INTEGER,
                    confirmed_at INTEGER,
                    failure_reason TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE selection_candidates (
                    crawl_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_app TEXT,
                    evidence_text TEXT,
                    score_basis_points INTEGER NOT NULL,
                    threshold_basis_points INTEGER NOT NULL,
                    auto_selected INTEGER NOT NULL,
                    eligible_auto INTEGER NOT NULL,
                    baseline_selected INTEGER NOT NULL,
                    selected INTEGER NOT NULL,
                    matched_keywords_json TEXT NOT NULL,
                    matched_rules_json TEXT NOT NULL,
                    model_signals_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    human_override TEXT NOT NULL,
                    operator_id TEXT,
                    decided_at INTEGER NOT NULL,
                    duplicate_group_id TEXT,
                    representative_record_id TEXT,
                    size_bytes INTEGER,
                    thumbnail_available INTEGER NOT NULL,
                    PRIMARY KEY (crawl_id, record_id)
                )
                """.trimIndent(),
            )
            db.execSQL(
                "CREATE INDEX selection_candidates_session_idx " +
                    "ON selection_candidates(session_id, crawl_id, record_id)",
            )
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            if (oldVersion < 2 && newVersion >= 2) {
                db.execSQL(
                    "ALTER TABLE selection_runs ADD COLUMN total_records INTEGER NOT NULL DEFAULT 1",
                )
                db.execSQL(
                    "UPDATE selection_runs SET total_records = MAX(1, " +
                        "(SELECT COUNT(*) FROM selection_candidates " +
                        "WHERE selection_candidates.crawl_id = selection_runs.crawl_id))",
                )
            }
            if (newVersion > DATABASE_VERSION) {
                error("Unsupported selection database migration")
            }
        }
    }

    companion object {
        private val RUN_COLUMNS = arrayOf(
            "crawl_id",
            "session_id",
            "state",
            "policy_version",
            "policy_fingerprint",
            "revision",
            "selection_fingerprint",
            "total_records",
            "review_candidates",
            "started_at",
            "updated_at",
            "frozen_at",
            "confirmed_at",
            "failure_reason",
        )
        private val CANDIDATE_COLUMNS = arrayOf(
            "record_id",
            "source_kind",
            "source_app",
            "evidence_text",
            "score_basis_points",
            "threshold_basis_points",
            "auto_selected",
            "selected",
            "eligible_auto",
            "matched_keywords_json",
            "matched_rules_json",
            "model_signals_json",
            "reasons_json",
            "human_override",
            "operator_id",
            "decided_at",
            "duplicate_group_id",
            "representative_record_id",
            "size_bytes",
            "thumbnail_available",
        )
        private val CANDIDATE_COLUMNS_WITH_BASELINE = CANDIDATE_COLUMNS + "baseline_selected"
        private val TERMINAL_STATES = setOf(
            SelectionRunState.CONFIRMED,
            SelectionRunState.CANCELLED,
            SelectionRunState.FAILED,
        )
        private val SAFE_REASON = Regex("^[a-z0-9_]{1,128}$")
        private const val MAX_PAGE_SIZE = 100
        private const val DATABASE_VERSION = 2
    }
}
