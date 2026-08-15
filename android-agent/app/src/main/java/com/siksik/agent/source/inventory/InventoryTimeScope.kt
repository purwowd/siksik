package com.siksik.agent.source.inventory

import java.time.Instant
import java.time.ZoneOffset

class InventoryTimeScope private constructor(
    val notBeforeEpochMs: Long,
) {
    val isBounded: Boolean
        get() = notBeforeEpochMs != Long.MIN_VALUE

    fun includesTimestamp(timestampEpochMs: Long?): Boolean =
        timestampEpochMs == null || timestampEpochMs >= notBeforeEpochMs

    fun includes(record: InventoryRecord): Boolean {
        if (record.sourceKind == InventorySourceKind.CONTACT) return true
        val sourceTime = record.captureTimeEpochMs
            ?: record.dateTakenEpochMs
            ?: record.dateAddedEpochMs
            ?: record.dateModifiedEpochMs
        return includesTimestamp(sourceTime)
    }

    companion object {
        val UNBOUNDED = InventoryTimeScope(Long.MIN_VALUE)

        fun forRun(mode: InventoryMode, referenceEpochMs: Long): InventoryTimeScope {
            require(referenceEpochMs > 0) { "inventory_reference_time_invalid" }
            val months = when (mode) {
                InventoryMode.QUICK -> 3L
                InventoryMode.FULL -> 6L
            }
            val cutoff = Instant.ofEpochMilli(referenceEpochMs)
                .atZone(ZoneOffset.UTC)
                .minusMonths(months)
                .toInstant()
                .toEpochMilli()
            return InventoryTimeScope(cutoff)
        }
    }
}
