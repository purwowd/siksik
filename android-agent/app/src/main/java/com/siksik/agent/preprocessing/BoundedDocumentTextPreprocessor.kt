package com.siksik.agent.preprocessing

import android.graphics.Bitmap
import android.graphics.pdf.PdfRenderer
import com.siksik.agent.BuildConfig
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.zip.ZipException
import java.util.zip.ZipInputStream
import javax.xml.parsers.ParserConfigurationException
import javax.xml.parsers.SAXParserFactory
import org.apache.poi.EncryptedDocumentException
import org.apache.poi.hssf.extractor.ExcelExtractor
import org.apache.poi.hssf.usermodel.HSSFWorkbook
import org.apache.poi.hwpf.HWPFDocument
import org.apache.poi.hwpf.extractor.WordExtractor
import org.xml.sax.Attributes
import org.xml.sax.InputSource
import org.xml.sax.SAXException
import org.xml.sax.helpers.DefaultHandler

class BoundedDocumentTextPreprocessor(
    private val ocr: TextOcrPreprocessor,
    private val maxInputBytes: Long = BuildConfig.MAX_DOCUMENT_INPUT_BYTES,
    private val maxTextCharacters: Int = BuildConfig.MAX_DOCUMENT_TEXT_CHARS,
) : DocumentTextPreprocessor {
    private val identity = EngineIdentity("SIKSIK bounded document text", "1.0.0")

    override fun capability(): EngineCapability = try {
        HSSFWorkbook::class.java.name
        HWPFDocument::class.java.name
        EngineCapability(EngineAvailability.AVAILABLE, identity)
    } catch (_: LinkageError) {
        EngineCapability(EngineAvailability.ERROR, identity, "legacy_document_engine_unavailable")
    }

    override fun process(
        input: PreprocessInput,
        cancellation: CancellationToken,
    ): DocumentTextResult {
        val started = System.nanoTime()
        if (cancellation.isCancelled()) {
            return result(
                started,
                ExecutionStatus.CANCELLED,
                DocumentState.TRUNCATED,
                "",
                listOf("cancelled"),
            )
        }
        if (input.sizeBytes != null && input.sizeBytes > maxInputBytes) {
            return result(
                started,
                ExecutionStatus.SKIPPED,
                DocumentState.OVERSIZED,
                "",
                listOf("document_oversized"),
            )
        }
        val format = DocumentFormat.fromMime(input.mimeType) ?: return result(
            started,
            ExecutionStatus.SKIPPED,
            DocumentState.UNSUPPORTED_FEATURE,
            "",
            listOf("document_format_unsupported"),
        )
        return try {
            when (format) {
                DocumentFormat.PDF -> extractPdf(input, cancellation, started)
                DocumentFormat.TXT, DocumentFormat.CSV -> extractPlain(input, cancellation, started)
                DocumentFormat.RTF -> extractRtf(input, cancellation, started)
                DocumentFormat.DOCX -> extractOoxml(input, OoxmlKind.WORD, cancellation, started)
                DocumentFormat.XLSX -> extractOoxml(input, OoxmlKind.EXCEL, cancellation, started)
                DocumentFormat.DOC -> extractDoc(input, cancellation, started)
                DocumentFormat.XLS -> extractXls(input, cancellation, started)
            }
        } catch (_: DocumentCancelledException) {
            cancelled(started)
        } catch (_: EncryptedDocumentException) {
            result(
                started,
                ExecutionStatus.SKIPPED,
                DocumentState.ENCRYPTED,
                "",
                listOf("document_encrypted"),
            )
        } catch (_: DocumentLimitException) {
            result(
                started,
                ExecutionStatus.TRUNCATED,
                DocumentState.OVERSIZED,
                "",
                listOf("document_oversized"),
            )
        } catch (_: ZipException) {
            result(
                started,
                ExecutionStatus.FAILED,
                DocumentState.CORRUPT,
                "",
                listOf("document_corrupt"),
            )
        } catch (_: SAXException) {
            corrupt(started)
        } catch (_: ParserConfigurationException) {
            result(
                started,
                ExecutionStatus.FAILED,
                DocumentState.UNSUPPORTED_FEATURE,
                "",
                listOf("document_parser_unavailable"),
            )
        } catch (_: IOException) {
            result(
                started,
                ExecutionStatus.FAILED,
                DocumentState.CORRUPT,
                "",
                listOf("document_read_failed"),
            )
        } catch (_: SecurityException) {
            result(
                started,
                ExecutionStatus.FAILED,
                DocumentState.CORRUPT,
                "",
                listOf("document_access_denied"),
            )
        } catch (_: RuntimeException) {
            result(
                started,
                ExecutionStatus.FAILED,
                DocumentState.CORRUPT,
                "",
                listOf("document_parser_failed"),
            )
        }
    }

    private fun extractPlain(
        input: PreprocessInput,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        val bytes = readBounded(input.streamProvider(), cancellation)
        val warnings = mutableListOf<String>()
        val text = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(java.nio.ByteBuffer.wrap(stripUtf8Bom(bytes)))
                .toString()
        } catch (_: CharacterCodingException) {
            warnings.add("legacy_text_encoding")
            bytes.toString(Charsets.ISO_8859_1)
        }
        return normalizedResult(started, text, warnings)
    }

    private fun extractRtf(
        input: PreprocessInput,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        val bytes = readBounded(input.streamProvider(), cancellation)
        val extraction = RtfTextExtractor.extract(bytes, maxTextCharacters)
        return normalizedResult(
            started,
            extraction.text,
            if (extraction.truncated) listOf("document_text_truncated") else emptyList(),
        )
    }

    private fun extractDoc(
        input: PreprocessInput,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        val bytes = readBounded(input.streamProvider(), cancellation)
        if (!bytes.startsWith(OLE_HEADER)) {
            return corrupt(started)
        }
        if (cancellation.isCancelled()) return cancelled(started)
        val text = HWPFDocument(ByteArrayInputStream(bytes)).use { document ->
            WordExtractor(document).use(WordExtractor::getText)
        }
        return normalizedResult(started, text, emptyList())
    }

    private fun extractXls(
        input: PreprocessInput,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        val bytes = readBounded(input.streamProvider(), cancellation)
        if (!bytes.startsWith(OLE_HEADER)) {
            return corrupt(started)
        }
        if (cancellation.isCancelled()) return cancelled(started)
        val text = HSSFWorkbook(ByteArrayInputStream(bytes)).use { workbook ->
            ExcelExtractor(workbook).use { extractor ->
                extractor.setIncludeSheetNames(true)
                extractor.setFormulasNotResults(false)
                extractor.text
            }
        }
        return normalizedResult(started, text, emptyList())
    }

    private fun extractOoxml(
        input: PreprocessInput,
        kind: OoxmlKind,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        input.streamProvider().use { inputStream ->
            val raw = BoundedInputStream(inputStream, maxInputBytes, cancellation)
            val prefix = ByteArray(8)
            val prefixRead = readPrefix(raw, prefix, cancellation)
            if (prefixRead >= OLE_HEADER.size && prefix.startsWith(OLE_HEADER)) {
                return result(
                    started,
                    ExecutionStatus.SKIPPED,
                    DocumentState.ENCRYPTED,
                    "",
                    listOf("document_encrypted"),
                )
            }
            val combined = PrefixInputStream(prefix, prefixRead.coerceAtLeast(0), raw)
            val collector = BoundedTextCollector(maxTextCharacters)
            var entryCount = 0
            var expandedBytes = 0L
            val recognizedEntries = mutableListOf<OoxmlEntry>()
            ZipInputStream(combined).use { archive ->
                while (true) {
                    if (cancellation.isCancelled()) return cancelled(started)
                    val entry = archive.nextEntry ?: break
                    entryCount += 1
                    if (entryCount > BuildConfig.MAX_DOCUMENT_ARCHIVE_ENTRIES) {
                        throw DocumentLimitException()
                    }
                    if (entry.isDirectory || !kind.accepts(entry.name)) {
                        archive.closeEntry()
                        continue
                    }
                    val entryBytes = readArchiveEntry(archive, cancellation) { count ->
                        expandedBytes += count
                        if (expandedBytes > BuildConfig.MAX_DOCUMENT_ARCHIVE_BYTES) {
                            throw DocumentLimitException()
                        }
                    }
                    recognizedEntries.add(OoxmlEntry(entry.name, entryBytes))
                    archive.closeEntry()
                }
            }
            if (recognizedEntries.isEmpty()) return corrupt(started)
            when (kind) {
                OoxmlKind.WORD -> recognizedEntries
                    .sortedBy(OoxmlEntry::sortKey)
                    .forEach { entry ->
                        if (cancellation.isCancelled()) return cancelled(started)
                        parseWordXmlText(entry.bytes, collector)
                    }
                OoxmlKind.EXCEL -> parseExcelEntries(
                    recognizedEntries,
                    collector,
                    cancellation,
                )
            }
            return normalizedResult(
                started,
                collector.value(),
                if (collector.truncated) listOf("document_text_truncated") else emptyList(),
            )
        }
    }

    private fun extractPdf(
        input: PreprocessInput,
        cancellation: CancellationToken,
        started: Long,
    ): DocumentTextResult {
        val descriptorProvider = input.fileDescriptorProvider ?: return result(
            started,
            ExecutionStatus.SKIPPED,
            DocumentState.UNSUPPORTED_FEATURE,
            "",
            listOf("pdf_descriptor_unavailable"),
        )
        val ocrCapability = ocr.capability()
        if (ocrCapability.availability != EngineAvailability.AVAILABLE) {
            return result(
                started,
                ExecutionStatus.SKIPPED,
                DocumentState.UNSUPPORTED_FEATURE,
                "",
                listOf(ocrCapability.reason ?: "pdf_ocr_unavailable"),
            )
        }
        descriptorProvider().use { descriptor ->
            PdfRenderer(descriptor).use { renderer ->
                val pages = minOf(renderer.pageCount, BuildConfig.MAX_PDF_PAGES)
                val collector = BoundedTextCollector(maxTextCharacters)
                var successfulPages = 0
                var failedPages = 0
                for (index in 0 until pages) {
                    if (cancellation.isCancelled()) return cancelled(started)
                    renderer.openPage(index).use { page ->
                        val scale = pageScale(page.width, page.height)
                        val width = (page.width * scale).toInt().coerceAtLeast(1)
                        val height = (page.height * scale).toInt().coerceAtLeast(1)
                        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
                        try {
                            page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                            val ocrResult = ocr.process(
                                PreprocessInput(
                                    "${input.recordId}_page_$index",
                                    "image/png",
                                    null,
                                    width,
                                    height,
                                    { ByteArrayInputStream(ByteArray(0)) },
                                    BitmapProvider { bitmap },
                                ),
                                cancellation,
                            )
                            when (ocrResult.execution.status) {
                                ExecutionStatus.COMPLETED,
                                ExecutionStatus.TRUNCATED,
                                -> {
                                    successfulPages += 1
                                    collector.append(ocrResult.normalizedText)
                                }
                                ExecutionStatus.CANCELLED -> return cancelled(started)
                                else -> failedPages += 1
                            }
                        } finally {
                            if (!bitmap.isRecycled) bitmap.recycle()
                        }
                    }
                    if (collector.truncated) break
                }
                val warnings = buildList {
                    if (renderer.pageCount > pages) add("pdf_pages_truncated")
                    if (collector.truncated) add("document_text_truncated")
                    if (failedPages > 0 && successfulPages > 0) add("pdf_ocr_partial")
                }
                if (failedPages > 0 && successfulPages == 0) {
                    return result(
                        started,
                        ExecutionStatus.FAILED,
                        DocumentState.TRUNCATED,
                        "",
                        listOf("pdf_ocr_failed"),
                    )
                }
                return normalizedResult(started, collector.value(), warnings)
            }
        }
    }

    private fun readBounded(
        input: InputStream,
        cancellation: CancellationToken,
    ): ByteArray = input.use { stream ->
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(64 * 1024)
        var total = 0L
        while (true) {
            if (cancellation.isCancelled()) throw DocumentCancelledException()
            val read = stream.read(buffer)
            if (read < 0) break
            if (read == 0) continue
            total += read
            if (total > maxInputBytes) throw DocumentLimitException()
            output.write(buffer, 0, read)
        }
        output.toByteArray()
    }

    private fun readPrefix(
        input: InputStream,
        prefix: ByteArray,
        cancellation: CancellationToken,
    ): Int {
        var total = 0
        while (total < prefix.size) {
            if (cancellation.isCancelled()) throw DocumentCancelledException()
            val read = input.read(prefix, total, prefix.size - total)
            if (read < 0) break
            if (read == 0) continue
            total += read
        }
        return total
    }

    private fun readArchiveEntry(
        input: InputStream,
        cancellation: CancellationToken,
        onBytes: (Long) -> Unit,
    ): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        while (true) {
            if (cancellation.isCancelled()) throw DocumentCancelledException()
            val read = input.read(buffer)
            if (read < 0) break
            if (read == 0) continue
            onBytes(read.toLong())
            output.write(buffer, 0, read)
        }
        return output.toByteArray()
    }

    private fun parseWordXmlText(bytes: ByteArray, collector: BoundedTextCollector) {
        parseXml(bytes, WordTextHandler(collector))
    }

    private fun parseExcelEntries(
        entries: List<OoxmlEntry>,
        collector: BoundedTextCollector,
        cancellation: CancellationToken,
    ) {
        val sharedStrings = entries
            .firstOrNull { it.name.equals("xl/sharedStrings.xml", ignoreCase = true) }
            ?.let { entry -> parseSharedStrings(entry.bytes) }
            .orEmpty()
        entries
            .filter { it.name.lowercase().matches(Regex("xl/worksheets/sheet[0-9]+\\.xml")) }
            .sortedBy(OoxmlEntry::sortKey)
            .forEach { entry ->
                if (cancellation.isCancelled()) throw DocumentCancelledException()
                parseXml(entry.bytes, XlsxWorksheetHandler(sharedStrings, collector))
            }
    }

    private fun parseSharedStrings(bytes: ByteArray): List<String> {
        val handler = SharedStringsHandler(
            maxEntries = MAX_SHARED_STRINGS,
            maxCharacters = MAX_SHARED_STRING_CHARACTERS,
        )
        parseXml(bytes, handler)
        return handler.values
    }

    private fun parseXml(bytes: ByteArray, handler: DefaultHandler) {
        val factory = SAXParserFactory.newInstance().apply {
            isNamespaceAware = true
            isValidating = false
            setFeature("http://xml.org/sax/features/external-general-entities", false)
            setFeature("http://xml.org/sax/features/external-parameter-entities", false)
            setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
        }
        factory.newSAXParser().xmlReader.apply {
            entityResolver = org.xml.sax.EntityResolver { _, _ ->
                InputSource(ByteArrayInputStream(ByteArray(0)))
            }
            contentHandler = handler
            errorHandler = handler
        }.parse(InputSource(ByteArrayInputStream(bytes)))
    }

    private fun normalizedResult(
        started: Long,
        value: String,
        initialWarnings: List<String>,
    ): DocumentTextResult {
        val normalized = normalizeSearchTextBounded(value, maxTextCharacters)
        val warnings = initialWarnings.toMutableList()
        if (normalized.truncated && "document_text_truncated" !in warnings) {
            warnings.add("document_text_truncated")
        }
        val truncated = warnings.any {
            it.endsWith("_truncated") || it.endsWith("_partial")
        }
        val state = when {
            truncated -> DocumentState.TRUNCATED
            normalized.value.isEmpty() -> DocumentState.BLANK
            else -> DocumentState.EXTRACTED
        }
        val status = when {
            truncated -> ExecutionStatus.TRUNCATED
            else -> ExecutionStatus.COMPLETED
        }
        return result(started, status, state, normalized.value, warnings)
    }

    private fun corrupt(started: Long) = result(
        started,
        ExecutionStatus.FAILED,
        DocumentState.CORRUPT,
        "",
        listOf("document_corrupt"),
    )

    private fun cancelled(started: Long) = result(
        started,
        ExecutionStatus.CANCELLED,
        DocumentState.TRUNCATED,
        "",
        listOf("cancelled"),
    )

    private fun result(
        started: Long,
        status: ExecutionStatus,
        state: DocumentState,
        text: String,
        warnings: List<String>,
    ) = DocumentTextResult(
        ExecutionInfo(identity, status, elapsedMs(started), warnings),
        state,
        text,
        text.length,
    )

    private fun pageScale(width: Int, height: Int): Double {
        val pixels = width.toLong() * height.toLong()
        if (pixels <= PDF_PAGE_PIXELS) return 1.0
        return kotlin.math.sqrt(PDF_PAGE_PIXELS.toDouble() / pixels.toDouble())
    }

    companion object {
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
        private const val PDF_PAGE_PIXELS = 2_000_000L
        private const val MAX_SHARED_STRINGS = 100_000
        private const val MAX_SHARED_STRING_CHARACTERS = 4_000_000
    }
}

