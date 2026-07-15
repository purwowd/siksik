"""Video moderation — SafeWatch (policy-following video guardrail).

Wiring order:
  1. Optional plugin (`SADT_GPU_SAFEWATCH_PLUGIN` or ``{model}/sadt_adapter.py``)
  2. Fall back to path/keyframe lexicon bridge (jangan return [] bila checkpoint ada)

Refs: https://github.com/BillChan226/SafeWatch · https://safewatch-aiguard.github.io/
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.plugin import run_plugin
from app.services.gpu_stack.types import ModerationHit
from app.services.lexicon import category_for_keyword, match_keywords

log = logging.getLogger(__name__)


def status() -> dict:
    configured = bool(settings.gpu_safewatch_enabled)
    available = False
    detail = "checkpoint not loaded"
    plugin = (settings.gpu_safewatch_plugin or "").strip()
    if plugin:
        available = True
        detail = f"plugin={plugin}"
    elif settings.gpu_safewatch_model:
        p = Path(settings.gpu_safewatch_model)
        if p.exists():
            available = True
            adapter = (p / "sadt_adapter.py") if p.is_dir() else (p.parent / "sadt_adapter.py")
            detail = f"checkpoint ok ({p})" + (
                "; sadt_adapter.py found" if adapter.is_file() else "; set plugin or sadt_adapter.py"
            )
        else:
            detail = f"path missing: {p} — keyframe/path bridge"
    else:
        detail = "SADT_GPU_SAFEWATCH_MODEL empty — keyframe/path bridge"
    return {
        "name": "SafeWatch",
        "configured": configured,
        "available": available,
        "model": settings.gpu_safewatch_model,
        "plugin": plugin,
        "detail": detail,
    }


def _bridge_policy(path: Path) -> list[ModerationHit]:
    from app.services.vision import extract_video_keyframes

    blobs: list[str] = [f"{path.parent.name} {path.name}"]
    frames = extract_video_keyframes(path, max_frames=min(3, settings.gpu_video_keyframes))
    try:
        for fr in frames:
            blobs.append(fr.name)
            try:
                from app.services import ocr as ocr_mod

                if settings.ocr_enabled or settings.media_text_enabled or settings.gpu_stack_enabled:
                    r = ocr_mod.run_ocr(fr)
                    if r and r.text:
                        blobs.append(r.text)
            except Exception:
                pass
    finally:
        for fr in frames:
            try:
                fr.unlink(missing_ok=True)
            except OSError:
                pass
        if frames:
            try:
                frames[0].parent.rmdir()
            except OSError:
                pass

    kws = match_keywords(" ".join(blobs))
    if not kws:
        return []
    wired = bool(status()["available"])
    return [
        ModerationHit(
            category=category_for_keyword(kws[0]),
            label=f"SafeWatch: {kws[0]}",
            confidence=0.72 if wired else 0.68,
            layer_origin=Layer.L4.value,
            evidence=f"{path.name} | policies={','.join(kws[:5])}"[:280],
            backend="safewatch" if wired else "safewatch-bridge",
        )
    ]


def moderate(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_safewatch_enabled:
        return []

    model_dir = settings.gpu_safewatch_model or None
    if model_dir:
        p = Path(model_dir)
        if p.is_file():
            model_dir = str(p.parent)

    plugin_hits = run_plugin(
        path,
        plugin=settings.gpu_safewatch_plugin or None,
        model_dir=model_dir,
        default_layer=Layer.L4.value,
        default_backend="safewatch",
    )
    if plugin_hits is not None:
        return plugin_hits

    return _bridge_policy(path)
