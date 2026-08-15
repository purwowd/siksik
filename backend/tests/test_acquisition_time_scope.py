from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.acquisition.time_scope import build_time_scope
from app.models.schemas import AcquisitionMode


@pytest.mark.unit
def test_quick_and_full_use_calendar_month_boundaries() -> None:
    reference = datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc)

    quick = build_time_scope(AcquisitionMode.QUICK, reference=reference)
    full = build_time_scope(AcquisitionMode.FULL, reference=reference)

    assert quick.lookback_months == 3
    assert quick.not_before == datetime(2026, 5, 31, 12, 30, tzinfo=timezone.utc)
    assert full.lookback_months == 6
    assert full.not_before == datetime(2026, 2, 28, 12, 30, tzinfo=timezone.utc)


@pytest.mark.unit
def test_time_scope_rejects_naive_reference() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_time_scope(
            AcquisitionMode.QUICK,
            reference=datetime(2026, 8, 14, 10, 0),
        )