private enum class DocumentFormat {
    PDF,
    DOC,
    DOCX,
    XLS,
    XLSX,
    CSV,
    TXT,
    RTF;

    companion object {
        fun fromMime(value: String): DocumentFormat? = when (value.lowercase().substringBefore(';')) {
            "application/pdf" -> PDF
            "application/msword" -> DOC
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document" -> DOCX
            "application/vnd.ms-excel" -> XLS
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" -> XLSX
            "text/csv", "application/csv" -> CSV
            "text/plain" -> TXT
            "application/rtf", "text/rtf" -> RTF
            else -> null
        }
    }
}

private enum class OoxmlKind {
    WORD,
    EXCEL;

    fun accepts(path: String): Boolean {
        val normalized = path.lowercase()
        return when (this) {
            WORD -> normalized == "word/document.xml" ||
                normalized.matches(Regex("word/(header|footer)[0-9]+\\.xml")) ||
                normalized in setOf(
                    "word/footnotes.xml",
                    "word/endnotes.xml",
                    "word/comments.xml",
                )
            EXCEL -> normalized == "xl/sharedstrings.xml" ||
                normalized.matches(Regex("xl/worksheets/sheet[0-9]+\\.xml"))
        }
    }
}

private data class OoxmlEntry(
    val name: String,
    val bytes: ByteArray,
) {
    fun sortKey(): String {
        val normalized = name.lowercase()
        if (normalized == "word/document.xml") return "0_document"
        val sheet = Regex("xl/worksheets/sheet([0-9]+)\\.xml").matchEntire(normalized)
        if (sheet != null) return "0_sheet_${sheet.groupValues[1].padStart(12, '0')}"
        return "1_$normalized"
    }
}

