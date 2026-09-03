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
        lambda _p, **_kwargs: [],
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


@pytest.mark.unit
def test_ocr_enabled_runs_easyocr_on_camera_roll(tmp_path, monkeypatch):
    from app.models.schemas import AcquisitionMode
    from app.services.analysis import _skip_heavy_ocr_for_gallery
    from app.services.hash_cache import reset_analysis_mode, set_analysis_mode

    monkeypatch.setattr(settings, "gpu_stack_enabled", False)
    monkeypatch.setattr(settings, "media_text_enabled", False)
    monkeypatch.setattr(settings, "ocr_enabled", True)
    hashed = tmp_path / "deadbeef.jpg"
    hashed.write_bytes(b"x")
    token = set_analysis_mode(AcquisitionMode.QUICK)
    try:
        assert not _skip_heavy_ocr_for_gallery(
            hashed,
            "media_image",
            "DCIM/Camera IMG_20260817.jpg",
        )
        assert not _skip_heavy_ocr_for_gallery(
            hashed,
            "media_image",
            "Pictures/Screenshots Screenshot_20260817.png",
        )
    finally:
        reset_analysis_mode(token)


@pytest.mark.unit
def test_ocr_disabled_still_skips_plain_camera_roll(tmp_path, monkeypatch):
    from app.models.schemas import AcquisitionMode
    from app.services.analysis import _skip_heavy_ocr_for_gallery
    from app.services.hash_cache import reset_analysis_mode, set_analysis_mode

    monkeypatch.setattr(settings, "gpu_stack_enabled", False)
    monkeypatch.setattr(settings, "media_text_enabled", True)
    monkeypatch.setattr(settings, "ocr_enabled", False)
    token = set_analysis_mode(AcquisitionMode.QUICK)
    try:
        hashed = tmp_path / "deadbeef.jpg"
        hashed.write_bytes(b"x")
        assert _skip_heavy_ocr_for_gallery(
            hashed,
            "media_image",
            "DCIM/Camera IMG_20260817.jpg",
        )
        assert not _skip_heavy_ocr_for_gallery(
            hashed,
            "media_image",
            "Pictures/Screenshots Screenshot_20260817.png",
        )
    finally:
        reset_analysis_mode(token)

@pytest.mark.unit
def test_crawl_record_merges_precomputed_social_ocr_into_findings(tmp_path):
    from app.services.analysis import CANONICAL_CRAWL_RECORD_MIME, analyze_content_result

    path = tmp_path / "record_ui_comments_test.siksik-record.json"
    path.write_text("{}", encoding="utf-8")
    outcome = analyze_content_result(
        path,
        CANONICAL_CRAWL_RECORD_MIME,
        "visible_ui",
        "Select\nComments",
        ["makar", "pistol"],
        precomputed_ocr_text="jisonghemdal\nPistol\n2h",
        precomputed_ocr_backend="host_ocr",
    )
    labels = " ".join(item["label"].lower() for item in outcome.findings)
    evidence = " ".join(str(item.get("evidence") or "").lower() for item in outcome.findings)
    assert "pistol" in labels or "pistol" in evidence


@pytest.mark.unit
def test_crawl_record_content_flags_enter_findings_once(tmp_path, monkeypatch):
    from app.core import config
    from app.services.analysis import CANONICAL_CRAWL_RECORD_MIME, analyze_content_result

    monkeypatch.setattr(config.settings, "gpu_stack_enabled", False)
    monkeypatch.setattr(config.settings, "content_text_model", "")
    path = tmp_path / "record_social_content.siksik-record.json"
    path.write_text("{}", encoding="utf-8")

    outcome = analyze_content_result(
        path,
        CANONICAL_CRAWL_RECORD_MIME,
        "visible_ui",
        "Acara LGBT Pride Month",
        [],
        precomputed_ocr_text="Bendera LGBT pada Pride Month",
        precomputed_ocr_backend="host_ocr",
    )

    flagged = [item for item in outcome.findings if item["category"] == "lgbt_content"]
    assert len(flagged) == 1
    assert flagged[0]["label"] == "LGBT text/flag"


