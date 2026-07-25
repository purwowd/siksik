package com.siksik.agent.preprocessing

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.Base64
import java.util.zip.GZIPInputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.apache.poi.hssf.usermodel.HSSFWorkbook
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DocumentTextPreprocessorTest {
    private val preprocessor = BoundedDocumentTextPreprocessor(
        ocr = UnavailableOcr,
        maxInputBytes = 1024 * 1024,
        maxTextCharacters = 128,
    )

    @Test
    fun plainWhitespaceNormalizationIsNotTruncation() {
        val result = preprocessor.process(
            input("text/plain", "  alpha   beta\r\n\r\ngamma  ".toByteArray()),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertEquals(DocumentState.EXTRACTED, result.state)
        assertEquals("alpha beta\ngamma", result.normalizedText)
        assertFalse("document_text_truncated" in result.execution.warnings)
    }

    @Test
    fun csvTextIsExtractedThroughTheBoundedPlainTextPath() {
        val result = preprocessor.process(
            input("text/csv", "name,value\nalpha,42".toByteArray()),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertEquals(DocumentState.EXTRACTED, result.state)
        assertEquals("name,value\nalpha,42", result.normalizedText)
    }

    @Test
    fun cancellationAndOversizedInputAreExplicit() {
        val cancelled = preprocessor.process(
            input("text/plain", "value".toByteArray()),
            CancellationToken { true },
        )
        assertEquals(ExecutionStatus.CANCELLED, cancelled.execution.status)
        assertEquals(listOf("cancelled"), cancelled.execution.warnings)

        val oversized = preprocessor.process(
            input("text/plain", "value".toByteArray(), declaredSize = 2_000_000),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.SKIPPED, oversized.execution.status)
        assertEquals(DocumentState.OVERSIZED, oversized.state)
    }

    @Test
    fun rtfLimitPropagatesTruncation() {
        val small = BoundedDocumentTextPreprocessor(
            ocr = UnavailableOcr,
            maxInputBytes = 1024,
            maxTextCharacters = 8,
        )
        val result = small.process(
            input("application/rtf", "{\\rtf1 alpha beta gamma}".toByteArray()),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.TRUNCATED, result.execution.status)
        assertEquals(DocumentState.TRUNCATED, result.state)
        assertTrue("document_text_truncated" in result.execution.warnings)
        assertTrue(result.normalizedText.length <= 8)
    }

    @Test
    fun docxTextIsExtractedWithBoundedXmlParser() {
        val bytes = archive(
            listOf(
                "word/header1.xml" to wordXml("Header"),
                "word/document.xml" to wordXml("Main document"),
            ),
        )
        val result = preprocessor.process(
            input(DOCX_MIME, bytes),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertEquals("Main document\nHeader", result.normalizedText)
    }

    @Test
    fun xlsxResolvesSharedStringsIndependentOfZipOrder() {
        val worksheet = """
            <worksheet><sheetData><row>
              <c t="s"><v>0</v></c><c><v>42</v></c>
            </row></sheetData></worksheet>
        """.trimIndent().toByteArray()
        val sharedStrings = """
            <sst><si><t>Alpha</t></si></sst>
        """.trimIndent().toByteArray()
        val bytes = archive(
            listOf(
                "xl/worksheets/sheet1.xml" to worksheet,
                "xl/sharedStrings.xml" to sharedStrings,
            ),
        )
        val result = preprocessor.process(
            input(XLSX_MIME, bytes),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertEquals("Alpha 42", result.normalizedText)
    }

    @Test
    fun legacyXlsTextIsExtracted() {
        val bytes = ByteArrayOutputStream().use { output ->
            HSSFWorkbook().use { workbook ->
                workbook.createSheet("Sheet A").createRow(0).createCell(0).setCellValue("Alpha")
                workbook.write(output)
            }
            output.toByteArray()
        }
        val result = preprocessor.process(
            input("application/vnd.ms-excel", bytes),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertTrue(result.normalizedText.contains("Alpha"))
    }

    @Test
    fun legacyDocTextIsExtracted() {
        val compressed = Base64.getDecoder().decode(LEGACY_DOC_GZIP_BASE64)
        val bytes = GZIPInputStream(ByteArrayInputStream(compressed)).use { it.readBytes() }
        val result = preprocessor.process(
            input("application/msword", bytes),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.COMPLETED, result.execution.status)
        assertTrue(result.normalizedText.contains("Legacy DOC fixture"))
    }

    @Test
    fun corruptEncryptedAndUnsupportedDocumentsAreDistinct() {
        val corrupt = preprocessor.process(
            input(DOCX_MIME, "not-a-zip".toByteArray()),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.FAILED, corrupt.execution.status)
        assertEquals(DocumentState.CORRUPT, corrupt.state)

        val encrypted = preprocessor.process(
            input(DOCX_MIME, OLE_HEADER),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.SKIPPED, encrypted.execution.status)
        assertEquals(DocumentState.ENCRYPTED, encrypted.state)

        val unsupported = preprocessor.process(
            input("application/octet-stream", byteArrayOf(1)),
            CancellationToken.NONE,
        )
        assertEquals(ExecutionStatus.SKIPPED, unsupported.execution.status)
        assertEquals(DocumentState.UNSUPPORTED_FEATURE, unsupported.state)
    }

    private fun input(
        mime: String,
        bytes: ByteArray,
        declaredSize: Long? = bytes.size.toLong(),
    ) = PreprocessInput(
        recordId = "record-1",
        mimeType = mime,
        sizeBytes = declaredSize,
        width = null,
        height = null,
        streamProvider = { ByteArrayInputStream(bytes) },
    )

    private fun archive(entries: List<Pair<String, ByteArray>>): ByteArray =
        ByteArrayOutputStream().use { output ->
            ZipOutputStream(output).use { archive ->
                entries.forEach { (name, bytes) ->
                    archive.putNextEntry(ZipEntry(name))
                    archive.write(bytes)
                    archive.closeEntry()
                }
            }
            output.toByteArray()
        }

    private fun wordXml(value: String): ByteArray =
        "<w:document xmlns:w=\"urn:w\"><w:p><w:r><w:t>$value</w:t></w:r></w:p></w:document>"
            .toByteArray()

    private object UnavailableOcr : TextOcrPreprocessor {
        override fun capability() = EngineCapability(
            EngineAvailability.UNAVAILABLE,
            EngineIdentity("test-ocr", "1"),
            "ocr_unavailable",
        )

        override fun process(
            input: PreprocessInput,
            cancellation: CancellationToken,
        ) = TextOcrResult(
            ExecutionInfo(EngineIdentity("test-ocr", "1"), ExecutionStatus.SKIPPED, 0),
            "",
            emptyList(),
            null,
        )

        override fun close() = Unit
    }

    companion object {
        private const val DOCX_MIME =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        private const val XLSX_MIME =
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        private const val LEGACY_DOC_GZIP_BASE64 =
            "H4sIAP+fWWoCA+2cTUwTQRTH325LaSvQLQhW8KOiQRONwaiJB2MgICIaQCHxKt9t0oJBULhx" +
                "MfGI8eDFxEDCycRoJF5FD95ULhz0hEcSjZUbB7v+3+4USgvSEiMffb/m7ezO7MybfTOzO7PZ19" +
                "nP/vmJV+XfKIVL5KC46SFXUpwGOZI4MIiOqri4aZocFYSYwo7i+9Q71aCx4rd2K/vRqBo3fBW" +
                "kiLoBrUMZn0BtI7aU0N8xTd+G+wki1nZSHU3SxvA5eylz3kAKIO9VmOAn92jaOn5gSAX1leO" +
                "vkFIStjufUvqRIGx/rlMP9VEHddEont/11EJ1CHspTCM0RMM0iPRCMdPuRT3nl8QSOUJBS5OD" +
                "bkDqmgIr87wFMcwuAXPywg9VVFCt33aOu2bcMW+wsNU35n9ZMl9qBGrKV5283P5CTqKJCXKb" +
                "YvKRF90gjFAnh/U7R1Tzy9Q5xO2hmQYwC4xijshrct1aZfOjgx8jrepREm30ONPLvki1NYvmB" +
                "EK+K9VjJtmLUoZRzhDmmK3YH4T0Wds7FEJcA3T1IzWVEvWegN8zWVt113ImzWDykvb5hqYb" +
                "qzLbb7I4wxXXeEbdvh1GiaLSd1GxZoT3Ed5EBdkU/Ug/j3L0DMppw9Q6Sp3IyQY8m6H2Wpgl" +
                "rIyeaY0bUcsI3cN2CHm7kDuICX4YJg5ZRg3i+qvdDwMUm0XFQ1aZX69FqljS7waadewkh6Gl" +
                "xSZigg6KGTKKNsmUayZ/mqZpjIqr/33p3DcrV4Yl2sktJhcEQRAEQRCEXCaOFbXTm7665Jj5B" +
                "88Wl1pCxvNHbjp5/PUXXqMEVBqLoRYZC7/FjoIgCIIgCIIgCIKwU9f/+tzHuaenK4zHT7D+P" +
                "7X0Qtb/giAIgiAIgiAIgrAz0dQa3kH2x/H8FT1/KZ9P9v86eBB6IXvI9m5mX9AiiE+l+4l9C" +
                "OyP7Nn7nj3VyyD7VPp+hOxyVAE5ADkIOQQ5rNJZKpP24/KXDP8VdmgYsPwxLlueF4M0mlX/K" +
                "aM8LVEW9yGXx3aJmLGTG9bKcwwypvbPUDt1UCdFqGdT/bcIvTf5ejLJY30Gr7wWblneLd1Uj" +
                "7CLhi2Hj7U8UNajnHSNx1A2+ilJfx61WVqjljfMKF2F9t5ljxt24xiwnE3W4wT062rsZqrfWK" +
                "U/9cqzq88F6M/W/oEk/YIgCMLW8AdxrTKnAEwAAA=="
        private val OLE_HEADER = byteArrayOf(
            0xD0.toByte(),
            0xCF.toByte(),
            0x11,
            0xE0.toByte(),
            0xA1.toByte(),
            0xB1.toByte(),
            0x1A,
            0xE1.toByte(),
        )
    }
}
