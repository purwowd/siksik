package com.siksik.agent

import com.siksik.agent.model.ApiException
import com.siksik.agent.selection.SelectionSet
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SelectionSetTest {
    @Test
    fun snapshotsValidUniqueSelection() {
        val source = mutableListOf("media_fixture_1", "media_fixture_2")
        val selection = SelectionSet.validated(source, 2)
        source.clear()

        assertEquals(listOf("media_fixture_1", "media_fixture_2"), selection.itemIds)
    }

    @Test
    fun rejectsEmptyDuplicateUnsafeAndOversizedSelections() {
        assertThrows(ApiException::class.java) { SelectionSet.validated(emptyList(), 2) }
        assertThrows(ApiException::class.java) {
            SelectionSet.validated(listOf("media_fixture", "media_fixture"), 2)
        }
        assertThrows(ApiException::class.java) { SelectionSet.validated(listOf("bad"), 2) }
        assertThrows(ApiException::class.java) {
            SelectionSet.validated(listOf("media_fixture_1", "media_fixture_2"), 1)
        }
    }
}