@pytest.mark.unit
def test_image_cross_detector_hits_are_one_finding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services.analysis import analyze_content_result

    image = tmp_path / "flag.png"
    image.write_bytes(b"placeholder")
    monkeypatch.setattr(
        "app.services.nudity.analyze_image_result",
        lambda _path: SimpleNamespace(findings=(), cacheable=True),
    )
    monkeypatch.setattr(
        "app.services.vision.analyze_image_file",
        lambda _path, **_kwargs: [
            {
                "category": "lgbt_content",
                "label": "LGBT text/flag",
                "confidence": 0.84,
                "layer_origin": "L3",
                "evidence": "[visual] pride flag",
            },
            {
                "category": "lgbt_flag",
                "label": "Qwen: pride flag",
                "confidence": 0.91,
                "layer_origin": "L3",
                "evidence": "[qwen] pride flag",
            },
        ],
    )

    outcome = analyze_content_result(
        image,
        "image/png",
        "media_image",
        "",
        [],
    )

    flagged = [item for item in outcome.findings if item["category"] == "lgbt_content"]
    assert len(flagged) == 1
    assert flagged[0]["confidence"] == 0.91
    assert "visual" in flagged[0]["evidence"]
    assert "qwen" in flagged[0]["evidence"]


@pytest.mark.unit
def test_media_binary_keeps_canonical_text_as_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services.analysis import analyze_content_result

    image = tmp_path / "opaque-name.jpg"
    image.write_bytes(b"placeholder")
    monkeypatch.setattr(
        "app.services.nudity.analyze_image_result",
        lambda _path: SimpleNamespace(findings=(), cacheable=True),
    )
    monkeypatch.setattr(
        "app.services.vision.analyze_lightweight_image_file",
        lambda _path, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.analysis._skip_heavy_ocr_for_gallery",
        lambda *_args, **_kwargs: True,
    )

    outcome = analyze_content_result(
        image,
        "image/jpeg",
        "media_image",
        "Rencana makar malam ini",
        ["makar"],
    )

    assert any(item["category"] == "incitement" for item in outcome.findings)
    assert any("makar" in item["evidence"].lower() for item in outcome.findings)


@pytest.mark.unit
def test_comments_body_ocr_strips_chrome():
    from app.acquisition.social_ocr import _comments_body_from_ocr

    cleaned = _comments_body_from_ocr(
        "Komentar\nPilih\nPistol\nTerbaru ke terlama\nMakar"
    )
    assert cleaned is not None
    assert "Pistol" in cleaned
    assert "Makar" in cleaned
    assert "Komentar" not in cleaned
    assert "Pilih" not in cleaned


@pytest.mark.unit
def test_ocr_progress_counts_paddle_evidence_not_only_ocr_label():
    from app.services.analysis import _is_ocr_progress_hit

    assert _is_ocr_progress_hit("OCR: ganti presiden", "") is True
    assert _is_ocr_progress_hit(
        "Meme politik",
        "[paddleocr] MENUJU INDONESIA EMAS ATAU INDONESIA CEMAS",
    ) is True
    assert _is_ocr_progress_hit("Indikasi visual LGBT", "Berkas: gallery/a.jpg") is False


@pytest.mark.unit
def test_visible_ui_screenshot_uses_analysis_easyocr_when_enabled(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services.analysis import analyze_content_result

    monkeypatch.setattr(settings, "ocr_enabled", True)
    path = tmp_path / "ig_profile.png"
    path.write_bytes(b"png")
    called = {"full": 0, "light": 0}
    monkeypatch.setattr(
        "app.services.nudity.analyze_image_result",
        lambda _path: SimpleNamespace(findings=(), cacheable=True),
    )
    monkeypatch.setattr(
        "app.services.sd_detector.analyze_image_result",
        lambda _path: SimpleNamespace(
            findings=(), cacheable=True, used=False, warning=None
        ),
    )
    monkeypatch.setattr(
        "app.services.vision.analyze_image_file",
        lambda *_a, **_k: called.__setitem__("full", called["full"] + 1) or [],
    )
    monkeypatch.setattr(
        "app.services.vision.analyze_lightweight_image_file",
        lambda *_a, **_k: called.__setitem__("light", called["light"] + 1) or [],
    )

    analyze_content_result(path, "image/png", "visible_ui", "", [])

    assert called["full"] == 1
    assert called["light"] == 0


@pytest.mark.unit
def test_android_transfer_skips_host_easyocr_by_default(monkeypatch):
    from app.acquisition.social_ocr import build_social_snapshot_enrichments
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "android_social_host_ocr_enabled", False)
    called = []
    monkeypatch.setattr(
        "app.acquisition.social_ocr._host_ocr_backend",
        lambda: called.append("backend") or None,
    )
    result = build_social_snapshot_enrichments(
        session_id="session-1",
        crawl_id="crawl-1",
        records={},
        artifacts=[],
        local_paths={},
    )
    assert result == []
    assert called == []
