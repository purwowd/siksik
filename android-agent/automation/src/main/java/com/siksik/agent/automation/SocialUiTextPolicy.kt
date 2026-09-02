package com.siksik.agent.automation

import java.util.Locale

internal object SocialUiTextPolicy {
    private val facebookSuggestionFragments = setOf(
        "people you may know",
        "orang yang mungkin anda kenal",
        "friend suggestions",
        "saran teman",
        "remove friend suggestion",
        "hapus saran teman",
    )
    private val xProfileMetadataPrefixes = setOf(
        "born ",
        "lahir ",
        "joined ",
        "bergabung ",
        "followed by ",
        "diikuti oleh ",
    )

    /** Empty-state copy on X profile timelines (EN + ID, including recent X app variants). */
    val xEmptyTimelineLabels = listOf(
        "No posts yet",
        "No replies yet",
        "Belum ada postingan",
        "Belum ada balasan",
        "hasn't posted",
        "hasn’t posted",
        "haven't posted",
        "haven’t posted",
        "You haven't posted yet",
        "You haven’t posted yet",
        "When you send posts or replies",
        "belum memposting",
        "Post now",
        "Posting sekarang",
        "Kirim postingan",
        "Jika sudah, Anda dapat melihatnya di sini.",
        "Belum ada apa-apa di sini.",
        "belum ada apa-apa",
        "When you post",
        "Share your thoughts",
        "Share a post",
        "Bagikan pemikiran",
    )

    val AUTH_WALL_EXACT_LABELS = listOf(
        "Log in",
        "Log In",
        "Login",
        "LOG IN",
        "Masuk",
        "Sign in",
        "Sign In",
        "Sign up",
        "Sign Up",
        "Create account",
        "Create Account",
        "Create new account",
        "Create New Account",
        "Buat akun",
        "Buat Akun",
        "Buat akun baru",
        "Buat Akun Baru",
        "Daftar",
        "Use another profile",
        "Use Another Profile",
        "Gunakan profil lain",
        "Gunakan profil lainnya",
        "Forgot password?",
        "Forgot password",
        "Lupa kata sandi?",
        "Lupa kata sandi",
        "Join Facebook",
        "Gabung Facebook",
    )

    // UiAutomator By.textContains is case-sensitive; keep mixed-case variants.
    val AUTH_WALL_DEVICE_FRAGMENTS = listOf(
        "Use another profile",
        "use another profile",
        "Gunakan profil lain",
        "gunakan profil lain",
        "Create new account",
        "create new account",
        "Buat akun baru",
        "buat akun baru",
        "Forgot password",
        "forgot password",
        "Lupa kata sandi",
        "lupa kata sandi",
        "use phone or email",
        "Use phone or email",
        "gunakan nomor ponsel atau email",
        "already have an account",
        "Already have an account",
        "sudah punya akun",
        "create your account",
        "Create your account",
        "buat akun anda",
        "Join Facebook",
        "join facebook",
        "Gabung Facebook",
        "gabung facebook",
        "Find your account",
        "find your account",
        "Cari akun Anda",
        "cari akun anda",
        "Log in to Facebook",
        "log in to facebook",
        "Masuk ke Facebook",
        "masuk ke facebook",
    )

    private val authWallPhrases = listOf(
        "use another profile",
        "gunakan profil lain",
        "create new account",
        "buat akun baru",
        "forgot password",
        "lupa kata sandi",
        "use phone or email",
        "gunakan nomor ponsel atau email",
        "already have an account",
        "sudah punya akun",
        "create your account",
        "buat akun anda",
        "create your new account",
        "join facebook",
        "gabung facebook",
        "find your account",
        "cari akun anda",
        "log in to facebook",
        "masuk ke facebook",
        "create new facebook account",
    )

    private val authWallNormalizedExact = setOf(
        "log in",
        "login",
        "masuk",
        "sign in",
        "sign up",
        "create account",
        "create new account",
        "buat akun",
        "buat akun baru",
        "daftar",
        "use another profile",
        "gunakan profil lain",
        "gunakan profil lainnya",
        "forgot password?",
        "forgot password",
        "lupa kata sandi?",
        "lupa kata sandi",
        "join facebook",
        "gabung facebook",
    )

    fun isFacebookSuggestionText(value: String): Boolean {
        val normalized = value.trim().lowercase(Locale.ROOT)
        return facebookSuggestionFragments.any(normalized::contains)
    }

    fun isXProfileMetadataLine(value: String): Boolean {
        val normalized = value.trim().lowercase(Locale.ROOT)
        return xProfileMetadataPrefixes.any(normalized::startsWith)
    }

    fun isXEmptyTimelineText(value: String): Boolean =
        xEmptyTimelineLabels.any { phrase -> value.contains(phrase, ignoreCase = true) }

