package com.siksik.agent.source.inventory

import android.Manifest
import android.content.ContentResolver
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.exifinterface.media.ExifInterface
import java.io.IOException
import java.time.DateTimeException
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

class ExifMetadataReader(
    private val context: Context,
    private val resolver: ContentResolver = context.contentResolver,
    private val locationAccess: () -> Boolean = {
        Build.VERSION.SDK_INT < 29 ||
            context.checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
    },
) {
    fun read(uri: Uri, mimeType: String): ExifMetadata? {
        if (!mimeType.startsWith("image/") || !ExifInterface.isSupportedMimeType(mimeType)) {
            return null
        }
        val locationGranted = locationAccess()
        return try {
            val sourceUri = if (Build.VERSION.SDK_INT >= 29 && locationGranted) {
                MediaStore.setRequireOriginal(uri)
            } else {
                uri
            }
            resolver.openFileDescriptor(sourceUri, "r")?.use { descriptor ->
                extract(ExifInterface(descriptor.fileDescriptor), locationGranted)
            } ?: unavailable("exif_stream_unavailable")
        } catch (_: SecurityException) {
            unavailable("exif_permission_revoked", "restricted")
        } catch (_: IOException) {
            unavailable("exif_malformed")
        } catch (_: IllegalArgumentException) {
            unavailable("exif_malformed")
        } catch (_: IllegalStateException) {
            unavailable("exif_malformed")
        }
    }

    private fun extract(exif: ExifInterface, locationGranted: Boolean): ExifMetadata {
        val coordinates = exif.latLong.takeIf { locationGranted }
        val hasCoordinates = coordinates != null
        val rawCaptureTime = exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL)
        val capturedAt = parseCaptureTime(
            rawCaptureTime,
            exif.getAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL),
        )
        val warnings = buildList {
            if (!locationGranted) add("gps_permission_not_granted")
            if (!rawCaptureTime.isNullOrBlank() && capturedAt == null) {
                add("exif_datetime_malformed")
            }
        }
        return ExifMetadata(
            state = when {
                hasCoordinates -> "present"
                locationGranted -> "available"
                else -> "gps_restricted"
            },
            orientation = exif.getAttributeInt(
                ExifInterface.TAG_ORIENTATION,
                ExifInterface.ORIENTATION_UNDEFINED,
            ).takeIf { it != ExifInterface.ORIENTATION_UNDEFINED },
            cameraMake = bounded(exif.getAttribute(ExifInterface.TAG_MAKE)),
            cameraModel = bounded(exif.getAttribute(ExifInterface.TAG_MODEL)),
            lensModel = bounded(exif.getAttribute(ExifInterface.TAG_LENS_MODEL)),
            exposureTime = bounded(exif.getAttribute(ExifInterface.TAG_EXPOSURE_TIME)),
            aperture = exif.getAttributeDouble(ExifInterface.TAG_F_NUMBER, Double.NaN)
                .takeUnless(Double::isNaN),
            focalLength = exif.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, Double.NaN)
                .takeUnless(Double::isNaN),
            iso = exif.getAttributeInt(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY, -1)
                .takeIf { it >= 0 },
            latitude = coordinates?.get(0),
            longitude = coordinates?.get(1),
            altitude = exif.getAltitude(Double.NaN)
                .takeUnless(Double::isNaN)
                .takeIf { locationGranted },
            capturedAtEpochMs = capturedAt,
            warningCodes = warnings,
        )
    }

    private fun parseCaptureTime(raw: String?, rawOffset: String?): Long? {
        if (raw.isNullOrBlank()) return null
        return try {
            val local = LocalDateTime.parse(raw.trim(), EXIF_DATE_TIME_FORMAT)
            val offset = rawOffset?.trim()?.takeIf(String::isNotBlank)?.let(ZoneOffset::of)
            if (offset != null) {
                local.toInstant(offset).toEpochMilli()
            } else {
                local.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
            }
        } catch (_: DateTimeException) {
            null
        }
    }

    private fun unavailable(warning: String, state: String = "unavailable") = ExifMetadata(
        state = state,
        orientation = null,
        cameraMake = null,
        cameraModel = null,
        lensModel = null,
        exposureTime = null,
        aperture = null,
        focalLength = null,
        iso = null,
        latitude = null,
        longitude = null,
        altitude = null,
        capturedAtEpochMs = null,
        warningCodes = listOf(warning),
    )

    private fun bounded(value: String?): String? = value
        ?.replace(Regex("[\\p{Cntrl}]"), " ")
        ?.trim()
        ?.take(256)
        ?.takeIf(String::isNotBlank)

    companion object {
        private val EXIF_DATE_TIME_FORMAT = DateTimeFormatter.ofPattern(
            "yyyy:MM:dd HH:mm:ss",
            Locale.ROOT,
        )
    }
}
