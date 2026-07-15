"""Unit tests — mesin analisis L1/L2/L3."""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services.analysis import analyze_image_meta_l3, analyze_text_l1_l2


@pytest.mark.unit
def test_jpeg_binary_noise_does_not_trigger_l1_bom(tmp_path, monkeypatch):
    """Byte JPEG sering membentuk token 'bom' bila di-decode UTF-8 — jangan scan binary."""
    from pathlib import Path

    from app.services.analysis import analyze_content, read_preview
    import asyncio

    # Minimal JPEG-like bytes containing standalone "bom" as decoded junk would
    junk = b"\xff\xd8\xff\xe0" + b"xxxx bom yyyy" + b"\x00\x01\x02" * 200 + b"\xff\xd9"
    img = tmp_path / "id-11134207-7r991-llk54ugij23069.jpeg"
    img.write_bytes(junk)

    text = asyncio.run(read_preview(img, "image/jpeg"))
    assert text == ""

    # vision path may add findings; stub it so we only test L1 binary scan removal
    monkeypatch.setattr(
        "app.services.vision.analyze_image_file",
        lambda _p: [],
    )
    findings = analyze_content(img, "image/jpeg", "documents", text, settings.risk_keywords)
    assert not any("Indikasi: bom" in f["label"] for f in findings)
    assert not any(f["label"].startswith("Indikasi:") for f in findings)


@pytest.mark.unit
def test_real_text_file_still_detects_bom(tmp_path):
    from app.services.analysis import analyze_content

    note = tmp_path / "note.txt"
    note.write_text("rencana bom di malam ini", encoding="utf-8")
    findings = analyze_content(note, "text/plain", "documents", note.read_text(), settings.risk_keywords)
    assert any("bom" in f["label"].lower() for f in findings)


@pytest.mark.unit
def test_l1_clean_text_no_finding():
    text = "Pesan biasa: koordinasi jadwal keluarga dan makan siang."
    findings = analyze_text_l1_l2(text, settings.risk_keywords)
    assert findings == []


@pytest.mark.unit
def test_l2_boost_with_context_cues():
    text = "Rencana segera di grup rahasia untuk makar."
    findings = analyze_text_l1_l2(text, settings.risk_keywords)
    assert findings
    # cue words should push at least one finding to L2
    assert any(f["layer_origin"] == "L2" for f in findings)


@pytest.mark.unit
def test_behavior_category():
    text = "Diskusi narkoba dan judi online."
    findings = analyze_text_l1_l2(text, settings.risk_keywords)
    cats = {f["category"] for f in findings}
    assert "perilaku_menyimpang" in cats


@pytest.mark.unit
def test_l3_risk_image_meta():
    raw = json.dumps({"name": "IMG.jpg", "tags": ["provokasi"], "risk": True})
    findings = analyze_image_meta_l3(raw)
    assert len(findings) == 1
    assert findings[0]["layer_origin"] == "L3"
    assert findings[0]["category"] == "konten_visual"


@pytest.mark.unit
def test_l3_safe_image_meta():
    raw = json.dumps({"name": "IMG.jpg", "tags": ["liburan"], "risk": False})
    assert analyze_image_meta_l3(raw) == []


@pytest.mark.unit
def test_l3_invalid_json():
    assert analyze_image_meta_l3("bukan-json") == []
