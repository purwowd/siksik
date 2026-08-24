"""Timeline hanya menghitung temuan dikonfirmasi analis."""

from __future__ import annotations

import pytest

from app.services.timeline import build_risk_timeline


@pytest.mark.unit
def test_timeline_ignores_pending_and_rejected():
    rows = [
        {"media_year": 2026, "category": "konten_visual", "review_status": "confirmed"},
        {"media_year": 2026, "category": "konten_visual", "review_status": "pending"},
        {"media_year": 2026, "category": "konten_visual", "review_status": "rejected"},
    ]
    data = build_risk_timeline(rows, years_back=5, now=__import__("datetime").datetime(2026, 8, 23))
    assert data["current_year_count"] == 1
    assert data["series"][-1]["total"] == 1
