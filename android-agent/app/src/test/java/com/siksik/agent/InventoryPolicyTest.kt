package com.siksik.agent

import com.siksik.agent.source.inventory.InventoryPolicy
import com.siksik.agent.source.inventory.InventorySourceKind
import com.siksik.agent.source.inventory.SourceAdapter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class InventoryPolicyTest {
    @Test
    fun sourceAdapterWireOrderCoversFlowStepTwoAndThreeSources() {
        assertEquals(
            listOf(
                "public_whatsapp",
                "public_telegram",
                "media_store_image",
                "media_store_video",
                "media_store_audio",
                "shared_storage_document",
                "document_tree",
                "sms_content_provider",
                "contacts_content_provider",
                "accessibility_visible_ui",
                "notification_listener",
            ),
            SourceAdapter.entries.map(SourceAdapter::wireName),
        )
    }

    @Test
    fun everyRequiredDocumentExtensionHasStableMimeAndKind() {
        val expected = mapOf(
            "pdf" to "application/pdf",
            "doc" to "application/msword",
            "docx" to "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xls" to "application/vnd.ms-excel",
            "xlsx" to "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "csv" to "text/csv",
            "txt" to "text/plain",
            "rtf" to "application/rtf",
        )

        expected.forEach { (extension, mime) ->
            val name = "fixture.$extension"
            assertEquals(mime, InventoryPolicy.normalizedMime(null, name))
            assertTrue(InventoryPolicy.isSupportedDocument(mime, name))
            assertEquals(InventorySourceKind.DOCUMENT, InventoryPolicy.sourceKind(mime, name))
        }
        assertEquals(expected.keys, InventoryPolicy.documentExtensions)
    }

    @Test
    fun sourceKindsCoverImageVideoAudioAndRejectUnknownBinary() {
        assertEquals(
            InventorySourceKind.MEDIA_IMAGE,
            InventoryPolicy.sourceKind("image/jpeg", "fixture.jpg"),
        )
        assertEquals(
            InventorySourceKind.MEDIA_VIDEO,
            InventoryPolicy.sourceKind("video/mp4", "fixture.mp4"),
        )
        assertEquals(
            InventorySourceKind.MEDIA_AUDIO,
            InventoryPolicy.sourceKind("audio/mpeg", "fixture.mp3"),
        )
        assertNull(InventoryPolicy.sourceKind("application/octet-stream", "fixture.bin"))
    }

    @Test
    fun traversalAndControlCharactersAreRemovedAndValuesAreBounded() {
        assertEquals("secret/DCIM/Cam_era", InventoryPolicy.normalizedDirectoryHint(
            "../../secret/../DCIM/Cam\u0000era/",
        ))
        assertEquals("folder_name_.pdf", InventoryPolicy.safeDisplayName("folder/name\u0000.pdf"))
        assertNull(InventoryPolicy.normalizedDirectoryHint("/.././"))

        val longPath = (1..40).joinToString("/") { "directory$it" }
        val normalized = InventoryPolicy.normalizedDirectoryHint(longPath)!!
        assertTrue(normalized.length <= 512)
        assertTrue(normalized.split('/').size <= 24)
        assertFalse(".." in normalized.split('/'))
    }

    @Test
    fun identifiersAreOpaqueStableAndAdapterBound() {
        val first = InventoryPolicy.identityHash("external:42")
        val second = InventoryPolicy.identityHash("external:42")

        assertEquals(first, second)
        assertEquals(64, first.length)
        assertTrue(InventoryPolicy.recordId(first).startsWith("record_"))
        assertTrue(
            InventoryPolicy.sourceLocator(SourceAdapter.MEDIA_IMAGE, first)
                .startsWith("media_store_image:"),
        )
    }

    @Test
    fun nestedPublicApplicationFoldersAreRecognized() {
        assertTrue(
            InventoryPolicy.isPublicDirectory(
                SourceAdapter.PUBLIC_WHATSAPP,
                "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Sent",
            ),
        )
        assertTrue(
            InventoryPolicy.isPublicDirectory(
                SourceAdapter.PUBLIC_TELEGRAM,
                "Telegram/Telegram Images/Profile",
            ),
        )
        assertFalse(
            InventoryPolicy.isPublicDirectory(
                SourceAdapter.PUBLIC_WHATSAPP,
                "Pictures/Screenshots",
            ),
        )
        assertEquals(
            4,
            InventoryPolicy.publicSqlPatterns(SourceAdapter.PUBLIC_WHATSAPP).size,
        )
    }

    @Test
    fun favoriteTokensMatchAlbumAndFlagPaths() {
        assertTrue(InventoryPolicy.looksFavorite("Pictures/Favorites", "photo.jpg"))
        assertTrue(InventoryPolicy.looksFavorite("DCIM/Favorit", null))
        assertTrue(InventoryPolicy.looksFavorite(null, "favourite-001.jpg"))
        assertFalse(InventoryPolicy.looksFavorite("DCIM/Camera", "IMG_0001.jpg"))
        assertEquals(listOf("%favorit%", "%favourite%"), InventoryPolicy.favoriteSqlLikePatterns)
    }

    @Test
    fun sharedPathFingerprintDeduplicatesMediaStoreAndDocumentTreeVisibility() {
        val mediaStore = InventoryPolicy.overlapDedupeHash(
            "media-fallback",
            "Fixture.JPG",
            "image/jpeg",
            1024,
            1_700_000_000_000,
            "DCIM/Camera",
        )
        val documentTree = InventoryPolicy.overlapDedupeHash(
            "tree-fallback",
            "fixture.jpg",
            "IMAGE/JPEG",
            1024,
            1_700_000_000_000,
            "/DCIM/Camera/",
        )

        assertEquals(mediaStore, documentTree)
        assertEquals(
            "fallback-without-path",
            InventoryPolicy.overlapDedupeHash(
                "fallback-without-path",
                "fixture.jpg",
                "image/jpeg",
                1024,
                1_700_000_000_000,
                null,
            ),
        )
    }
}
