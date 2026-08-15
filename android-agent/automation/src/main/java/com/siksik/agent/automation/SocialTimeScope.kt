package com.siksik.agent.automation

import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneOffset

data class SocialTimeDecision(
    val sourceTimeEpochMs: Long?,
    val outOfScope: Boolean,
)

object SocialTimeScope {
    fun evaluate(
        labels: Iterable<String?>,
        notBeforeEpochMs: Long,
        nowEpochMs: Long,
    ): SocialTimeDecision {
        require(notBeforeEpochMs in 1 until nowEpochMs) { "social_time_scope_invalid" }
        val parsed = labels.asSequence()
            .filterNotNull()
            .flatMap { label -> timestamps(label, nowEpochMs).asSequence() }
            .toList()
        val newest = parsed.maxOrNull()
        return SocialTimeDecision(newest, newest != null && newest < notBeforeEpochMs)
    }

    private fun timestamps(value: String, nowEpochMs: Long): List<Long> {
        val normalized = value.lowercase().replace('\u00a0', ' ').trim()
        if (normalized.isEmpty()) return emptyList()
        val now = Instant.ofEpochMilli(nowEpochMs).atZone(ZoneOffset.UTC)
        val values = mutableListOf<Long>()

        RELATIVE.findAll(normalized).forEach { match ->
            val amount = match.groupValues[1].toLongOrNull() ?: return@forEach
            val timestamp = when (unit(match.groupValues[2])) {
                RelativeUnit.SECOND -> now.minusSeconds(amount)
                RelativeUnit.MINUTE -> now.minusMinutes(amount)
                RelativeUnit.HOUR -> now.minusHours(amount)
                RelativeUnit.DAY -> now.minusDays(amount)
                RelativeUnit.WEEK -> now.minusWeeks(amount)
                RelativeUnit.MONTH -> now.minusMonths(amount)
                RelativeUnit.YEAR -> now.minusYears(amount)
                null -> return@forEach
            }
            values.add(timestamp.toInstant().toEpochMilli())
        }
        NATURAL_RELATIVE.findAll(normalized).forEach { match ->
            val timestamp = when (unit(match.groupValues[1])) {
                RelativeUnit.SECOND -> now.minusSeconds(1)
                RelativeUnit.MINUTE -> now.minusMinutes(1)
                RelativeUnit.HOUR -> now.minusHours(1)
                RelativeUnit.DAY -> now.minusDays(1)
                RelativeUnit.WEEK -> now.minusWeeks(1)
                RelativeUnit.MONTH -> now.minusMonths(1)
                RelativeUnit.YEAR -> now.minusYears(1)
                null -> return@forEach
            }
            values.add(timestamp.toInstant().toEpochMilli())
        }
        INDONESIAN_SINGULAR_RELATIVE.findAll(normalized).forEach { match ->
            val timestamp = when (match.groupValues[1]) {
                "sedetik" -> now.minusSeconds(1)
                "semenit" -> now.minusMinutes(1)
                "sejam" -> now.minusHours(1)
                "sehari" -> now.minusDays(1)
                "seminggu" -> now.minusWeeks(1)
                "sebulan" -> now.minusMonths(1)
                "setahun" -> now.minusYears(1)
                else -> return@forEach
            }
            values.add(timestamp.toInstant().toEpochMilli())
        }
        when {
            normalized.contains("just now") ||
                normalized.contains("baru saja") ||
                normalized.contains("today") ||
                normalized.contains("hari ini") ->
                values.add(nowEpochMs)
            normalized.contains("yesterday") || normalized.contains("kemarin") ->
                values.add(now.minusDays(1).toInstant().toEpochMilli())
        }

        ISO_DATE.findAll(normalized).forEach { match ->
            dateEpoch(
                match.groupValues[1].toIntOrNull(),
                match.groupValues[2].toIntOrNull(),
                match.groupValues[3].toIntOrNull(),
            )?.let(values::add)
        }
        val dayMonthMatches = DAY_MONTH.findAll(normalized).toList()
        val monthDayMatches = MONTH_DAY.findAll(normalized).toList()
        dayMonthMatches.forEach { match ->
            val day = match.groupValues[1].toIntOrNull() ?: return@forEach
            val month = month(match.groupValues[2]) ?: return@forEach
            val year = inferredYear(
                match.groupValues[3].toIntOrNull(),
                month,
                day,
                now.toLocalDate(),
            )
            dateEpoch(year, month, day)?.let(values::add)
        }
        monthDayMatches.forEach { match ->
            val month = month(match.groupValues[1]) ?: return@forEach
            val day = match.groupValues[2].toIntOrNull() ?: return@forEach
            val year = inferredYear(
                match.groupValues[3].toIntOrNull(),
                month,
                day,
                now.toLocalDate(),
            )
            dateEpoch(year, month, day)?.let(values::add)
        }
        if (dayMonthMatches.isEmpty() && monthDayMatches.isEmpty()) {
            MONTH_YEAR.findAll(normalized).forEach { match ->
                val month = month(match.groupValues[1]) ?: return@forEach
                val year = match.groupValues[2].toIntOrNull() ?: return@forEach
                if (year !in MIN_YEAR..MAX_YEAR) return@forEach
                val end = YearMonth.of(year, month).atEndOfMonth()
                values.add(end.atTime(23, 59, 59).toInstant(ZoneOffset.UTC).toEpochMilli())
            }
        }
        return values
    }