private class WordTextHandler(
    private val collector: BoundedTextCollector,
) : DefaultHandler() {
    private var capture = false
    private var current = StringBuilder()

    override fun startElement(uri: String?, localName: String?, qName: String?, attributes: Attributes?) {
        val name = localName?.takeIf(String::isNotEmpty) ?: qName.orEmpty().substringAfter(':')
        capture = name == "t"
        if (capture) current = StringBuilder()
        if (name in setOf("p", "row", "br", "tab")) collector.separator()
    }

    override fun characters(ch: CharArray, start: Int, length: Int) {
        if (capture && !collector.truncated) current.append(ch, start, length)
    }

    override fun endElement(uri: String?, localName: String?, qName: String?) {
        val name = localName?.takeIf(String::isNotEmpty) ?: qName.orEmpty().substringAfter(':')
        if (capture && name == "t") {
            collector.append(current.toString())
            capture = false
        }
        if (name in setOf("p", "row")) collector.separator()
    }
}

private class SharedStringsHandler(
    private val maxEntries: Int,
    private val maxCharacters: Int,
) : DefaultHandler() {
    val values = mutableListOf<String>()
    private var inSharedString = false
    private var captureText = false
    private var current = StringBuilder()
    private var characters = 0

    override fun startElement(
        uri: String?,
        localName: String?,
        qName: String?,
        attributes: Attributes?,
    ) {
        when (elementName(localName, qName)) {
            "si" -> {
                inSharedString = true
                current = StringBuilder()
            }
            "t" -> captureText = inSharedString
        }
    }

    override fun characters(ch: CharArray, start: Int, length: Int) {
        if (captureText) current.append(ch, start, length)
    }

    override fun endElement(uri: String?, localName: String?, qName: String?) {
        when (elementName(localName, qName)) {
            "t" -> captureText = false
            "si" -> {
                if (values.size >= maxEntries || characters + current.length > maxCharacters) {
                    throw DocumentLimitException()
                }
                val value = current.toString()
                values.add(value)
                characters += value.length
                inSharedString = false
            }
        }
    }
}

