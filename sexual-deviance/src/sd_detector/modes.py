from __future__ import annotations

from enum import Enum


class DetectionMode(str, Enum):
    """Tier operasi — pilih sesuai latency vs akurasi."""

    FAST = "fast"
    """Prescreen + NudeNet saja (~50ms). Tanpa LLM."""

    BALANCED = "balanced"
    """FAST + 1x VLM describe/classify (~1-2s). Tanpa multi-crop orientasi."""

    FULL = "full"
    """BALANCED + orientasi via 1 center-crop (~2-4s)."""
