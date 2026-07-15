"""Unit tests — word-boundary lexicon matching."""

from __future__ import annotations

import pytest

from app.services.lexicon import contains_phrase, match_keywords
from app.services.ocr import ocr_findings_from_text
from app.services.analysis import analyze_path_signals, analyze_text_l1_l2
from app.core.config import settings


@pytest.mark.unit
def test_no_false_positive_anti_inside_ganti():
    text = "SERIBU KALI GANTI PRESIDEN KALO KITA MALAS"
    assert not contains_phrase(text, "anti")
    # "anti pemerintah" / solo anti dari token split tidak boleh match via substring
    hits = match_keywords(text)
    assert "anti" not in [h.lower() for h in hits]
    assert not any("anti pemerintah" == h.lower() for h in hits)
    # ganti + presiden harus masuk (frasa + tag)
    assert any("ganti presid" in h.lower() or "presiden" in h.lower() for h in hits)


@pytest.mark.unit
def test_no_false_positive_bom_substring():
    text = "Semino kife kuba noscobom deko Yumano bumo eade"
    assert not contains_phrase(text, "bom")
    assert "bom" not in [h.lower() for h in match_keywords(text)]


@pytest.mark.unit
def test_true_positive_phrase_and_token():
    assert contains_phrase("ajak makar terhadap negara", "makar")
    assert contains_phrase("spanduk anti pemerintah di jalan", "anti pemerintah")
    findings = ocr_findings_from_text(
        "Spanduk anti pemerintah di jalan", backend="fake"
    )
    assert any("anti pemerintah" in f["label"] for f in findings)


@pytest.mark.unit
def test_path_ganti_presiden():
    hits = analyze_path_signals(
        "/staging/documents/kaos_ganti_presiden.jpg", settings.risk_keywords
    )
    assert hits
    assert any("presiden" in f["label"].lower() or "ganti" in f["label"].lower() for f in hits)


@pytest.mark.unit
def test_text_l1_no_ganti_fp():
    findings = analyze_text_l1_l2(
        "Kami ganti jadwal rapat saja", settings.risk_keywords
    )
    assert findings == []