private class XlsxWorksheetHandler(
    private val sharedStrings: List<String>,
    private val collector: BoundedTextCollector,
) : DefaultHandler() {
    private var cellType = ""
    private var inCell = false
    private var captureValue = false
    private var current = StringBuilder()

    override fun startElement(
        uri: String?,
        localName: String?,
        qName: String?,
        attributes: Attributes?,
    ) {
        when (elementName(localName, qName)) {
            "row" -> collector.separator()
            "c" -> {
                inCell = true
                cellType = attributes?.getValue("t").orEmpty()
                current = StringBuilder()
            }
            "v" -> captureValue = inCell
            "t" -> captureValue = inCell && cellType == "inlineStr"
        }
    }

    override fun characters(ch: CharArray, start: Int, length: Int) {
        if (captureValue) current.append(ch, start, length)
    }

    override fun endElement(uri: String?, localName: String?, qName: String?) {
        when (elementName(localName, qName)) {
            "v", "t" -> captureValue = false
            "c" -> {
                val raw = current.toString()
                val value = if (cellType == "s") {
                    val index = raw.trim().toIntOrNull()
                        ?: throw SAXException("invalid_shared_string_index")
                    sharedStrings.getOrNull(index)
                        ?: throw SAXException("shared_string_index_out_of_range")
                } else {
                    raw
                }
                collector.append(value)
                inCell = false
                cellType = ""
            }
            "row" -> collector.separator()
        }
    }
}

