package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SocialUiTextPolicyTest {
    @Test
    fun filtersFacebookSuggestionChromeInEnglishAndIndonesian() {
        assertTrue(SocialUiTextPolicy.isFacebookSuggestionText("People you may know"))
        assertTrue(SocialUiTextPolicy.isFacebookSuggestionText("Orang yang mungkin Anda kenal"))
        assertFalse(SocialUiTextPolicy.isFacebookSuggestionText("Postingan milik pengguna"))
    }

    @Test
    fun filtersXProfileMetadataInEnglishAndIndonesian() {
        assertTrue(SocialUiTextPolicy.isXProfileMetadataLine("Born December 15, 1998"))
        assertTrue(SocialUiTextPolicy.isXProfileMetadataLine("Lahir Desember 15, 1998"))
        assertFalse(SocialUiTextPolicy.isXProfileMetadataLine("Isi balasan pengguna"))
    }

    @Test
    fun detectsInstagramSavedAccountChooser() {
        assertTrue(
            SocialUiTextPolicy.looksLikeAuthWall(
                listOf("saved.user", "Use another profile", "Create new account", "Meta"),
            ),
        )
        assertTrue(SocialUiTextPolicy.isAuthWallText("Gunakan profil lain"))
        assertTrue(SocialUiTextPolicy.isAuthWallText("Buat akun baru"))
        assertTrue(
            SocialUiTextPolicy.looksLikeAuthWallDump(
                """<node text="Use another profile"/><node text="Create new account"/>""",
            ),
        )
    }

    @Test
    fun detectsInstagramSavedAccountPasswordWall() {
        assertTrue(
            SocialUiTextPolicy.looksLikeAuthWall(
                listOf("Password", "Log in", "Forgot password?"),
            ),
        )
        assertTrue(SocialUiTextPolicy.isAuthWallText("Forgot password?"))
        assertTrue(SocialUiTextPolicy.isAuthWallText("Lupa kata sandi?"))
        assertTrue(
            SocialUiTextPolicy.looksLikeAuthWallDump(
                """<node text="Password"/><node text="Log in"/><node text="Forgot password?"/>""",
            ),
        )
    }

    @Test
    fun doesNotTreatOwnProfileChromeAsAuthWall() {
        assertFalse(
            SocialUiTextPolicy.looksLikeAuthWall(
                listOf("Edit profile", "Share profile", "followers", "following", "posts"),
            ),
        )
        assertFalse(SocialUiTextPolicy.isAuthWallText("Create your first post"))
        assertFalse(SocialUiTextPolicy.looksLikeAuthWall(listOf("Password")))
        assertFalse(
            SocialUiTextPolicy.looksLikeAuthWallDump(
                """<node text="Edit profile"/><node text="Share profile"/>""",
            ),
        )
    }

    @Test
    fun detectsFacebookLoginActivityComponent() {
        assertTrue(
            SocialUiTextPolicy.isFacebookLoginComponent(
                "com.facebook.katana/.LoginActivity",
            ),
        )
        assertTrue(
            SocialUiTextPolicy.isFacebookLoginComponent(
                "com.facebook.katana/com.facebook.katana.LoginActivity",
            ),
        )
        assertFalse(
            SocialUiTextPolicy.isFacebookLoginComponent(
                "com.facebook.katana/.FbMainTabActivity",
            ),
        )
        assertEquals(
            "com.facebook.katana/.LoginActivity",
            SocialUiTextPolicy.parseFocusedActivityComponent(
                "    topResumedActivity=ActivityRecord{c76c288 u0 com.facebook.katana/.LoginActivity t40}",
            ),
        )
        assertEquals(
            "com.facebook.katana/com.facebook.katana.LoginActivity",
            SocialUiTextPolicy.parseFocusedActivityComponent(
                "  mCurrentFocus=Window{77530a2 u0 com.facebook.katana/com.facebook.katana.LoginActivity}",
            ),
        )
        assertTrue(
            SocialUiTextPolicy.looksLikeFacebookLoginDump(
                """<node package="com.facebook.katana" class="com.facebook.katana.LoginActivity" text="Join Facebook"/>""",
            ),
        )
        assertFalse(
            SocialUiTextPolicy.looksLikeFacebookLoginDump(
                """<node package="com.facebook.katana" text="Edit profile"/>""",
            ),
        )
    }
}
