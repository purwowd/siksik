package com.siksik.agent.staging

import com.siksik.agent.model.ApiException
import java.io.File

object StagePathPolicy {
    private const val MAX_SOURCE_NAME_LENGTH = 160

    fun controlledChild(root: File, relative: String): File {
        val canonicalRoot = root.canonicalFile
        val child = File(canonicalRoot, relative).canonicalFile
        if (!child.toPath().startsWith(canonicalRoot.toPath())) {
            throw ApiException("validation_error", "Path staging tidak valid.", 422)
        }
        return child
    }

    fun safeStagedName(artifactId: String, displayName: String): String {
        val sanitized = displayName
            .replace(Regex("[\\p{Cntrl}/\\\\]"), "_")
            .trim()
            .ifBlank { "media" }
            .take(MAX_SOURCE_NAME_LENGTH)
        return "${artifactId}__$sanitized"
    }

    fun relativePath(record: StageRecord, leaf: String): String =
        "${record.sessionId}/${record.stageId}/$leaf"
}
