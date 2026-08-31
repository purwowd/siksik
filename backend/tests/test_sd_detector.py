"""Sexual-deviance adapter mapping — no real VLM or sidecar."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import config
from app.services import analysis, nudity, sd_detector
from app.services.content_policy import merge_content_findings
from app.services.hash_cache import engine_fingerprint


def _lgbt(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "present": False,
        "flag_colors": [],
        "symbols": [],
        "clothing": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _verdict(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "action": "allow",
        "severity": "safe",
        "nudity": "none",
        "orientation": "none",
        "acts": [],
        "confidence": 0.8,
        "reason": "",
        "lgbt": _lgbt(),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.unit
def test_review_maps_indonesian_label_and_staging_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    staging = tmp_path / "staging"
    rel = Path("gallery") / "ab12cd.jpg"
    target = staging / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake")
    monkeypatch.setattr(config.settings, "staging_dir", staging)

    findings = sd_detector.findings_from_verdict(
        _verdict(
            action="review",
            severity="suggestive",
            nudity="partial",
            orientation="heterosexual",
            acts=["kissing", "bikini"],
            reason="woman in bikini kissing a man on a beach",
        ),
        relative_path=sd_detector.staging_relative_path(target),
        layer="L3",
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding["category"] == "ketelanjangan"
    assert finding["label"] == "Perlu ditinjau: konten sugestif, ketelanjangan sebagian"
    assert finding["layer_origin"] == "L3"
    assert "Berkas: gallery/ab12cd.jpg" in finding["evidence"]
    assert "Relasi: pria dan wanita" in finding["evidence"]
    assert "Adegan: ciuman, bikini" in finding["evidence"]
    assert "Alasan:" in finding["evidence"]
    assert "orientation=" not in finding["evidence"]
    assert "action=" not in finding["evidence"]
    assert str(target) not in finding["evidence"]


@pytest.mark.unit
def test_block_explicit_full_nudity_label():
    findings = sd_detector.findings_from_verdict(
        _verdict(action="block", severity="explicit", nudity="full", confidence=0.94),
        relative_path="recovered_trash/trash/d9210b84c21d208435b523a1b44a981b.webp",
        layer="L3",
    )
    assert findings[0]["label"] == "Diblokir: konten eksplisit, ketelanjangan penuh"
    assert "Berkas: recovered_trash/trash/d9210b84c21d208435b523a1b44a981b.webp" in findings[0]["evidence"]


@pytest.mark.unit
def test_allow_with_lgbt_is_pending_lgbt_finding():
    findings = sd_detector.findings_from_verdict(
        _verdict(
            action="allow",
            lgbt=_lgbt(present=True, flag_colors=["rainbow"], symbols=["pride flag"], clothing=["pride merch"]),
        ),
        relative_path="gallery/pride.jpg",
        layer="L3",
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["category"] == "lgbt_content"
    assert finding["label"] == "Indikasi visual LGBT"
    assert finding["keep_label"] is True
    assert "Berkas: gallery/pride.jpg" in finding["evidence"]
    assert "Bendera: pelangi" in finding["evidence"]
    assert "content://" not in finding["evidence"]


@pytest.mark.unit
def test_allow_without_lgbt_writes_no_finding():
    assert (
        sd_detector.findings_from_verdict(
            _verdict(action="allow"),
            relative_path="gallery/sky.jpg",
            layer="L3",
        )
        == []
    )


@pytest.mark.unit
def test_review_does_not_add_separate_lgbt_finding():
    findings = sd_detector.findings_from_verdict(
        _verdict(
            action="review",
            severity="suggestive",
            orientation="gay",
            lgbt=_lgbt(present=True, flag_colors=["rainbow"]),
        ),
        relative_path="gallery/kiss.jpg",
        layer="L3",
    )
    assert [item["category"] for item in findings] == ["ketelanjangan"]


@pytest.mark.unit
def test_device_uri_is_not_used_as_evidence_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "staging_dir", tmp_path / "staging")
    leaked = Path("content://media/external/images/media/99")
    relative = sd_detector.staging_relative_path(leaked)
    assert "content://" not in relative
    findings = sd_detector.findings_from_verdict(
        _verdict(action="review", severity="suggestive"),
        relative_path=relative,
        layer="L3",
    )
    assert "content://" not in findings[0]["evidence"]
    assert "/sdcard" not in findings[0]["evidence"]


@pytest.mark.unit
def test_sidecar_error_falls_back_to_nudenet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "gallery" / "photo.jpg"
    target.parent.mkdir()
    target.write_bytes(b"fake")
    monkeypatch.setattr(config.settings, "sd_detector_enabled", True)
    monkeypatch.setattr(config.settings, "staging_dir", tmp_path)
    monkeypatch.setattr(
        sd_detector,
        "_run_locked",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("sidecar")),
    )
    flag = {
        "category": "ketelanjangan",
        "label": "Ketelanjangan terdeteksi pada gambar: test",
        "confidence": 0.9,
        "layer_origin": "L3",
        "evidence": "fake detector",
    }
    monkeypatch.setattr(
        nudity,
        "analyze_image_result",
        lambda _path: nudity.NudityAnalysisResult((flag,), True),
    )
    monkeypatch.setattr("app.services.vision.analyze_lightweight_image_file", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.analysis._skip_heavy_ocr_for_gallery", lambda *_a, **_k: True)

    outcome = analysis.analyze_content_result(target, "image/jpeg", "gallery", "", [])

    assert flag in list(outcome.findings)
    assert outcome.cacheable is False


@pytest.mark.unit
def test_successful_sd_skips_nudenet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    staging = tmp_path / "staging"
    target = staging / "gallery" / "kiss.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake")
    monkeypatch.setattr(config.settings, "sd_detector_enabled", True)
    monkeypatch.setattr(config.settings, "staging_dir", staging)
    sd_flag = {
        "category": "ketelanjangan",
        "label": "Perlu ditinjau: konten sugestif",
        "confidence": 0.81,
        "layer_origin": "L3",
        "evidence": "Berkas: gallery/kiss.jpg. Relasi: pria dan wanita.",
    }
    monkeypatch.setattr(
        sd_detector,
        "analyze_image_result",
        lambda _path: sd_detector.SdAnalysisResult((sd_flag,), True, used=True),
    )

    def _nudenet_must_not_run(_path):
        raise AssertionError("NudeNet must not run when SD succeeds")

    monkeypatch.setattr(nudity, "analyze_image_result", _nudenet_must_not_run)
    monkeypatch.setattr("app.services.vision.analyze_lightweight_image_file", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.analysis._skip_heavy_ocr_for_gallery", lambda *_a, **_k: True)

    outcome = analysis.analyze_content_result(target, "image/jpeg", "gallery", "", [])

    assert sd_flag in list(outcome.findings)
    assert outcome.cacheable is True


@pytest.mark.unit
def test_merge_keeps_sd_lgbt_label():
    merged = merge_content_findings(
        [
            {
                "category": "lgbt_content",
                "label": "Indikasi visual LGBT",
                "confidence": 0.7,
                "layer_origin": "L3",
                "evidence": "Berkas: gallery/flag.jpg. Bendera: pelangi.",
                "keep_label": True,
            },
            {
                "category": "lgbt_content",
                "label": "LGBT text/flag",
                "confidence": 0.84,
                "layer_origin": "L3",
                "evidence": "[visual] pride flag",
            },
        ]
    )
    assert len(merged) == 1
    assert merged[0]["label"] == "Indikasi visual LGBT"
    assert "keep_label" not in merged[0]
    assert "Berkas: gallery/flag.jpg" in merged[0]["evidence"]


@pytest.mark.unit
def test_engine_fingerprint_tracks_sd_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "sd_detector_enabled", False)
    off = engine_fingerprint()
    monkeypatch.setattr(config.settings, "sd_detector_enabled", True)
    monkeypatch.setattr(config.settings, "sd_mode", "balanced")
    monkeypatch.setattr(config.settings, "sd_llama_host", "127.0.0.1")
    monkeypatch.setattr(config.settings, "sd_llama_port", 8080)
    on = engine_fingerprint()
    assert "sd=0" in off
    assert "sd=1:balanced:127.0.0.1:8080" in on
    assert off != on
