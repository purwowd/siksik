"""Video moderation — SafeWatch (policy-following video guardrail).

Plugin expects checkpoint via SADT_GPU_SAFEWATCH_MODEL.
Refs: https://github.com/BillChan226/SafeWatch · https://safewatch-aiguard.github.io/
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)


def status() -> dict:
    configured = bool(settings.gpu_safewatch_enabled)
    available = False
    detail = "checkpoint not loaded"
    if settings.gpu_safewatch_model:
        # SafeWatch ships custom eval scripts; treat path existence as readiness signal
        p = Path(settings.gpu_safewatch_model)
        if p.exists():
            available = True
            detail = f"checkpoint path ok ({p})"
        else:
            detail = f"path missing: {p} — policy/heuristic bridge active"
    else:
        detail = "SADT_GPU_SAFEWATCH_MODEL empty — policy/heuristic bridge"
    return {
        "name": "SafeWatch",
        "configured": configured,
        "available": available,
        "model": settings.gpu_safewatch_model,
        "detail": detail,
    }


def moderate(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_safewatch_enabled:
        return []

    if status()["available"]:
        # Future: load SafeWatch and run policy guardrail → multi-label + explanation
        log.debug("SafeWatch checkpoint present but inference adapter not bundled yet")
        return []

    # Bridge until SafeWatch weights/runtime are installed: filename + light visual on frames
    # already handled by vision path; here we only emit when policy keywords hit path.
    from app.services.vision import _filename_norm, _risk_lexicon

    hay = _filename_norm(f"{path.parent.name} {path.name}")
    hits_kw = [t for t in _risk_lexicon() if t in hay]
    if not hits_kw:
        return []
    return [
        ModerationHit(
            category="konten_visual",
            label=f"SafeWatch-bridge policy: {hits_kw[0]}",
            confidence=0.7,
            layer_origin=Layer.L4.value,
            evidence=f"{path.name} | policies={','.join(hits_kw[:5])}"[:280],
            backend="safewatch-bridge",
        )
    ]
