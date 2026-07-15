"""Unit tests — media date + risk timeline 5 tahun."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.services.media_dates import capture_meta, extract_captured_at
from app.services.timeline import build_risk_timeline


@pytest.mark.unit
def test_filename_date_android(tmp_path: Path):
    p = tmp_path / "IMG_20210315_120000.jpg"
    p.write_bytes(b"x")
    dt, src = extract_captured_at(p)
    assert src == "filename"
    assert dt is not None
    assert dt.year == 2021 and dt.month == 3 and dt.day == 15


@pytest.mark.unit
def test_screenshot_filename_year(tmp_path: Path):
    p = tmp_path / "Screenshot_20260710_212545_Attacker_191.jpg"
    p.write_bytes(b"x")
    meta = capture_meta(p)
    assert meta.get("captured_year") == 2026
    assert meta.get("date_source") == "filename"


@pytest.mark.unit
def test_timeline_improved_when_current_year_zero():
    rows = [
        {"media_year": 2022, "category": "anti_pemerintah"},
        {"media_year": 2022, "category": "anti_pemerintah"},
        {"media_year": 2023, "category": "perilaku_menyimpang"},
        {"media_year": 2024, "category": "anti_pemerintah"},
    ]
    tl = build_risk_timeline(rows, years_back=5, now=datetime(2026, 7, 1))
    assert tl["trend"] == "improved"
    assert tl["current_year_count"] == 0
    assert tl["peak_count"] >= 2
    assert "penurunan" in tl["insight"].lower() or "0 temuan" in tl["insight"]
    years = [s["year"] for s in tl["series"]]
    assert years == [2022, 2023, 2024, 2025, 2026]


@pytest.mark.unit
def test_timeline_elevated_current_year():
    rows = [
        {"media_year": 2024, "category": "anti_pemerintah"},
        {"media_year": 2026, "category": "anti_pemerintah"},
        {"media_year": 2026, "category": "anti_pemerintah"},
        {"media_year": 2026, "category": "konten_audio"},
    ]
    tl = build_risk_timeline(rows, years_back=5, now=datetime(2026, 7, 1))
    assert tl["current_year_count"] == 3
    assert tl["trend"] in {"elevated", "stable", "improving"}
