"""Image moderation — ICM-Assistant (rule-based explainable ICM).

Plugin expects local/HF weights via SADT_GPU_ICM_MODEL.
Until weights + runtime deps are installed, falls back to Pillow heuristic bridge.
Refs: https://github.com/zhaoyuzhi/icm-assistant
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)
_pipe = None


def status() -> dict:
    configured = bool(settings.gpu_icm_enabled)
    available = False
    detail = "weights not loaded"
    if settings.gpu_icm_model:
        try:
            # LLaVA-style CLI stack used by ICM-Assistant
            import llava  # noqa: F401

            available = True
            detail = f"llava stack ({settings.gpu_icm_model})"
        except Exception as exc:
            detail = f"llava unavailable: {exc}; using pillow bridge"
    else:
        detail = "SADT_GPU_ICM_MODEL empty — pillow bridge"
    return {
        "name": "ICM-Assistant",
        "configured": configured,
        "available": available,
        "model": settings.gpu_icm_model,
        "detail": detail,
    }


def _get_pipe():
    """Lazy-load ICM/LLaVA pipeline when installed."""
    global _pipe
    if _pipe is not None:
        return _pipe
    # Placeholder for full ICM runtime — weights downloaded separately.
    # Expected: zhaoyuzhi/ICM-LLaVA-v1.5-7B or local path.
    _pipe = False  # explicit "tried, not ready"
    log.info("ICM-Assistant weights not wired yet — pillow heuristic bridge active")
    return None


def moderate(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_icm_enabled:
        return []

    pipe = _get_pipe()
    if pipe:
        # Future: call ICM rule-based moderation and map labels → ModerationHit
        return []

    # Bridge: reuse Pillow visual heuristics so GPU mode still adds signal before weights land
    try:
        from app.services import vision as vis

        raw = vis._analyze_pil_image(path)  # noqa: SLF001 — intentional bridge
    except Exception as exc:
        log.warning("ICM bridge failed: %s", exc)
        return []

    hits: list[ModerationHit] = []
    for f in raw:
        hits.append(
            ModerationHit(
                category=f.get("category", "konten_visual"),
                label=f"ICM-bridge: {f.get('label', 'visual')}",
                confidence=float(f.get("confidence", 0.7)),
                layer_origin=Layer.L3.value,
                evidence=str(f.get("evidence", path.name))[:280],
                backend="icm-assistant-bridge",
            )
        )
    return hits