    fun labelsContainXEmptyTimeline(labels: Iterable<String>): Boolean =
        labels.any(::isXEmptyTimelineText)

    fun isAuthWallText(value: String): Boolean {
        val normalized = normalizeAuthLabel(value)
        if (normalized.isEmpty()) return false
        if (normalized in authWallNormalizedExact) return true
        return authWallPhrases.any(normalized::contains)
    }

    fun looksLikeAuthWall(labels: Iterable<String>): Boolean {
        if (labels.any(::isAuthWallText)) return true
        val normalized = labels.map(::normalizeAuthLabel).filter { it.isNotEmpty() }
        val hasPassword = normalized.any { it == "password" || it == "kata sandi" }
        val hasLogin = normalized.any { it == "log in" || it == "login" || it == "masuk" }
        return hasPassword && hasLogin
    }

    fun looksLikeAuthWallDump(dump: String): Boolean {
        if (dump.isBlank()) return false
        val lower = dump.lowercase(Locale.ROOT)
        if (authWallPhrases.any(lower::contains)) return true
        val hasPasswordField =
            lower.contains("text=\"password\"") ||
                lower.contains("text=\"kata sandi\"") ||
                lower.contains("content-desc=\"password\"") ||
                lower.contains("content-desc=\"kata sandi\"")
        val hasLoginAction = listOf("log in", "login", "masuk").any { action ->
            lower.contains("text=\"$action\"") ||
                lower.contains("content-desc=\"$action\"")
        }
        return hasPasswordField && hasLoginAction
    }

    fun parseFocusedActivityComponent(windowDump: String): String? {
        val patterns = listOf(
            Regex("""mCurrentFocus=Window\{[^\n]*\s([\w.]+)/([^\s}]+)"""),
            Regex("""mFocusedApp=ActivityRecord\{[^\n]*\s([\w.]+)/(\S+)"""),
            Regex("""topResumedActivity=ActivityRecord\{[^\n]*\s([\w.]+)/(\S+)"""),
        )
        for (pattern in patterns) {
            val match = pattern.find(windowDump) ?: continue
            val pkg = match.groupValues[1]
            val cls = match.groupValues[2].trimEnd('}')
            if (pkg.isNotEmpty() && cls.isNotEmpty()) return "$pkg/$cls"
        }
        return null
    }

    fun isFacebookLoginComponent(component: String): Boolean {
        val normalized = component.trim().lowercase(Locale.ROOT)
        if (!normalized.contains("com.facebook.katana")) return false
        val activity = normalized.substringAfterLast('/')
            .trimStart('.')
            .substringAfterLast('.')
        return activity == "loginactivity" ||
            activity.endsWith("loginactivity") ||
            activity.contains("loggedout")
    }

    fun looksLikeFacebookLoginDump(dump: String): Boolean {
        if (dump.isBlank()) return false
        val lower = dump.lowercase(Locale.ROOT)
        if (!lower.contains("com.facebook.katana")) return false
        if (looksLikeFacebookHomeFeedDump(dump)) return false
        if (lower.contains("loggedout")) return true
        return facebookLoginPhrases.any(lower::contains)
    }

    fun looksLikeFacebookHomeFeedDump(dump: String): Boolean {
        if (dump.isBlank()) return false
        val lower = dump.lowercase(Locale.ROOT)
        if (!lower.contains("com.facebook.katana")) return false
        return facebookHomeFeedPhrases.any(lower::contains)
    }

    fun looksLikeFacebookHomeFeedLabels(labels: Iterable<String>): Boolean {
        val joined = labels
            .map { it.trim().lowercase(Locale.ROOT) }
            .filter(String::isNotEmpty)
            .joinToString(" ")
        if (joined.isEmpty()) return false
        return facebookHomeFeedPhrases.any(joined::contains)
    }

    fun looksLikeFacebookLoginLabels(labels: Iterable<String>): Boolean {
        val joined = labels
            .map { it.trim().lowercase(Locale.ROOT) }
            .filter(String::isNotEmpty)
            .joinToString(" ")
        if (joined.isEmpty()) return false
        return facebookLoginPhrases.any(joined::contains)
    }

    private val facebookHomeFeedPhrases = listOf(
        "what's on your mind",
        "on your mind",
        "apa yang anda pikirkan",
        "di pikiranmu",
        "beranda, tab",
        "home, tab",
        "logo facebook",
        "buka profil",
        "open profile",
        "pengiriman pesan",
        "messaging",
    )

    private val facebookLoginPhrases = listOf(
        "join facebook",
        "gabung facebook",
        "find your account",
        "cari akun anda",
        "log in to facebook",
        "masuk ke facebook",
        "create new facebook account",
        "create new account",
        "buat akun baru",
    )

    private fun normalizeAuthLabel(value: String): String =
        value.trim().lowercase(Locale.ROOT)
}