private fun elementName(localName: String?, qName: String?): String =
    localName?.takeIf(String::isNotEmpty) ?: qName.orEmpty().substringAfter(':')

private class BoundedTextCollector(private val limit: Int) {
    private val output = StringBuilder()
    var truncated = false
        private set

    fun append(value: String) {
        if (truncated || value.isEmpty()) return
        if (output.isNotEmpty() && !output.last().isWhitespace()) {
            if (output.length >= limit) {
                truncated = true
                return
            }
            output.append(' ')
        }
        val remaining = limit - output.length
        if (remaining <= 0) {
            truncated = true
            return
        }
        if (value.length > remaining) {
            output.append(value, 0, remaining)
            truncated = true
        } else {
            output.append(value)
        }
    }

    fun separator() {
        if (truncated || output.isEmpty() || output.last() == '\n') return
        if (output.length >= limit) {
            truncated = true
        } else {
            output.append('\n')
        }
    }

    fun value(): String = output.toString()
}

private data class RtfExtraction(
    val text: String,
    val truncated: Boolean,
)

private object RtfTextExtractor {
    private val skippedDestinations = setOf(
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "object",
        "header",
        "footer",
    )

    fun extract(bytes: ByteArray, limit: Int): RtfExtraction {
        require(limit > 0)
        val input = bytes.toString(Charsets.ISO_8859_1)
        val output = StringBuilder()
        val skipStack = ArrayDeque<Boolean>()
        var skip = false
        var index = 0
        while (index < input.length && output.length < limit) {
            when (val char = input[index]) {
                '{' -> {
                    skipStack.addLast(skip)
                    index += 1
                }
                '}' -> {
                    skip = if (skipStack.isEmpty()) false else skipStack.removeLast()
                    index += 1
                }
                '\\' -> {
                    index += 1
                    if (index >= input.length) break
                    when (input[index]) {
                        '\\', '{', '}' -> {
                            if (!skip) output.append(input[index])
                            index += 1
                        }
                        '\'' -> {
                            val end = (index + 3).coerceAtMost(input.length)
                            if (end - index == 3) {
                                val value = input.substring(index + 1, end).toIntOrNull(16)
                                if (!skip && value != null) output.append(value.toChar())
                            }
                            index = end
                        }
                        else -> {
                            val wordStart = index
                            while (index < input.length && input[index].isLetter()) index += 1
                            val word = input.substring(wordStart, index)
                            val negative = index < input.length && input[index] == '-'
                            if (negative) index += 1
                            val numberStart = index
                            while (index < input.length && input[index].isDigit()) index += 1
                            val number = input.substring(numberStart, index).toIntOrNull()
                            if (index < input.length && input[index] == ' ') index += 1
                            if (word in skippedDestinations) skip = true
                            if (!skip) {
                                when (word) {
                                    "par", "line" -> output.append('\n')
                                    "tab" -> output.append(' ')
                                    "u" -> if (number != null) {
                                        val code = if (negative) -number else number
                                        output.append((code and 0xffff).toChar())
                                    }
                                }
                            }
                        }
                    }
                }
                '\r', '\n' -> index += 1
                else -> {
                    if (!skip && !char.isISOControl()) output.append(char)
                    index += 1
                }
            }
        }
        return RtfExtraction(output.toString(), index < input.length)
    }
}

