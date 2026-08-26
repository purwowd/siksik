package com.siksik.agent.staging

import com.siksik.agent.model.ApiException
import java.io.File

object StagePathPolicy {
    private const val MAX_STAGED_NAME_LENGTH = 160

    fun controlledChild(root: File, relative: String): File {
        val normalized = relative.trim().trim('/')
        if (
            normalized.isEmpty() ||
            normalized.startsWith("/") ||
            normalized.split('/').any { it.isEmpty() || it == "." || it == ".." }
        ) {
            throw ApiException("validation_error", "Path staging tidak valid.", 422)
        }
        val canonicalRoot = root.canonicalFile
        val child = File(canonicalRoot, normalized).canonicalFile
        if (!child.toPath().startsWith(canonicalRoot.toPath())) {
            throw ApiException("validation_error", "Path staging di luar root.", 422)
        }
        return child
    }

    fun safeStagedName(artifactId: String, displayName: String): String {
        val cleaned = displayName.replace(Regex("[\\p{Cntrl}/\\\\]"), "_").take(MAX_STAGED_NAME_LENGTH)
        return "${artifactId}__$cleaned"
    }
}
