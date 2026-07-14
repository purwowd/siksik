"""Unit tests — mesin analisis L1/L2/L3."""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services.analysis import analyze_image_meta_l3, analyze_text_l1_l2


@pytest.mark.unit
def test_l1_detects_keyword():
    text = "Pesan rahasia terkait anti pemerintah di grup."
    findings = analyze_text_l1_l2(text, settings.risk_keywords)
    assert findings
    assert any("anti pemerintah" in f["label"] for f in findings)
    assert all(0 < f["confidence"] <= 0.99 for f in findings)


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
