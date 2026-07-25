package com.siksik.agent

import com.siksik.agent.model.ApiException
import com.siksik.agent.source.media.normalizedDirectoryHint
import com.siksik.agent.source.media.safeDisplayName
import com.siksik.agent.staging.StagePathPolicy
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class PathPolicyTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun mediaPathPreservesSafeHierarchy() {
        assertEquals("DCIM/Camera", normalizedDirectoryHint("/DCIM/Camera/"))
        assertEquals("Pictures/Screenshot", normalizedDirectoryHint("Pictures/Screenshot/"))
    }

    @Test
    fun mediaPathRemovesUnsafeSegmentsAndControlCharacters() {
        assertEquals("DCIM/Cam_era", normalizedDirectoryHint("../DCIM/Cam\u0000era"))
        assertEquals("Folder_name", normalizedDirectoryHint("Folder\\name"))
        assertNull(normalizedDirectoryHint("/.././"))
        assertEquals("___", safeDisplayName("/\\\u0000"))
    }

    @Test
    fun mediaPathIsBounded() {
        val value = normalizedDirectoryHint((1..32).joinToString("/") { "folder$it" })!!

        assertTrue(value.split('/').size <= 16)
        assertTrue(value.length <= 256)
    }

    @Test
    fun stagePathRejectsTraversal() {
        val root = temporaryFolder.newFolder("root")

        assertThrows(ApiException::class.java) {
            StagePathPolicy.controlledChild(root, "../outside")
        }
    }

    @Test
    fun stagedNameRemovesSeparatorsAndControlCharacters() {
        val value = StagePathPolicy.safeStagedName("artifact_fixture", "folder/a\\b\u0000.jpg")

        assertEquals("artifact_fixture__folder_a_b_.jpg", value)
        assertTrue(value.length <= "artifact_fixture__".length + 160)
    }
}
