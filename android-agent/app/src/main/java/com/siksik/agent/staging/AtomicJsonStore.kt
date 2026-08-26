package com.siksik.agent.staging

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import org.json.JSONObject

internal object AtomicJsonStore {
    fun write(target: File, payload: JSONObject) {
        target.parentFile?.mkdirs()
        val partial = File(target.parentFile, "${target.name}.partial")
        partial.writeText(payload.toString(), Charsets.UTF_8)
        try {
            Files.move(
                partial.toPath(),
                target.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (_: Exception) {
            if (target.exists()) target.delete()
            if (!partial.renameTo(target)) {
                throw IllegalStateException("atomic json replace failed")
            }
        }
    }

    fun read(target: File): JSONObject? {
        if (!target.isFile) return null
        return JSONObject(target.readText(Charsets.UTF_8))
    }
}
