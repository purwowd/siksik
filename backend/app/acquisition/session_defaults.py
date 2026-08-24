from __future__ import annotations

from app.models.schemas import SessionProgress, SessionStatus, TimingBreakdown


def empty_progress(phase: SessionStatus = SessionStatus.PENDING) -> dict:
    return SessionProgress(phase=phase, percent=0, message="Menunggu").model_dump()


def empty_timing() -> dict:
    return TimingBreakdown().model_dump()
