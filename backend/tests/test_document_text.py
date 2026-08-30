from __future__ import annotations

import zipfile

import pytest

from app.services import document_text
from app.services.analysis import analyze_content, read_preview


@pytest.mark.unit
def test_json_extraction_uses_values_not_metadata_keys():
    text = document_text.extract_json_text(
        '{"political_campaign": false, "payload": {"message": "Jadwal makan keluarga"}}',
        max_chars=1000,
    )

    assert "political_campaign" not in text
    assert "Jadwal makan keluarga" in text


@pytest.mark.unit
def test_html_extraction_drops_script_and_keeps_visible_text():
    text = document_text.extract_html_text(
        "<html><script>makar()</script><body>Laporan kegiatan keluarga</body></html>",
        max_chars=1000,
    )

    assert "makar" not in text
    assert "Laporan kegiatan keluarga" in text


@pytest.mark.unit
def test_pdf_native_text_is_extracted(tmp_path):
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    path = tmp_path / "laporan.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Rencana makar malam ini) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)

    text = document_text.extract_document_text(path, "application/pdf")

    assert "Rencana makar malam ini" in text


@pytest.mark.unit
async def test_docx_text_enters_official_analysis(tmp_path):
    path = tmp_path / "laporan.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Rencana makar malam ini</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)

    text = await read_preview(
        path,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    findings = analyze_content(
        path,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "documents",
        text,
        ["makar"],
    )

    assert "Rencana makar malam ini" in text
    assert any(
        "makar" in finding["evidence"].lower()
        and finding["category"] in {"anti_pemerintah", "incitement"}
        for finding in findings
    )