    private fun inferredYear(explicit: Int?, month: Int, day: Int, now: LocalDate): Int {
        if (explicit != null) return explicit
        val currentYearDate = runCatching { LocalDate.of(now.year, month, day) }.getOrNull()
            ?: return now.year
        return if (currentYearDate.isAfter(now.plusDays(1))) now.year - 1 else now.year
    }

    private fun dateEpoch(year: Int?, month: Int?, day: Int?): Long? {
        if (year == null || month == null || day == null || year !in MIN_YEAR..MAX_YEAR) return null
        return runCatching {
            LocalDate.of(year, month, day)
                .atTime(23, 59, 59)
                .toInstant(ZoneOffset.UTC)
                .toEpochMilli()
        }.getOrNull()
    }

    private fun unit(value: String): RelativeUnit? = when (value.lowercase()) {
        "s", "sec", "secs", "second", "seconds", "detik" -> RelativeUnit.SECOND
        "m", "min", "mins", "minute", "minutes", "menit" -> RelativeUnit.MINUTE
        "h", "hr", "hrs", "hour", "hours", "jam" -> RelativeUnit.HOUR
        "d", "day", "days", "hari" -> RelativeUnit.DAY
        "w", "week", "weeks", "minggu", "mgg" -> RelativeUnit.WEEK
        "mo", "mos", "month", "months", "bulan", "bln" -> RelativeUnit.MONTH
        "y", "yr", "yrs", "year", "years", "tahun", "thn" -> RelativeUnit.YEAR
        else -> null
    }

    private fun month(value: String): Int? = MONTHS[value.lowercase().trimEnd('.')]

    private enum class RelativeUnit {
        SECOND,
        MINUTE,
        HOUR,
        DAY,
        WEEK,
        MONTH,
        YEAR,
    }

    private val RELATIVE = Regex(
        """(?i)(?:^|[\s,.•·])([0-9]{1,3})\s*(seconds?|secs?|sec|detik|minutes?|mins?|min|menit|hours?|hrs?|hr|jam|days?|hari|weeks?|minggu|mgg|months?|mos?|mo|bulan|bln|years?|yrs?|yr|tahun|thn|[smhdwy])(?:\s+ago)?(?:$|[\s,.•·])""",
    )
    private val NATURAL_RELATIVE = Regex(
        """(?i)\b(?:a|an|one)\s+(second|minute|hour|day|week|month|year)\s+ago\b""",
    )
    private val INDONESIAN_SINGULAR_RELATIVE = Regex(
        """\b(sedetik|semenit|sejam|sehari|seminggu|sebulan|setahun)(?:\s+(?:yang\s+)?lalu)?\b""",
    )
    private val ISO_DATE = Regex("""\b(20[0-9]{2})-([0-9]{1,2})-([0-9]{1,2})\b""")
    private val DAY_MONTH = Regex(
        """(?i)\b([0-9]{1,2})\s+(jan(?:uary|uari)?|feb(?:ruary|ruari)?|mar(?:ch|et)?|apr(?:il)?|may|mei|jun(?:e|i)?|jul(?:y|i)?|aug(?:ust)?|agu(?:stus)?|sep(?:t(?:ember)?)?|okt(?:ober)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|des(?:ember)?)[.]?(?:\s+([12][0-9]{3}))?\b""",
    )
    private val MONTH_DAY = Regex(
        """(?i)\b(jan(?:uary|uari)?|feb(?:ruary|ruari)?|mar(?:ch|et)?|apr(?:il)?|may|mei|jun(?:e|i)?|jul(?:y|i)?|aug(?:ust)?|agu(?:stus)?|sep(?:t(?:ember)?)?|okt(?:ober)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|des(?:ember)?)[.]?\s+([0-9]{1,2})(?:,?\s+([12][0-9]{3}))?\b""",
    )
    private val MONTH_YEAR = Regex(
        """(?i)\b(jan(?:uary|uari)?|feb(?:ruary|ruari)?|mar(?:ch|et)?|apr(?:il)?|may|mei|jun(?:e|i)?|jul(?:y|i)?|aug(?:ust)?|agu(?:stus)?|sep(?:t(?:ember)?)?|okt(?:ober)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|des(?:ember)?)[.]?\s+([12][0-9]{3})\b""",
    )
    private val MONTHS = mapOf(
        "jan" to 1, "january" to 1, "januari" to 1,
        "feb" to 2, "february" to 2, "februari" to 2,
        "mar" to 3, "march" to 3, "maret" to 3,
        "apr" to 4, "april" to 4,
        "may" to 5, "mei" to 5,
        "jun" to 6, "june" to 6, "juni" to 6,
        "jul" to 7, "july" to 7, "juli" to 7,
        "aug" to 8, "august" to 8, "agu" to 8, "agustus" to 8,
        "sep" to 9, "sept" to 9, "september" to 9,
        "oct" to 10, "october" to 10, "okt" to 10, "oktober" to 10,
        "nov" to 11, "november" to 11,
        "dec" to 12, "december" to 12, "des" to 12, "desember" to 12,
    )
    private const val MIN_YEAR = 2000
    private const val MAX_YEAR = 2200
}
