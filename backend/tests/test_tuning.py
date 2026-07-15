"""Performance tuning knobs — OCR resize, video caps, Whisper duration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core import config
from app.services import hash_cache
from app.services.ocr import FakeOCRBackend, prepare_ocr_path, run_ocr
from app.services.gpu_stack import audio_whisper


@pytest.mark.unit
def test_prepare_ocr_path_downscales_large_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 256)
    big = tmp_path / "poster.jpg"
    Image.new("RGB", (2000, 1500), (255, 255, 255)).save(big)

    ocr_path, tmp = prepare_ocr_path(big)
    try:
        assert tmp is not None
        with Image.open(ocr_path) as im:
            assert max(im.size) <= 256
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_prepare_ocr_path_skips_small_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 1280)
    monkeypatch.setattr(config.settings, "ocr_min_edge_px", 0)
    monkeypatch.setattr(config.settings, "ocr_sharpen", False)
    small = tmp_path / "thumb.jpg"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(small)

    ocr_path, tmp = prepare_ocr_path(small)
    assert ocr_path == small
    assert tmp is None


@pytest.mark.unit
def test_prepare_ocr_path_sharpens_small_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 1280)
    monkeypatch.setattr(config.settings, "ocr_min_edge_px", 0)
    monkeypatch.setattr(config.settings, "ocr_sharpen", True)
    small = tmp_path / "thumb.jpg"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(small)

    ocr_path, tmp = prepare_ocr_path(small)
    try:
        assert tmp is not None
        assert ocr_path != small
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_engine_fingerprint_includes_tuning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 1280)
    fp = hash_cache.engine_fingerprint()
    assert "v12" in fp
    assert "ocr_px=1280" in fp
    assert "wh1st=" in fp
    assert "clip=" in fp
    assert "ocr_min=" in fp

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 640)
    assert hash_cache.engine_fingerprint() != fp


@pytest.mark.unit
def test_fuse_tokoh_and_hate_text(tmp_path: Path):
    from app.services.ocr import fuse_tokoh_and_text

    path = tmp_path / "meme_jokowi.jpg"
    path.write_bytes(b"x")
    ocr_findings = [
        {
            "category": "anti_pemerintah",
            "label": "OCR: ganti presiden",
            "confidence": 0.86,
            "layer_origin": "L3",
            "evidence": "[fake] GANTI PRESIDEN",
        }
    ]
    tokoh = [
        {
            "category": "konten_visual",
            "label": "Tokoh: indikasi foto Jokowi",
            "confidence": 0.8,
            "layer_origin": "L3",
            "evidence": "[clip] x",
        }
    ]
    fused = fuse_tokoh_and_text(
        path=path,
        ocr_text="Seribu kali GANTI PRESIDEN Jokowi lengserkan",
        ocr_backend="fake",
        tokoh_findings=tokoh,
        ocr_findings=ocr_findings,
    )
    assert any("Meme/poster tokoh + ujaran" in f["label"] for f in fused)
    assert any(f["label"].startswith("Tokoh:") for f in fused)
    assert any(f["label"].startswith("OCR:") for f in fused)


@pytest.mark.unit
def test_consolidate_image_findings_merges_ocr_and_drops_meme_dupes():
    from app.services.ocr import consolidate_image_findings

    raw = [
        {"category": "anti_pemerintah", "label": "OCR: presiden", "confidence": 0.86, "layer_origin": "L3", "evidence": "[easyocr] teks"},
        {"category": "anti_pemerintah", "label": "OCR: ganti presiden", "confidence": 0.86, "layer_origin": "L3", "evidence": "[easyocr] teks"},
        {"category": "anti_pemerintah", "label": "OCR: jokowi", "confidence": 0.86, "layer_origin": "L3", "evidence": "[easyocr] teks"},
        {"category": "konten_visual", "label": "Tokoh: indikasi foto Jokowi", "confidence": 0.8, "layer_origin": "L3", "evidence": "clip"},
        {
            "category": "anti_pemerintah",
            "label": "Meme/poster tokoh + ujaran: ganti presiden",
            "confidence": 0.93,
            "layer_origin": "L3",
            "evidence": "fuse",
        },
    ]
    out = consolidate_image_findings(raw)
    assert len(out) == 1
    assert out[0]["label"].startswith("Meme/poster tokoh + ujaran:")

    merged = consolidate_image_findings(raw[:3])
    assert len(merged) == 1
    assert merged[0]["label"] == "OCR: presiden, ganti presiden, jokowi"


@pytest.mark.unit
def test_fuse_no_composite_without_hate(tmp_path: Path):
    from app.services.ocr import fuse_tokoh_and_text

    path = tmp_path / "portrait.jpg"
    path.write_bytes(b"x")
    fused = fuse_tokoh_and_text(
        path=path,
        ocr_text="Presiden Republik Indonesia resmi",
        ocr_backend="fake",
        tokoh_findings=[
            {
                "category": "konten_visual",
                "label": "Tokoh: indikasi foto Presiden RI",
                "confidence": 0.8,
                "layer_origin": "L3",
                "evidence": "x",
            }
        ],
        ocr_findings=[{"category": "anti_pemerintah", "label": "OCR: presiden", "confidence": 0.8, "layer_origin": "L3", "evidence": "x"}],
    )
    assert not any("Meme/poster tokoh + ujaran" in f["label"] for f in fused)


@pytest.mark.unit
def test_clip_tokoh_noop_without_transformers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.services import clip_tokoh

    monkeypatch.setattr(config.settings, "clip_tokoh_enabled", True)
    clip_tokoh.reset_model()
    monkeypatch.setattr(clip_tokoh, "_get_pipeline", lambda: (None, None))
    img = tmp_path / "x.jpg"
    pytest.importorskip("PIL")
    from PIL import Image

    Image.new("RGB", (64, 64), (120, 80, 40)).save(img)
    assert clip_tokoh.analyze_image_tokoh(img) == []


@pytest.mark.unit
def test_prepare_ocr_path_upscales_tiny_meme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("PIL")
    from PIL import Image

    tiny = tmp_path / "meme.jpg"
    Image.new("RGB", (360, 640), (255, 255, 255)).save(tiny)
    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 2200)
    monkeypatch.setattr(config.settings, "ocr_min_edge_px", 1200)
    monkeypatch.setattr(config.settings, "ocr_sharpen", False)
    ocr_path, tmp = prepare_ocr_path(tiny)
    try:
        assert tmp is not None
        with Image.open(ocr_path) as im:
            assert max(im.size) >= 1200
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_normalize_ocr_text_spacing_and_typos():
    from app.services.ocr import normalize_ocr_text

    assert "NGOCOK 30" in normalize_ocr_text("SEHARINGOCOK30X")
    assert "jakarta" in normalize_ocr_text("Gubernur DKI Jakaria").lower()
    assert "dki jakarta" in normalize_ocr_text("GUBERNUR DKIJAKARIA").lower()
    assert "ganti" in normalize_ocr_text("gantl presidens").lower()


@pytest.mark.unit
def test_easyocr_lines_filters_low_conf_and_sorts():
    from app.services.ocr import _easyocr_lines

    rows = [
        [[[100, 80], [200, 80], [200, 100], [100, 100]], "BOTTOM", 0.9],
        [[[10, 10], [80, 10], [80, 30], [10, 30]], "TOP", 0.95],
        [[[50, 50], [60, 50], [60, 55], [50, 55]], "xx", 0.05],
    ]
    text, avg = _easyocr_lines(rows, paragraph=False, min_conf=0.18)
    assert text.startswith("TOP")
    assert "BOTTOM" in text
    assert "xx" not in text
    assert avg is not None and avg > 0.5


@pytest.mark.unit
def test_ocr_corpus_includes_tokoh():
    from app.services.ocr import ocr_keyword_corpus

    corpus = [k.lower() for k in ocr_keyword_corpus()]
    assert "presiden" in corpus
    assert "jokowi" in corpus or "joko widodo" in corpus


@pytest.mark.unit
def test_run_ocr_uses_resize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 128)
    monkeypatch.setattr(config.settings, "ocr_enabled", True)
    img = tmp_path / "chat.jpg"
    Image.new("RGB", (800, 600), (255, 255, 255)).save(img)

    fake = FakeOCRBackend(forced_text="demo provokasi")
    result = run_ocr(img, backend=fake)
    assert result is not None
    assert "provokasi" in result.text or "demo" in result.text


@pytest.mark.unit
def test_is_junk_media_path():
    from app.services.acquisition import _is_junk_media_path

    assert _is_junk_media_path("/sdcard/Movies/.nomedia")
    assert _is_junk_media_path("/sdcard/Download/.database_uuid")
    assert _is_junk_media_path("/x/Thumbs.db")
    assert not _is_junk_media_path("/sdcard/Download/clip.mp4")
    assert not _is_junk_media_path("/sdcard/DCIM/Camera/IMG_001.jpg")


@pytest.mark.unit
def test_video_keyword_corpus_includes_papua():
    from app.services.lexicon import video_keyword_corpus, match_keywords

    corpus = video_keyword_corpus()
    assert "papua" in [k.lower() for k in corpus]
    text = "Hutan Papua kini suhantur pesta sangat haram"
    hits = match_keywords(text, corpus)
    assert "papua" in [h.lower() for h in hits]
    assert "pesta" in [h.lower() for h in hits]


@pytest.mark.unit
def test_whisper_skips_long_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "gpu_whisper_enabled", True)
    monkeypatch.setattr(config.settings, "video_whisper_max_duration_s", 60)
    monkeypatch.setattr(
        audio_whisper,
        "status",
        lambda: {"available": True},
    )
    video = tmp_path / "long.mp4"
    video.write_bytes(b"fake")

    with patch("app.services.vision.video_duration_s", return_value=120.0):
        assert audio_whisper.transcribe(video) == ""
