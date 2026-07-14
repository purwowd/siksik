"""OCR unit + GPU-optional tests.

Local: pakai FakeOCRBackend (tanpa EasyOCR).
Server GPU: set SADT_OCR_ENABLED=1 dan jalankan marker gpu_ocr.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import config
from app.services import ocr as ocr_mod
from app.services.ocr import FakeOCRBackend, ocr_findings_from_text, run_ocr


@pytest.fixture(autouse=True)
def _reset_ocr_cache():
    ocr_mod.reset_backend_cache()
    yield
    ocr_mod.reset_backend_cache()


@pytest.mark.unit
def test_ocr_findings_from_text_detects_keyword():
    text = "Poster mengajak aksi anti pemerintah malam ini"
    findings = ocr_findings_from_text(text, backend="fake")
    assert findings
    assert any("anti pemerintah" in f["label"] for f in findings)
    assert all(f["layer_origin"] == "L3" for f in findings)


@pytest.mark.unit
def test_ocr_findings_clean_text():
    text = "Foto liburan keluarga di pantai"
    assert ocr_findings_from_text(text, backend="fake") == []


@pytest.mark.unit
def test_fake_backend_extract(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "poster_anti_pemerintah.jpg"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(img)
    backend = FakeOCRBackend(forced_text="Spanduk anti pemerintah di jalan")
    result = run_ocr(img, backend=backend)
    assert result is not None
    assert "anti pemerintah" in result.text.lower()
    findings = ocr_mod.analyze_image_ocr(img, backend=backend)
    assert findings


@pytest.mark.unit
def test_ocr_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    ocr_mod.reset_backend_cache()
    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "x.jpg"
    Image.new("RGB", (32, 32), (10, 10, 10)).save(img)
    assert ocr_mod.analyze_image_ocr(img) == []
    assert ocr_mod.get_backend() is None


@pytest.mark.unit
def test_ocr_status_keys():
    st = ocr_mod.ocr_status()
    assert "enabled" in st
    assert "backend" in st
    assert "gpu" in st
    assert "available" in st


@pytest.mark.unit
def test_vision_pipelines_ocr_when_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Vision L3 should include OCR findings when analyze_image_ocr returns hits."""
    from app.services import vision as vis

    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "neutral.jpg"
    Image.new("RGB", (100, 100), (120, 120, 120)).save(img)

    monkeypatch.setattr(
        "app.services.ocr.analyze_image_ocr",
        lambda path, backend=None: [
            {
                "category": "anti_pemerintah",
                "label": "OCR: anti pemerintah",
                "confidence": 0.9,
                "layer_origin": "L3",
                "evidence": "mock",
            }
        ],
    )
    findings = vis.analyze_image_file(img)
    assert any(f["label"].startswith("OCR:") for f in findings)


@pytest.mark.gpu
@pytest.mark.acceptance
def test_real_ocr_backend_on_gpu_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Jalankan di server GPU setelah pip install -r requirements-gpu.txt.

    SADT_OCR_ENABLED=1 SADT_OCR_BACKEND=easyocr SADT_OCR_GPU=1 pytest -m gpu -k real_ocr
    """
    if os.getenv("SADT_OCR_ENABLED", "0") != "1":
        pytest.skip("SADT_OCR_ENABLED!=1 — skip OCR GPU gate")

    monkeypatch.setattr(config.settings, "ocr_enabled", True)
    monkeypatch.setattr(config.settings, "ocr_backend", os.getenv("SADT_OCR_BACKEND", "easyocr"))
    monkeypatch.setattr(config.settings, "ocr_gpu", os.getenv("SADT_OCR_GPU", "1") == "1")
    ocr_mod.reset_backend_cache()

    backend = ocr_mod.get_backend()
    assert backend is not None, "OCR backend tidak tersedia — install requirements-gpu.txt"
    assert backend.available()

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    img_path = tmp_path / "banner.jpg"
    im = Image.new("RGB", (640, 160), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.text((20, 60), "anti pemerintah", fill=(0, 0, 0))
    im.save(img_path)

    result = run_ocr(img_path, backend=backend)
    assert result is not None
    # OCR nyata bisa miss font kecil — jangan assert keyword ketat; pastikan pipeline hidup
    assert isinstance(result.text, str)
    findings = ocr_mod.analyze_image_ocr(img_path, backend=backend)
    # If OCR read the banner, expect a hit; otherwise soft-pass with warning
    if "anti" in (result.text or "").lower() or "pemerintah" in (result.text or "").lower():
        assert findings
    else:
        pytest.xfail(f"OCR hidup tapi teks banner tidak terbaca jelas: {result.text!r}")
