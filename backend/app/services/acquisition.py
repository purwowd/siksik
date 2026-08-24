"""Backward-compatible facade — canonical implementation is ``app.acquisition.orchestration``."""

from __future__ import annotations

from app.acquisition import orchestration as _orchestration

__all__ = [name for name in dir(_orchestration) if not name.startswith("__")]


def __getattr__(name: str):
    return getattr(_orchestration, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_orchestration)))
