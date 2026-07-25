package com.siksik.agent

import android.Manifest
import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.exifinterface.media.ExifInterface
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siksik.agent.source.inventory.ExifMetadataReader
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExifMetadataReaderTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val resolver = context.contentResolver

    @Test
    fun exifReaderHandlesPresentRestrictedAndMalformedMetadata() {
        val image = createImage("exif-${UUID.randomUUID()}.jpg")
        val malformed = createMalformedImage("malformed-${UUID.randomUUID()}.jpg")
        try {
            resolver.openFileDescriptor(image, "rw")!!.use { descriptor ->
                ExifInterface(descriptor.fileDescriptor).apply {
                    setAttribute(ExifInterface.TAG_MAKE, "Fixture Camera")
                    setAttribute(ExifInterface.TAG_MODEL, "Fixture Model")
                    setAttribute(
                        ExifInterface.TAG_ORIENTATION,
                        ExifInterface.ORIENTATION_ROTATE_90.toString(),
                    )
                    setAttribute(ExifInterface.TAG_DATETIME_ORIGINAL, "2026:07:16 10:30:00")
                    setAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL, "+07:00")
                    setLatLong(-6.2000, 106.8166)
                    setAltitude(12.5)
                    saveAttributes()
                }
            }
            grantLocationPermission()
            val present = ExifMetadataReader(context).read(image, "image/jpeg")!!
            assertEquals("Fixture Camera", present.cameraMake)
            assertEquals("Fixture Model", present.cameraModel)
            assertEquals(ExifInterface.ORIENTATION_ROTATE_90, present.orientation)
            assertNotNull(present.latitude)
            assertNotNull(present.longitude)
            assertNotNull(present.altitude)
            assertTrue(present.capturedAtEpochMs != null && present.capturedAtEpochMs > 0)

            if (Build.VERSION.SDK_INT >= 29) {
                val restricted = ExifMetadataReader(
                    context,
                    locationAccess = { false },
                ).read(image, "image/jpeg")!!
                assertEquals("gps_restricted", restricted.state)
                assertNull(restricted.latitude)
                assertNull(restricted.longitude)
                assertNull(restricted.altitude)
                assertTrue("gps_permission_not_granted" in restricted.warningCodes)
            }

            val invalid = ExifMetadataReader(context).read(malformed, "image/jpeg")!!
            assertEquals("unavailable", invalid.state)
            assertTrue("exif_malformed" in invalid.warningCodes)
        } finally {
            resolver.delete(image, null, null)
            resolver.delete(malformed, null, null)
        }
    }

    private fun createImage(name: String): Uri {
        val uri = insertMedia(name)
        resolver.openOutputStream(uri, "w")!!.use { output ->
            val bitmap = Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888)
            try {
                check(bitmap.compress(Bitmap.CompressFormat.JPEG, 95, output))
            } finally {
                bitmap.recycle()
            }
        }
        publish(uri)
        return uri
    }

    private fun createMalformedImage(name: String): Uri {
        val uri = insertMedia(name)
        resolver.openOutputStream(uri, "w")!!.use { it.write("invalid-jpeg".toByteArray()) }
        publish(uri)
        return uri
    }

    private fun insertMedia(name: String): Uri {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, name)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            if (Build.VERSION.SDK_INT >= 29) {
                put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/SIKSIKFixture")
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
        }
        return requireNotNull(
            resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values),
        )
    }

    private fun publish(uri: Uri) {
        if (Build.VERSION.SDK_INT >= 29) {
            resolver.update(
                uri,
                ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) },
                null,
                null,
            )
        }
    }

    private fun grantLocationPermission() {
        if (Build.VERSION.SDK_INT >= 29) {
            InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(
                context.packageName,
                Manifest.permission.ACCESS_MEDIA_LOCATION,
            )
        }
    }

}
