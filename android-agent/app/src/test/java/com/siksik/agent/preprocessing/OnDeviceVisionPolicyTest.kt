package com.siksik.agent.preprocessing

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnDeviceVisionPolicyTest {
    @Test
    fun cameraRollSkipsOnDeviceOcr() {
        assertFalse(
            OnDeviceVisionPolicy.shouldRunOcr(
                "media_image",
                "IMG_20260817_101112.jpg",
                "DCIM/Camera",
            ),
        )
        assertFalse(
            OnDeviceVisionPolicy.shouldRunOcr(
                "media_video",
                "VID_001.mp4",
                "Pictures",
            ),
        )
    }

    @Test
    fun screenshotChatAndDownloadKeepOnDeviceOcr() {
        assertTrue(
            OnDeviceVisionPolicy.shouldRunOcr(
                "media_image",
                "Screenshot_20260817.png",
                "Pictures/Screenshots",
            ),
        )
        assertTrue(
            OnDeviceVisionPolicy.shouldRunOcr(
                "media_image",
                "IMG-20260817-WA0001.jpg",
                "WhatsApp/Media/WhatsApp Images",
            ),
        )
        assertTrue(
            OnDeviceVisionPolicy.shouldRunOcr(
                "media_image",
                "poster.jpg",
                "Download",
            ),
        )
    }

    @Test
    fun documentsAndSmsDoNotUseImageOcrGate() {
        assertFalse(
            OnDeviceVisionPolicy.shouldRunOcr(
                "document",
                "surat.pdf",
                "Documents",
            ),
        )
        assertFalse(
            OnDeviceVisionPolicy.shouldRunOcr(
                "sms",
                "SMS",
                null,
            ),
        )
    }
}
