"""Unit tests for L3 vision heuristics."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import vision as vis


@pytest.mark.unit
def test_vision_status_keys():
    st = vis.vision_status()
    assert "pillow" in st
    assert "ffmpeg" in st
    assert "torch_cuda" in st


@pytest.mark.unit
def test_analyze_image_with_pillow(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    # Strong red high-contrast poster-like image + risky filename
    img = Image.new("RGB", (200, 200), (220, 20, 20))
    path = tmp_path / "poster_provokasi_demo.jpg"
    img.save(path)
    findings = vis.analyze_image_file(path)
    assert findings
    assert findings[0]["layer_origin"] == "L3"


@pytest.mark.unit
def test_safe_image_no_false_alarm(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (200, 200), (180, 180, 180))
    path = tmp_path / "family_photo.jpg"
    img.save(path)
    findings = vis.analyze_image_file(path)
    assert findings == []
