package com.siksik.agent.automation

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FacebookMenuSurfacePolicyTest {
    @Test
    fun detectsSamsungMenuDrawerFromDiagnosisSample() {
        assertTrue(
            FacebookMenuSurfacePolicy.looksLikeMenuDrawer(
                listOf(
                    "Bantuan dan dukungan, Judul.",
                    "Pengaturan dan privasi, Judul.",
                    "Upgrade, Judul.",
                    "Facebook Plus, tombol 2 dari 2",
                    "Profil, Tab 5 dari 5",
                    "Menu",
                    "Cari",
                ),
            ),
        )
    }

    @Test
    fun profileTimelineIsNotMenuDrawer() {
        assertFalse(
            FacebookMenuSurfacePolicy.looksLikeMenuDrawer(
                listOf(
                    "Saipul Tes",
                    "Edit profile",
                    "2 friends",
                    "All posts",
                ),
            ),
        )
    }
}
