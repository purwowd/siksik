package com.siksik.agent.automation

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FacebookPostsSurfacePolicyTest {
    @Test
    fun detectsAllPostsSurfaceLabelsInEnglishAndIndonesian() {
        assertTrue(FacebookPostsSurfacePolicy.looksLikeAllPostsSurfaceLabel("All posts"))
        assertTrue(FacebookPostsSurfacePolicy.looksLikeAllPostsSurfaceLabel("Semua postingan"))
        assertTrue(FacebookPostsSurfacePolicy.looksLikeAllPostsSurfaceLabel("All, 1 of 3"))
        assertFalse(FacebookPostsSurfacePolicy.looksLikeAllPostsSurfaceLabel("People you may know"))
    }

    @Test
    fun detectsEmptyPostsLabels() {
        assertTrue(
            FacebookPostsSurfacePolicy.looksLikeEmptyPosts(
                listOf("No posts yet", "Friends"),
            ),
        )
        assertTrue(
            FacebookPostsSurfacePolicy.looksLikeEmptyPosts(
                listOf("Belum ada postingan"),
            ),
        )
        assertFalse(
            FacebookPostsSurfacePolicy.looksLikeEmptyPosts(
                listOf("Saipul Tes", "Edit profile"),
            ),
        )
    }

    @Test
    fun detectsProfileMediaTabsAndPhotosSubtab() {
        val wallLabels = listOf(
            "Semua, 1 dari 3",
            "Semua",
            "Foto, 2 dari 3",
            "Postingan, 1 dari 3",
        )
        assertTrue(FacebookPostsSurfacePolicy.looksLikeProfileMediaTabs(wallLabels))
        assertTrue(FacebookPostsSurfacePolicy.looksLikeProfilePhotosSubtabActive(wallLabels))
        val postsSelected = listOf(
            "Semua, 1 dari 3",
            "Postingan, 1 dari 3",
            "Foto",
        )
        assertTrue(FacebookPostsSurfacePolicy.looksLikeProfileMediaTabs(postsSelected))
        assertFalse(FacebookPostsSurfacePolicy.looksLikeProfilePhotosSubtabActive(postsSelected))
    }
}
