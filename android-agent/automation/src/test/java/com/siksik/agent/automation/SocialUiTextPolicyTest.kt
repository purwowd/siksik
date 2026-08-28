package com.siksik.agent.automation

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
}
