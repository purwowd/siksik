from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.schemas import AcquisitionMode

QUICK_LOOKBACK_MONTHS = 3
FULL_LOOKBACK_MONTHS = 6
MIN_TIME_SCOPE_EPOCH_MS = 946_684_800_000


@dataclass(frozen=True, slots=True)
class AcquisitionTimeScope:
    lookback_months: int
    not_before: datetime

    @property
    def not_before_epoch_ms(self) -> int:
        return int(self.not_before.timestamp() * 1000)


def build_time_scope(
    mode: AcquisitionMode,
    *,
    reference: datetime | None = None,
) -> AcquisitionTimeScope:
    if mode == AcquisitionMode.QUICK:
        months = QUICK_LOOKBACK_MONTHS
    elif mode == AcquisitionMode.FULL:
        months = FULL_LOOKBACK_MONTHS
    else:
        raise ValueError("unsupported acquisition mode")
    current = reference or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("acquisition time reference must be timezone-aware")
    current = current.astimezone(timezone.utc)
    month_index = current.year * 12 + current.month - 1 - months
    target_year, zero_based_month = divmod(month_index, 12)
    target_month = zero_based_month + 1
    target_day = min(current.day, calendar.monthrange(target_year, target_month)[1])
    return AcquisitionTimeScope(
        lookback_months=months,
        not_before=current.replace(
            year=target_year,
            month=target_month,
            day=target_day,
        ),
    )
