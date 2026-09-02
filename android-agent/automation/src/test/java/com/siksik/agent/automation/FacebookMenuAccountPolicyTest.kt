package com.siksik.agent.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FacebookMenuAccountPolicyTest {
    private fun candidate(
        labels: List<String>,
        className: String,
        left: Int,
        top: Int,
        width: Int,
        height: Int,
    ) = FacebookMenuAccountPolicy.LabeledBounds(
        labels = labels,
        className = className,
        left = left,
        top = top,
        width = width,
        height = height,
    )

    @Test
    fun infersNameOnlyFromProfileRowCompoundLabel() {
        assertEquals(
            "Jane Doe",
            FacebookMenuAccountPolicy.inferAccountName(
                listOf(
                    "Jane Doe, see your profile",
                    "Create story",
                    "Menu",
                ),
            ),
        )
    }

    @Test
    fun doesNotGuessMarkerFromMenuShortcuts() {
        assertNull(
            FacebookMenuAccountPolicy.inferAccountName(
                listOf(
                    "Kabar Beranda",
                    "Kabar Beranda, tombol 1 dari 15",
                    "Meta AI",
                    "Menu",
                ),
            ),
        )
    }

    @Test
    fun infersFromBoundedCandidatesDespiteMenuNoise() {
        val marker = FacebookMenuAccountPolicy.inferAccountNameFromCandidates(
            listOf(
                candidate(
                    labels = listOf("Kabar Beranda, tombol 1 dari 15"),
                    className = "android.view.ViewGroup",
                    left = 34,
                    top = 510,
                    width = 888,
                    height = 114,
                ),
                candidate(
                    labels = listOf("Jane Doe, see your profile"),
                    className = "android.widget.Button",
                    left = 68,
                    top = 155,
                    width = 404,
                    height = 113,
                ),
            ),
        )
        assertEquals("Jane Doe", marker)
    }

    @Test
    fun resolvesMarkerAndTapTargetTogether() {
        val candidates = listOf(
            candidate(
                labels = listOf("profile photo"),
                className = "android.widget.ImageView",
                left = 68,
                top = 155,
                width = 113,
                height = 113,
            ),
            candidate(
                labels = listOf("Jane Doe, see your profile"),
                className = "android.widget.Button",
                left = 68,
                top = 155,
                width = 404,
                height = 113,
            ),
            candidate(
                labels = listOf("Jane Doe"),
                className = "android.view.ViewGroup",
                left = 215,
                top = 194,
                width = 223,
                height = 35,
            ),
        )
        val resolved = FacebookMenuAccountPolicy.resolveMenuProfileUsernameTap(candidates)
        assertEquals("Jane Doe", resolved?.first)
        assertEquals(223, resolved?.second?.width)
        assertEquals(35, resolved?.second?.height)
    }

    @Test
    fun rejectsStoryShortcutTapTargets() {
        assertNull(
            FacebookMenuAccountPolicy.selectMenuNameTapTarget(
                listOf(
                    candidate(
                        labels = listOf("Create story"),
                        className = "android.widget.Button",
                        left = 34,
                        top = 307,
                        width = 200,
                        height = 120,
                    ),
                ),
                marker = "Create story",
            ),
        )
    }

    @Test
    fun parsesProfileRowFromShellDumpSnippet() {
        val xml = """
            <hierarchy>
              <node class="android.widget.Button" content-desc="Jane Doe, see your profile"
                bounds="[68,155][472,268]" text="" />
              <node class="android.view.ViewGroup" content-desc="Jane Doe" text="Jane Doe"
                bounds="[215,194][438,229]" />
            </hierarchy>
        """.trimIndent()
        val candidates = FacebookMenuAccountPolicy.parseShellDumpCandidates(xml)
        val resolved = FacebookMenuAccountPolicy.resolveMenuProfileUsernameTap(candidates)
        assertEquals("Jane Doe", resolved?.first)
        assertEquals(223, resolved?.second?.width)
    }

    @Test
    fun parseShellDumpCandidatesIgnoresOtherPackagesWhenFiltered() {
        val xml = """
            <hierarchy>
              <node package="com.siksik.agent" content-desc="Menu" class="android.widget.Button"
                bounds="[11,70][135,194]" text="" />
              <node package="com.facebook.katana" content-desc="Jane Doe, see your profile"
                class="android.widget.Button" bounds="[68,155][472,268]" text="" />
            </hierarchy>
        """.trimIndent()
        val filtered = FacebookMenuAccountPolicy.parseShellDumpCandidates(
            xml,
            "com.facebook.katana",
        )
        assertEquals(1, filtered.size)
        assertTrue(filtered[0].labels.any { it.contains("Jane Doe") })
    }

    @Test
    fun hasMenuProfileRowWhenCompoundLabelPresent() {
        assertTrue(
            FacebookMenuAccountPolicy.hasMenuProfileRow(
                listOf(
                    candidate(
                        labels = listOf("Jane Doe, see your profile"),
                        className = "android.widget.Button",
                        left = 68,
                        top = 155,
                        width = 404,
                        height = 113,
                    ),
                ),
            ),
        )
        assertFalse(
            FacebookMenuAccountPolicy.hasMenuProfileRow(
                listOf(
                    candidate(
                        labels = listOf("Menu", "Home, Tab 1 of 5", "Upgrade"),
                        className = "android.widget.Button",
                        left = 11,
                        top = 70,
                        width = 124,
                        height = 124,
                    ),
                ),
            ),
        )
    }

    @Test
    fun selectsTopmostMenuButtonAmongCandidates() {
        val chosen = FacebookMenuAccountPolicy.selectTopmostMenuButton(
            listOf(
                candidate(
                    labels = listOf("Profile, Tab 5 of 5"),
                    className = "android.view.View",
                    left = 864,
                    top = 70,
                    width = 216,
                    height = 124,
                ),
                candidate(
                    labels = listOf("Menu"),
                    className = "android.widget.Button",
                    left = 11,
                    top = 70,
                    width = 124,
                    height = 124,
                ),
                candidate(
                    labels = listOf("Menu"),
                    className = "android.widget.Button",
                    left = 967,
                    top = 70,
                    width = 113,
                    height = 124,
                ),
            ),
            maxLeft = 540,
        )
        assertEquals(11, chosen?.left)
        assertEquals(70, chosen?.top)
    }

    @Test
    fun rejectsCloseMenuChromeWhenSelectingHamburger() {
        val chosen = FacebookMenuAccountPolicy.selectTopmostMenuButton(
            listOf(
                candidate(
                    labels = listOf("Tutup menu"),
                    className = "android.widget.Button",
                    left = 967,
                    top = 70,
                    width = 113,
                    height = 124,
                ),
                candidate(
                    labels = listOf("Menu"),
                    className = "android.widget.Button",
                    left = 11,
                    top = 70,
                    width = 124,
                    height = 124,
                ),
            ),
            maxLeft = 540,
        )
        assertEquals(11, chosen?.left)
    }

    @Test
    fun ranksMultipleUsernameTapTargetsByAspectRatio() {
        val targets = FacebookMenuAccountPolicy.selectMenuNameTapTargets(
            listOf(
                candidate(
                    labels = listOf("Jane Doe"),
                    className = "android.view.ViewGroup",
                    left = 215,
                    top = 194,
                    width = 223,
                    height = 35,
                ),
                candidate(
                    labels = listOf("Jane Doe"),
                    className = "android.widget.TextView",
                    left = 240,
                    top = 198,
                    width = 120,
                    height = 28,
                ),
            ),
            marker = "Jane Doe",
            limit = 2,
        )
        assertEquals(2, targets.size)
        assertEquals(223, targets[0].width)
        assertEquals(120, targets[1].width)
    }

    @Test
    fun preferredMenuNameTapPointUsesTextHeavyZoneWithinBounds() {
        val target = candidate(
            labels = listOf("Jane Doe"),
            className = "android.view.ViewGroup",
            left = 215,
            top = 194,
            width = 223,
            height = 35,
        )
        val (tapX, tapY) = FacebookMenuAccountPolicy.preferredMenuNameTapPoint(target)
        assertEquals(215 + (223 * 2 / 3), tapX)
        assertEquals(194 + 35 / 2, tapY)
    }
}