private class BoundedInputStream(
    private val delegate: InputStream,
    private val maxBytes: Long,
    private val cancellation: CancellationToken,
) : InputStream() {
    private var total = 0L

    override fun read(): Int {
        if (cancellation.isCancelled()) throw DocumentCancelledException()
        val value = delegate.read()
        if (value >= 0) account(1)
        return value
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (cancellation.isCancelled()) throw DocumentCancelledException()
        val read = delegate.read(buffer, offset, length)
        if (read > 0) account(read)
        return read
    }

    private fun account(count: Int) {
        total += count
        if (total > maxBytes) throw DocumentLimitException()
    }
}

private class PrefixInputStream(
    private val prefix: ByteArray,
    private val prefixLength: Int,
    private val delegate: InputStream,
) : InputStream() {
    private var index = 0

    override fun read(): Int = if (index < prefixLength) {
        prefix[index++].toInt() and 0xff
    } else {
        delegate.read()
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (index >= prefixLength) return delegate.read(buffer, offset, length)
        val count = minOf(length, prefixLength - index)
        prefix.copyInto(buffer, offset, index, index + count)
        index += count
        return count
    }
}

private class DocumentLimitException : IOException()
private class DocumentCancelledException : IOException()

private fun ByteArray.startsWith(prefix: ByteArray): Boolean =
    size >= prefix.size && prefix.indices.all { this[it] == prefix[it] }

private fun stripUtf8Bom(value: ByteArray): ByteArray = if (
    value.size >= 3 &&
    value[0] == 0xEF.toByte() &&
    value[1] == 0xBB.toByte() &&
    value[2] == 0xBF.toByte()
) {
    value.copyOfRange(3, value.size)
} else {
    value
}
