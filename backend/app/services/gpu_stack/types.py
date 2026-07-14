"""Shared types for GPU moderation stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModerationHit:
    category: str
    label: str
    confidence: float
    layer_origin: str  # L3 | L4 | L2
    evidence: str
    backend: str
    extras: dict[str, Any] = field(default_factory=dict)

    def as_finding(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "confidence": round(min(0.99, max(0.0, self.confidence)), 3),
            "layer_origin": self.layer_origin,
            "evidence": f"[{self.backend}] {self.evidence}"[:320],
        }


@dataclass
class StackStatus:
    enabled: bool
    device: str | None
    backends: dict[str, dict[str, Any]]
