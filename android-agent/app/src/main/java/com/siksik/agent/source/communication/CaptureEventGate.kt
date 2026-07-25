package com.siksik.agent.source.communication

class CaptureEventGate(
    private val minimumIntervalMs: Long,
    private val clock: () -> Long,
) {
    private val lastCaptureAt = mutableMapOf<String, Long>()

    init {
        require(minimumIntervalMs > 0)
    }

    @Synchronized
    fun allow(packageName: String, allowedPackages: Set<String>): Boolean {
        if (packageName !in allowedPackages) return false
        val now = clock()
        val previous = lastCaptureAt[packageName]
        if (previous != null && now - previous < minimumIntervalMs) return false
        lastCaptureAt[packageName] = now
        lastCaptureAt.keys.retainAll(allowedPackages)
        return true
    }

    @Synchronized
    fun clear() {
        lastCaptureAt.clear()
    }
}
