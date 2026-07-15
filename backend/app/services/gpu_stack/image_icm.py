"""Image moderation — ICM-Assistant (rule-based explainable ICM).

Wiring order:
  1. Plugin (`SADT_GPU_ICM_PLUGIN` / ``{model}/sadt_adapter.py``)
  2. Transformers/LLaVA load when ``SADT_GPU_ICM_MODEL`` set (best-effort)
  3. Pillow visual heuristic bridge

Refs: https://github.com/zhaoyuzhi/icm-assistant
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
_pipe = None
_pipe_failed = False


def status() -> dict:
    configured = bool(settings.gpu_icm_enabled)
    available = False
    detail = "weights not loaded"
    plugin = (settings.gpu_icm_plugin or "").strip()
    if plugin:
        available = True
        detail = f"plugin={plugin}"
    elif settings.gpu_icm_model:
        try:
            import transformers  # noqa: F401

            available = True
            detail = f"transformers ready ({settings.gpu_icm_model})"
        except Exception as exc:
            detail = f"transformers unavailable: {exc}; pillow bridge"
    else:
        detail = "SADT_GPU_ICM_MODEL empty — pillow bridge"
    return {
        "name": "ICM-Assistant",
        "configured": configured,
        "available": available,
        "model": settings.gpu_icm_model,
        "plugin": plugin,
        "detail": detail,
    }


def _try_load_hf():
    """Lazy load a captioning / VLM pipeline for ICM model id or path."""
    global _pipe, _pipe_failed
    if _pipe is not None:
        return _pipe
    if _pipe_failed or not settings.gpu_icm_model:
        return None
    try:
        import torch
        from transformers import pipeline

        device = 0 if torch.cuda.is_available() else -1
        # image-to-text is the widest portable hook; ICM-LLaVA may need custom plugin
        _pipe = pipeline(
            "image-to-text",
            model=settings.gpu_icm_model,
            device=device,
            trust_remote_code=True,
        )
        return _pipe
    except Exception as exc:
        log.info("ICM HF pipeline not ready (%s) — pillow bridge", exc)
        _pipe_failed = True
        return None


def _moderate_hf(path: Path) -> list[ModerationHit]:
    pipe = _try_load_hf()
    if not pipe:
        return []
    try:
        out = pipe(str(path))
        text = ""
        if isinstance(out, list) and out:
            text = str(out[0].get("generated_text", out[0]))
        elif isinstance(out, dict):
            text = str(out.get("generated_text", out))
        else:
            text = str(out)
        kws = match_keywords(text)
        if not kws:
            return []
        return [
            ModerationHit(
                category=category_for_keyword(kws[0]),
                label=f"ICM: {kws[0]}",
                confidence=0.82,
                layer_origin=Layer.L3.value,
                evidence=text[:280],
                backend="icm-assistant",
            )
        ]
    except Exception as exc:
        log.warning("ICM HF inference failed: %s", exc)
        return []


def _bridge_pillow(path: Path) -> list[ModerationHit]:
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


def moderate(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_icm_enabled:
        return []

    model_dir = settings.gpu_icm_model or None
    if model_dir:
        p = Path(model_dir)
        if p.is_dir() or p.is_file():
            model_dir = str(p if p.is_dir() else p.parent)
        else:
            model_dir = None  # HF hub id — plugin from settings only

    plugin_hits = run_plugin(
        path,
        plugin=settings.gpu_icm_plugin or None,
        model_dir=model_dir,
        default_layer=Layer.L3.value,
        default_backend="icm-assistant",
    )
    if plugin_hits is not None:
        return plugin_hits

    hf_hits = _moderate_hf(path)
    if hf_hits:
        return hf_hits

    return _bridge_pillow(path)
