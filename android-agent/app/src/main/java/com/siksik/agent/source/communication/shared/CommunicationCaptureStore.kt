package com.siksik.agent.source.communication

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.siksik.agent.BuildConfig
import java.io.File
import org.json.JSONArray
import org.json.JSONObject

class CommunicationCaptureStore(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext ?: context
    private val database = Database(appContext).writableDatabase

    @Synchronized
    fun beginSession(
        sessionId: String,
        crawlId: String,
        targets: Set<String>,
        now: Long,
    ) {
        require(CommunicationIdentifiers.SAFE_ID.matches(sessionId))
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        val validatedTargets = CommunicationPolicy.validateTargets(targets)
        val existing = session(crawlId)
        deactivateOtherSessions(crawlId, now)
        if (existing != null) {
            require(existing.sessionId == sessionId && existing.targetPackages == validatedTargets)
            database.update(
                "capture_sessions",
                ContentValues().apply {
                    put("active", 1)
                    putNull("active_social_package")
                    putNull("active_social_scope")
                    put("updated_at", now)
                },
                "crawl_id = ?",
                arrayOf(crawlId),
            )
            return
        }
        database.insertOrThrow(
            "capture_sessions",
            null,
            ContentValues().apply {
                put("crawl_id", crawlId)
                put("session_id", sessionId)
                put("target_packages", JSONArray(validatedTargets.toList()).toString())
                put("active", 1)
                put("accessibility_state", "ready")
                putNull("accessibility_reason")
                put("notification_state", "ready")
                putNull("notification_reason")
                put("screen_sequence", 0)
                putNull("active_social_package")
                putNull("active_social_scope")
                put("started_at", now)
                put("updated_at", now)
            },
        )
    }

    @Synchronized
    fun resumeSession(crawlId: String, now: Long) {
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        deactivateOtherSessions(crawlId, now)
        database.update(
            "capture_sessions",
            ContentValues().apply {
                put("active", 1)
                putNull("active_social_package")
                putNull("active_social_scope")
                put("updated_at", now)
            },
            "crawl_id = ?",
            arrayOf(crawlId),
        )
    }

    @Synchronized
    fun activeSession(): CaptureSession? = database.query(
        "capture_sessions",
        SESSION_COLUMNS,
        "active = 1",
        null,
        null,
        null,
        "updated_at DESC",
        "1",
    ).use { cursor -> if (cursor.moveToFirst()) cursor.captureSession() else null }

    @Synchronized
    fun session(crawlId: String): CaptureSession? = database.query(
        "capture_sessions",
        SESSION_COLUMNS,
        "crawl_id = ?",
        arrayOf(crawlId),
        null,
        null,
        null,
        "1",
    ).use { cursor -> if (cursor.moveToFirst()) cursor.captureSession() else null }

    @Synchronized
    fun sessionForSession(sessionId: String): CaptureSession? = database.query(
        "capture_sessions",
        SESSION_COLUMNS,
        "session_id = ?",
        arrayOf(sessionId),
        null,
        null,
        "updated_at DESC",
        "1",
    ).use { cursor -> if (cursor.moveToFirst()) cursor.captureSession() else null }

    @Synchronized
    fun targetsForCrawl(crawlId: String): Set<String> =
        session(crawlId)?.targetPackages ?: emptySet()

    @Synchronized
    fun finishSession(crawlId: String, now: Long) {
        database.update(
            "capture_sessions",
            ContentValues().apply {
                put("active", 0)
                putNull("active_social_package")
                putNull("active_social_scope")
                put("updated_at", now)
            },
            "crawl_id = ?",
            arrayOf(crawlId),
        )
    }

    @Synchronized
    fun markAccessibilityIssue(reason: String, now: Long) {
        require(SAFE_REASON.matches(reason))
        database.update(
            "capture_sessions",
            ContentValues().apply {
                put("accessibility_state", "partial")
                put("accessibility_reason", reason)
                put("updated_at", now)
            },
            "active = 1",
            null,
        )
    }

    @Synchronized
    fun markNotificationIssue(reason: String, now: Long) {
        require(SAFE_REASON.matches(reason))
        database.update(
            "capture_sessions",
            ContentValues().apply {
                put("notification_state", "partial")
                put("notification_reason", reason)
                put("updated_at", now)
            },
            "active = 1",
            null,
        )
    }

    @Synchronized
    fun setVerifiedSocialScope(
        crawlId: String,
        packageName: String,
        socialScope: String,
        now: Long,
    ): Boolean {
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        require(packageName in CommunicationPolicy.supportedSocialTargets)
        require(CommunicationPolicy.supportsSocialScope(packageName, socialScope))
        val capture = session(crawlId) ?: return false
        if (!capture.active || packageName !in capture.targetPackages) return false
        return database.update(
            "capture_sessions",
            ContentValues().apply {
                put("active_social_package", packageName)
                put("active_social_scope", socialScope)
                put("updated_at", now)
            },
            "crawl_id = ? AND active = 1",
            arrayOf(crawlId),
        ) == 1
    }

    @Synchronized
    fun clearVerifiedSocialScope(crawlId: String, now: Long) {
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        database.update(
            "capture_sessions",
            ContentValues().apply {
                putNull("active_social_package")
                putNull("active_social_scope")
                put("updated_at", now)
            },
            "crawl_id = ?",
            arrayOf(crawlId),
        )
    }

    @Synchronized
    fun activeVerifiedSocialScope(crawlId: String, packageName: String): String? = database.query(
        "capture_sessions",
        arrayOf("active_social_scope"),
        "crawl_id = ? AND active = 1 AND active_social_package = ?",
        arrayOf(crawlId, packageName),
        null,
        null,
        "updated_at DESC",
        "1",
    ).use { cursor ->
        if (cursor.moveToFirst()) cursor.stringOrNull(0) else null
    }?.takeIf { socialScope ->
        CommunicationPolicy.supportsSocialScope(packageName, socialScope)
    }

    @Synchronized
    fun recordVisibleSnapshot(
        packageName: String,
        windowId: Int,
        activityContext: String?,
        eventType: Int,
        eventTime: Long,
        nodes: List<VisibleNodeRecord>,
        normalizedText: String?,
        contentHash: String,
        socialScope: String,
        screenshotIds: List<String>,
        now: Long,
        profileLinks: List<String> = emptyList(),
    ): Boolean {
        val active = activeSession() ?: return false
        if (
            packageName !in active.targetPackages ||
            !CommunicationPolicy.supportsSocialScope(packageName, socialScope) ||
            activeVerifiedSocialScope(active.crawlId, packageName) != socialScope ||
            screenshotIds.size > 16 ||
            screenshotIds.any { !CommunicationIdentifiers.SAFE_ID.matches(it) } ||
            profileLinks.size > MAX_PROFILE_LINKS ||
            profileLinks.any { it.length !in 4..MAX_PROFILE_LINK_LENGTH } ||
            (socialScope != "own_profile" && profileLinks.isNotEmpty()) ||
            nodes.size > BuildConfig.MAX_UI_NODES ||
            nodes.any { node ->
                node.depth !in 0..BuildConfig.MAX_UI_DEPTH ||
                    (node.text?.length ?: 0) > BuildConfig.MAX_UI_TEXT_LENGTH ||
                    (node.contentDescription?.length ?: 0) > BuildConfig.MAX_UI_TEXT_LENGTH ||
                    listOf(node.left, node.top, node.right, node.bottom).any { it !in -100_000..100_000 }
            }
        ) {
            return false
        }
        if (countRows("visible_snapshots", active.crawlId) >= BuildConfig.MAX_CAPTURE_RECORDS) {
            markAccessibilityIssue("visible_ui_record_limit", now)
            return false
        }
        database.beginTransaction()
        return try {
            database.execSQL(
                "UPDATE capture_sessions SET screen_sequence = screen_sequence + 1, " +
                    "updated_at = ? WHERE crawl_id = ? AND active = 1 " +
                    "AND active_social_package = ? AND active_social_scope = ?",
                arrayOf<Any>(now, active.crawlId, packageName, socialScope),
            )
            val sequence = database.query(
                "capture_sessions",
                arrayOf("screen_sequence"),
                "crawl_id = ? AND active = 1 AND active_social_package = ? " +
                    "AND active_social_scope = ?",
                arrayOf(active.crawlId, packageName, socialScope),
                null,
                null,
                null,
                "1",
            ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else return false }
            val identity = "${active.crawlId}:$packageName:$socialScope:$sequence:$contentHash"
            val recordId = CommunicationPolicy.recordId("ui", identity)
            var inserted = database.insertWithOnConflict(
                "visible_snapshots",
                null,
                ContentValues().apply {
                    put("crawl_id", active.crawlId)
                    put("record_id", recordId)
                    put("package_name", packageName)
                    put("social_scope", socialScope)
                    put("window_id", windowId)
                    put("activity_context", activityContext)
                    put("event_type", eventType)
                    put("event_time", eventTime)
                    put("screen_sequence", sequence)
                    put("content_hash", contentHash)
                    put("normalized_text", normalizedText)
                    put("nodes_json", nodesJson(nodes).toString())
                    put("screenshot_ids", JSONArray(screenshotIds).toString())
                    put("profile_links", JSONArray(profileLinks).toString())
                    put("observed_at", now)
                },
                SQLiteDatabase.CONFLICT_IGNORE,
            ) != -1L
            if (!inserted) {
                val existing = database.query(
                    "visible_snapshots",
                    arrayOf("screenshot_ids", "social_scope"),
                    "crawl_id = ? AND content_hash = ?",
                    arrayOf(active.crawlId, contentHash),
                    null,
                    null,
                    null,
                    "1",
                ).use { cursor ->
                    if (cursor.moveToFirst()) {
                        stringList(cursor.getString(0)) to cursor.getString(1)
                    } else {
                        null
                    }
                }
                if (existing != null) {
                    // Idempotent success for TEXT_ONLY duplicates (UNIQUE crawl_id+content_hash).
                    inserted = true
                    if (screenshotIds.isNotEmpty()) {
                        val merged = (existing.first + screenshotIds).distinct().take(16)
                        database.update(
                            "visible_snapshots",
                            ContentValues().apply {
                                put("screenshot_ids", JSONArray(merged).toString())
                                put("observed_at", now)
                            },
                            "crawl_id = ? AND content_hash = ?",
                            arrayOf(active.crawlId, contentHash),
                        )
                    }
                }
            }
            database.setTransactionSuccessful()
            inserted
        } finally {
            database.endTransaction()
        }
    }

    @Synchronized
    fun recordAutomationResult(crawlId: String, result: AutomationTargetResult, now: Long) {
        require(result.targetPackage in CommunicationPolicy.supportedSocialTargets)
        require(result.state in AUTOMATION_STATES)
        require(result.reason == null || SAFE_REASON.matches(result.reason))
        require(result.scrollCount in 0..100 && result.screenshotIds.size <= 48)
        result.screenshotIds.forEach { require(CommunicationIdentifiers.SAFE_ID.matches(it)) }
        database.insertWithOnConflict(
            "automation_results",
            null,
            ContentValues().apply {
                put("crawl_id", crawlId)
                put("target_package", result.targetPackage)
                put("state", result.state)
                put("reason", result.reason)
                put("scroll_count", result.scrollCount)
                put("screenshot_ids", JSONArray(result.screenshotIds).toString())
                put("duration_ms", result.durationMs)
                put("updated_at", now)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    @Synchronized
    fun recordAutomationScopeCheckpoint(
        crawlId: String,
        targetPackage: String,
        socialScope: String,
        state: String,
        attempt: Int,
        failureClass: String?,
        reason: String?,
        scrollCount: Int,
        screenshotCount: Int,
        now: Long,
    ): Boolean {
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        require(targetPackage in CommunicationPolicy.supportedSocialTargets)
        require(CommunicationPolicy.supportsSocialScope(targetPackage, socialScope))
        require(state in AUTOMATION_SCOPE_STATES)
        require(attempt in 0..32)
        require(failureClass == null || failureClass in AUTOMATION_FAILURE_CLASSES)
        require(reason == null || SAFE_REASON.matches(reason))
        require(scrollCount in 0..2_000 && screenshotCount in 0..48)
        val capture = session(crawlId) ?: return false
        if (targetPackage !in capture.targetPackages) return false
        return database.insertWithOnConflict(
            "automation_scope_results",
            null,
            ContentValues().apply {
                put("crawl_id", crawlId)
                put("target_package", targetPackage)
                put("social_scope", socialScope)
                put("state", state)
                put("attempt", attempt)
                put("failure_class", failureClass)
                put("reason", reason)
                put("scroll_count", scrollCount)
                put("screenshot_count", screenshotCount)
                put("updated_at", now)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        ) != -1L
    }

    @Synchronized
    fun completedAutomationScopes(crawlId: String, targetPackage: String): Set<String> {
        // Resume only scopes that both checkpointed complete and still have capture rows.
        // A bare checkpoint without visible_ui evidence must be retried.
        val checkpointed = database.query(
            "automation_scope_results",
            arrayOf("social_scope"),
            "crawl_id = ? AND target_package = ? AND state = ?",
            arrayOf(crawlId, targetPackage, "complete"),
            null,
            null,
            "row_id ASC",
        ).use { cursor ->
            buildSet {
                while (cursor.moveToNext()) add(cursor.getString(0))
            }
        }
        if (checkpointed.isEmpty()) return emptySet()
        return checkpointed.filterTo(linkedSetOf()) { scope ->
            database.query(
                "visible_snapshots",
                arrayOf("row_id"),
                "crawl_id = ? AND package_name = ? AND social_scope = ?",
                arrayOf(crawlId, targetPackage, scope),
                null,
                null,
                "row_id ASC",
                "1",
            ).use { cursor -> cursor.moveToFirst() }
        }
    }

    @Synchronized
    fun automationIssue(crawlId: String): String? {
        val reasons = linkedSetOf<String>()
        database.query(
            "automation_results",
            arrayOf("state", "reason", "target_package"),
            "crawl_id = ? AND state != ?",
            arrayOf(crawlId, "complete"),
            null,
            null,
            "row_id ASC",
            null,
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val target = shortTargetLabel(cursor.getString(2))
                val reason = cursor.stringOrNull(1) ?: "automation_${cursor.getString(0)}"
                reasons.add("$target:$reason")
            }
        }
        // InventoryPageV1.source_reason / partial reason max_length=128.
        return reasons.take(4)
            .joinToString(";")
            .take(MAX_ISSUE_REASON_LENGTH)
            .takeIf(String::isNotEmpty)
    }

    @Synchronized
    fun automationResultCount(crawlId: String): Int = countRows("automation_results", crawlId)

    @Synchronized
    fun visiblePage(crawlId: String, afterRowId: Long, limit: Int): List<StoredVisibleRecord> =
        database.query(
            "visible_snapshots",
            VISIBLE_COLUMNS,
            "crawl_id = ? AND row_id > ? AND social_scope IS NOT NULL",
            arrayOf(crawlId, afterRowId.toString()),
            null,
            null,
            "row_id ASC",
            limit.toString(),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    add(
                        StoredVisibleRecord(
                            rowId = cursor.getLong(0),
                            recordId = cursor.getString(1),
                            packageName = cursor.getString(2),
                            windowId = cursor.getInt(3),
                            activityContext = cursor.stringOrNull(4),
                            eventType = cursor.getInt(5),
                            eventTime = cursor.getLong(6),
                            screenSequence = cursor.getLong(7),
                            contentHash = cursor.getString(8),
                            normalizedText = cursor.stringOrNull(9),
                            nodes = parseNodes(cursor.getString(10)),
                            screenshotIds = stringList(cursor.getString(11)),
                            profileLinks = stringList(cursor.getString(12)),
                            observedAt = cursor.getLong(13),
                            socialScope = cursor.getString(14),
                        ),
                    )
                }
            }
        }

    @Synchronized
    fun recordNotification(
        packageName: String,
        notificationKey: String,
        metadata: NotificationRecordMetadata,
        contentHash: String,
        now: Long,
    ): Boolean {
        val active = activeSession() ?: return false
        if (
            packageName == appContext.packageName ||
            packageName in CommunicationPolicy.supportedSocialTargets
        ) {
            return false
        }
        val identity = CommunicationPolicy.identityHash("notification", notificationKey)
        val existing = database.query(
            "notifications",
            arrayOf("row_id", "content_hash", "update_count"),
            "crawl_id = ? AND notification_identity = ?",
            arrayOf(active.crawlId, identity),
            null,
            null,
            null,
            "1",
        ).use { cursor ->
            if (cursor.moveToFirst()) {
                Triple(cursor.getLong(0), cursor.getString(1), cursor.getInt(2))
            } else {
                null
            }
        }
        if (existing?.second == contentHash) return false
        if (existing == null && countRows("notifications", active.crawlId) >= BuildConfig.MAX_CAPTURE_RECORDS) {
            markNotificationIssue("notification_record_limit", now)
            return false
        }
        val recordId = CommunicationPolicy.scopedRecordId(
            "notification",
            active.crawlId,
            identity,
        )
        val values = ContentValues().apply {
            put("crawl_id", active.crawlId)
            put("record_id", recordId)
            put("notification_identity", identity)
            put("package_name", packageName)
            put("title", metadata.title)
            put("text", metadata.text)
            put("sub_text", metadata.subText)
            put("big_text", metadata.bigText)
            put("text_lines", JSONArray(metadata.textLines).toString())
            put("category", metadata.category)
            put("channel_id", metadata.channelId)
            put("post_time", metadata.postTimeEpochMs)
            put("removed_at", metadata.removedAtEpochMs)
            put("content_hash", contentHash)
            put("update_count", (existing?.third ?: 0) + 1)
            put("observed_at", now)
        }
        val stored = if (existing == null) {
            val inserted = database.insertWithOnConflict(
                "notifications",
                null,
                values,
                SQLiteDatabase.CONFLICT_IGNORE,
            )
            inserted != -1L || database.update(
                "notifications",
                values,
                "crawl_id = ? AND notification_identity = ?",
                arrayOf(active.crawlId, identity),
            ) == 1
        } else {
            database.update(
                "notifications",
                values,
                "row_id = ?",
                arrayOf(existing.first.toString()),
            ) == 1
        }
        if (!stored) markNotificationIssue("notification_write_conflict", now)
        return stored
    }

    @Synchronized
    fun markNotificationRemoved(notificationKey: String, now: Long) {
        val active = activeSession() ?: return
        val identity = CommunicationPolicy.identityHash("notification", notificationKey)
        database.update(
            "notifications",
            ContentValues().apply { put("removed_at", now) },
            "crawl_id = ? AND notification_identity = ?",
            arrayOf(active.crawlId, identity),
        )
    }

    @Synchronized
    fun notificationPage(
        crawlId: String,
        afterRowId: Long,
        limit: Int,
    ): List<StoredNotificationRecord> = database.query(
        "notifications",
        NOTIFICATION_COLUMNS,
        "crawl_id = ? AND row_id > ?",
        arrayOf(crawlId, afterRowId.toString()),
        null,
        null,
        "row_id ASC",
        limit.toString(),
    ).use { cursor ->
        buildList {
            while (cursor.moveToNext()) {
                add(
                    StoredNotificationRecord(
                        rowId = cursor.getLong(0),
                        recordId = cursor.getString(1),
                        notificationIdentity = cursor.getString(2),
                        packageName = cursor.getString(3),
                        title = cursor.stringOrNull(4),
                        text = cursor.stringOrNull(5),
                        subText = cursor.stringOrNull(6),
                        bigText = cursor.stringOrNull(7),
                        textLines = stringList(cursor.getString(8)),
                        category = cursor.stringOrNull(9),
                        channelId = cursor.stringOrNull(10),
                        postTime = cursor.getLong(11),
                        removedAt = cursor.longOrNull(12),
                        contentHash = cursor.getString(13),
                        updateCount = cursor.getInt(14),
                        observedAt = cursor.getLong(15),
                    ),
                )
            }
        }
    }

    @Synchronized
    fun clearSession(sessionId: String) {
        val crawlIds = mutableListOf<String>()
        database.query(
            "capture_sessions",
            arrayOf("crawl_id"),
            "session_id = ?",
            arrayOf(sessionId),
            null,
            null,
            null,
        ).use { cursor -> while (cursor.moveToNext()) crawlIds.add(cursor.getString(0)) }
        crawlIds.forEach { crawlId ->
            database.delete("visible_snapshots", "crawl_id = ?", arrayOf(crawlId))
            database.delete("notifications", "crawl_id = ?", arrayOf(crawlId))
            database.delete("automation_results", "crawl_id = ?", arrayOf(crawlId))
            database.delete("automation_scope_results", "crawl_id = ?", arrayOf(crawlId))
            captureDirectory(sessionId, crawlId).deleteRecursively()
        }
        database.delete("capture_sessions", "session_id = ?", arrayOf(sessionId))
    }

    @Synchronized
    fun clearCrawl(crawlId: String) {
        val sessionId = session(crawlId)?.sessionId
        database.delete("visible_snapshots", "crawl_id = ?", arrayOf(crawlId))
        database.delete("notifications", "crawl_id = ?", arrayOf(crawlId))
        database.delete("automation_results", "crawl_id = ?", arrayOf(crawlId))
        database.delete("automation_scope_results", "crawl_id = ?", arrayOf(crawlId))
        database.delete("capture_sessions", "crawl_id = ?", arrayOf(crawlId))
        if (sessionId != null) captureDirectory(sessionId, crawlId).deleteRecursively()
    }

    fun screenshotDirectory(sessionId: String, crawlId: String): File {
        require(CommunicationIdentifiers.SAFE_ID.matches(sessionId))
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        return captureDirectory(sessionId, crawlId).resolve("screenshots").also { directory ->
            check(directory.mkdirs() || directory.isDirectory)
        }
    }

    @Synchronized
    fun screenshotForTransfer(
        sessionId: String,
        crawlId: String,
        screenshotId: String,
    ): File? {
        require(CommunicationIdentifiers.SAFE_ID.matches(sessionId))
        require(CommunicationIdentifiers.SAFE_ID.matches(crawlId))
        require(CommunicationIdentifiers.SAFE_ID.matches(screenshotId))
        val capture = session(crawlId) ?: return null
        if (capture.sessionId != sessionId) return null
        val root = captureDirectory(sessionId, crawlId).resolve("screenshots").canonicalFile
        val file = root.resolve("$screenshotId.png").canonicalFile
        if (!file.toPath().startsWith(root.toPath()) || !file.isFile) return null
        return file
    }

    override fun close() {
        database.close()
    }

    private fun captureDirectory(sessionId: String, crawlId: String): File =
        appContext.filesDir.resolve("communication").resolve(sessionId).resolve(crawlId)

    private fun deactivateOtherSessions(crawlId: String, now: Long) {
        database.update(
            "capture_sessions",
            ContentValues().apply {
                put("active", 0)
                putNull("active_social_package")
                putNull("active_social_scope")
                put("updated_at", now)
            },
            "crawl_id != ? AND active = 1",
            arrayOf(crawlId),
        )
    }

    private fun countRows(table: String, crawlId: String): Int {
        require(table in TABLES_WITH_CRAWL)
        return database.rawQuery(
            "SELECT COUNT(*) FROM $table WHERE crawl_id = ?",
            arrayOf(crawlId),
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }
    }

    private fun Cursor.captureSession(): CaptureSession = CaptureSession(
        sessionId = getString(0),
        crawlId = getString(1),
        targetPackages = stringList(getString(2)).toSet(),
        active = getInt(3) == 1,
        accessibilityState = getString(4),
        accessibilityReason = stringOrNull(5),
        notificationState = getString(6),
        notificationReason = stringOrNull(7),
    )

    private fun nodesJson(nodes: List<VisibleNodeRecord>): JSONArray = JSONArray().apply {
        nodes.forEach { node ->
            put(
                JSONObject()
                    .put("sequence", node.sequence)
                    .put("depth", node.depth)
                    .put("text", node.text ?: JSONObject.NULL)
                    .put("content_description", node.contentDescription ?: JSONObject.NULL)
                    .put("class_name", node.className ?: JSONObject.NULL)
                    .put("view_id", node.viewId ?: JSONObject.NULL)
                    .put("left", node.left)
                    .put("top", node.top)
                    .put("right", node.right)
                    .put("bottom", node.bottom)
                    .put("clickable", node.clickable)
                    .put("scrollable", node.scrollable),
            )
        }
    }

    private fun parseNodes(raw: String): List<VisibleNodeRecord> {
        val values = JSONArray(raw)
        return buildList {
            for (index in 0 until minOf(values.length(), BuildConfig.MAX_UI_NODES)) {
                val item = values.getJSONObject(index)
                add(
                    VisibleNodeRecord(
                        sequence = item.getInt("sequence"),
                        depth = item.getInt("depth"),
                        text = item.nullableString("text"),
                        contentDescription = item.nullableString("content_description"),
                        className = item.nullableString("class_name"),
                        viewId = item.nullableString("view_id"),
                        left = item.getInt("left"),
                        top = item.getInt("top"),
                        right = item.getInt("right"),
                        bottom = item.getInt("bottom"),
                        clickable = item.getBoolean("clickable"),
                        scrollable = item.getBoolean("scrollable"),
                    ),
                )
            }
        }
    }

    private fun stringList(raw: String): List<String> {
        val values = JSONArray(raw)
        return buildList {
            for (index in 0 until values.length()) add(values.getString(index))
        }
    }

    private fun JSONObject.nullableString(key: String): String? =
        if (isNull(key)) null else getString(key)

    private fun Cursor.stringOrNull(index: Int): String? = if (isNull(index)) null else getString(index)

    private fun Cursor.longOrNull(index: Int): Long? = if (isNull(index)) null else getLong(index)

    data class StoredVisibleRecord(
        val rowId: Long,
        val recordId: String,
        val packageName: String,
        val windowId: Int,
        val activityContext: String?,
        val eventType: Int,
        val eventTime: Long,
        val screenSequence: Long,
        val contentHash: String,
        val normalizedText: String?,
        val nodes: List<VisibleNodeRecord>,
        val screenshotIds: List<String>,
        val profileLinks: List<String>,
        val observedAt: Long,
        val socialScope: String,
    )

    data class StoredNotificationRecord(
        val rowId: Long,
        val recordId: String,
        val notificationIdentity: String,
        val packageName: String,
        val title: String?,
        val text: String?,
        val subText: String?,
        val bigText: String?,
        val textLines: List<String>,
        val category: String?,
        val channelId: String?,
        val postTime: Long,
        val removedAt: Long?,
        val contentHash: String,
        val updateCount: Int,
        val observedAt: Long,
    )

    private class Database(context: Context) : SQLiteOpenHelper(
        context,
        "siksik_communication.db",
        null,
        DATABASE_VERSION,
    ) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE capture_sessions (
                    crawl_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    target_packages TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    accessibility_state TEXT NOT NULL,
                    accessibility_reason TEXT,
                    notification_state TEXT NOT NULL,
                    notification_reason TEXT,
                    screen_sequence INTEGER NOT NULL,
                    active_social_package TEXT,
                    active_social_scope TEXT,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE visible_snapshots (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    crawl_id TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    package_name TEXT NOT NULL,
                    social_scope TEXT NOT NULL,
                    window_id INTEGER NOT NULL,
                    activity_context TEXT,
                    event_type INTEGER NOT NULL,
                    event_time INTEGER NOT NULL,
                    screen_sequence INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    normalized_text TEXT,
                    nodes_json TEXT NOT NULL,
                    screenshot_ids TEXT NOT NULL,
                    profile_links TEXT NOT NULL DEFAULT '[]',
                    observed_at INTEGER NOT NULL,
                    UNIQUE(crawl_id, content_hash)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE notifications (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    crawl_id TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    notification_identity TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    title TEXT,
                    text TEXT,
                    sub_text TEXT,
                    big_text TEXT,
                    text_lines TEXT NOT NULL,
                    category TEXT,
                    channel_id TEXT,
                    post_time INTEGER NOT NULL,
                    removed_at INTEGER,
                    content_hash TEXT NOT NULL,
                    update_count INTEGER NOT NULL,
                    observed_at INTEGER NOT NULL,
                    UNIQUE(crawl_id, notification_identity)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE automation_results (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    crawl_id TEXT NOT NULL,
                    target_package TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT,
                    scroll_count INTEGER NOT NULL,
                    screenshot_ids TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(crawl_id, target_package)
                )
                """.trimIndent(),
            )
            createAutomationScopeResultsTable(db)
            db.execSQL("CREATE INDEX capture_session_active ON capture_sessions(active, updated_at)")
            db.execSQL("CREATE INDEX visible_crawl_page ON visible_snapshots(crawl_id, row_id)")
            db.execSQL(
                "CREATE INDEX visible_crawl_scope_page " +
                    "ON visible_snapshots(crawl_id, social_scope, row_id)",
            )
            db.execSQL("CREATE INDEX notification_crawl_page ON notifications(crawl_id, row_id)")
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            if (oldVersion < 2) {
                db.execSQL("ALTER TABLE capture_sessions ADD COLUMN active_social_package TEXT")
                db.execSQL("ALTER TABLE capture_sessions ADD COLUMN active_social_scope TEXT")
                db.execSQL("ALTER TABLE visible_snapshots ADD COLUMN social_scope TEXT")
                db.execSQL(
                    "CREATE INDEX visible_crawl_scope_page " +
                        "ON visible_snapshots(crawl_id, social_scope, row_id)",
                )
            }
            if (oldVersion < 3) {
                db.execSQL(
                    "ALTER TABLE visible_snapshots ADD COLUMN " +
                        "profile_links TEXT NOT NULL DEFAULT '[]'",
                )
            }
            if (oldVersion < 4) {
                createAutomationScopeResultsTable(db)
            }
            if (newVersion > DATABASE_VERSION) {
                error("Unsupported communication database migration")
            }
        }

        private fun createAutomationScopeResultsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS automation_scope_results (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    crawl_id TEXT NOT NULL,
                    target_package TEXT NOT NULL,
                    social_scope TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    failure_class TEXT,
                    reason TEXT,
                    scroll_count INTEGER NOT NULL,
                    screenshot_count INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(crawl_id, target_package, social_scope)
                )
                """.trimIndent(),
            )
            db.execSQL(
                "CREATE INDEX IF NOT EXISTS automation_scope_crawl " +
                    "ON automation_scope_results(crawl_id, target_package, state)",
            )
        }
    }

    companion object {
        private const val DATABASE_VERSION = 4
        private const val MAX_PROFILE_LINKS = 16
        private const val MAX_PROFILE_LINK_LENGTH = 2048
        private const val MAX_ISSUE_REASON_LENGTH = 128
        private val SAFE_REASON = Regex("^[a-z0-9_]{1,128}$")
        private val TABLES_WITH_CRAWL = setOf(
            "visible_snapshots",
            "notifications",
            "automation_results",
            "automation_scope_results",
        )
        private val AUTOMATION_STATES = setOf(
            "complete",
            "partial",
            "cancelled",
            "failed",
            "target_missing",
            "timeout",
        )
        private val AUTOMATION_SCOPE_STATES = setOf(
            "complete",
            "retrying",
            "failed",
            "cancelled",
        )
        private val AUTOMATION_FAILURE_CLASSES = setOf(
            "observation",
            "action",
            "postcondition",
            "empty_content",
        )
        private val SHORT_TARGET_LABELS = mapOf(
            "com.instagram.android" to "ig",
            "com.twitter.android" to "x",
            "com.facebook.katana" to "fb",
        )

        private fun shortTargetLabel(packageName: String): String =
            SHORT_TARGET_LABELS[packageName]
                ?: packageName.substringAfterLast('.').take(12).ifEmpty { "app" }

        private val SESSION_COLUMNS = arrayOf(
            "session_id",
            "crawl_id",
            "target_packages",
            "active",
            "accessibility_state",
            "accessibility_reason",
            "notification_state",
            "notification_reason",
        )
        private val VISIBLE_COLUMNS = arrayOf(
            "row_id",
            "record_id",
            "package_name",
            "window_id",
            "activity_context",
            "event_type",
            "event_time",
            "screen_sequence",
            "content_hash",
            "normalized_text",
            "nodes_json",
            "screenshot_ids",
            "profile_links",
            "observed_at",
            "social_scope",
        )
        private val NOTIFICATION_COLUMNS = arrayOf(
            "row_id",
            "record_id",
            "notification_identity",
            "package_name",
            "title",
            "text",
            "sub_text",
            "big_text",
            "text_lines",
            "category",
            "channel_id",
            "post_time",
            "removed_at",
            "content_hash",
            "update_count",
            "observed_at",
        )
    }
}
