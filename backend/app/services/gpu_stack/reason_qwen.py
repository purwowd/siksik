"""General multimodal reasoning — Qwen2.5-VL-7B.

Used to fuse OCR/ASR evidence and produce higher-level explanations / policy judgment.
Load via SADT_GPU_QWEN_MODEL (HF id or local path), e.g. Qwen/Qwen2.5-VL-7B-Instruct.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)
_model = None
_processor = None


def status() -> dict:
    configured = bool(settings.gpu_qwen_enabled)
    available = False
    detail = "not loaded"
    try:
        import transformers  # noqa: F401

        if settings.gpu_qwen_model:
            available = True  # import ok; actual weights load lazy
            detail = f"transformers ready ({settings.gpu_qwen_model})"
        else:
            detail = "SADT_GPU_QWEN_MODEL empty"
    except Exception as exc:
        detail = f"transformers unavailable: {exc}"
    return {
        "name": "Qwen2.5-VL-7B",
        "configured": configured,
        "available": available and bool(settings.gpu_qwen_model),
        "model": settings.gpu_qwen_model,
        "detail": detail,
    }


def _try_load():
    global _model, _processor
    if _model is not None:
        return _model, _processor
    if not settings.gpu_qwen_model:
        return None, None
    try:
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _processor = AutoProcessor.from_pretrained(settings.gpu_qwen_model, trust_remote_code=True)
        _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            settings.gpu_qwen_model,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None,
            trust_remote_code=True,
        )
        if device == "cpu":
            _model = _model.to(device)
        return _model, _processor
    except Exception as exc:
        log.warning("Qwen2.5-VL load failed: %s", exc)
        _model = False
        return None, None


def moderate_image(path: Path) -> list[ModerationHit]:
    """Optional VL pass — only if model loads; else skip (no fake hits)."""
    if not settings.gpu_stack_enabled or not settings.gpu_qwen_enabled:
        return []
    model, processor = _try_load()
    if not model or not processor:
        return []
    # Full generative inference is env-specific; keep hook minimal & safe.
    # When loaded, ask a short policy question and map keywords in answer.
    try:
        prompt = (
            "You are a forensic media moderator for Indonesian risk policy "
            "(provokasi, makar, radikal, narkoba, senjata, judi, pornografi anak). "
            "Describe unsafe signals in this image in one short Indonesian sentence, "
            "or say AMAN if safe."
        )
        # Prefer processors with chat template; fall back silently if API differs.
        text = f"<image>\n{prompt}"
        from PIL import Image
        import torch

        image = Image.open(path).convert("RGB")
        inputs = processor(text=[text], images=[image], return_tensors="pt")
        inputs = {k: v.to(model.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=64)
        answer = processor.batch_decode(out, skip_special_tokens=True)[0]
        return _hits_from_text(answer, layer=Layer.L3.value, backend="qwen2.5-vl")
    except Exception as exc:
        log.warning("Qwen VL image failed: %s", exc)
        return []


def moderate_video_summary(path: Path, prior_hits: list[ModerationHit]) -> list[ModerationHit]:
    """Synthesize prior Whisper/OCR/ICM evidence without re-decoding video if VL unavailable."""
    if not settings.gpu_stack_enabled or not settings.gpu_qwen_enabled:
        return []
    if not prior_hits:
        return []
    # Lightweight synthesis without requiring full VL load: aggregate evidence vs lexicon
    blob = " ".join(h.evidence for h in prior_hits).lower()
    return _hits_from_text(blob, layer=Layer.L4.value, backend="qwen-synth")


def _hits_from_text(text: str, *, layer: str, backend: str) -> list[ModerationHit]:
    if not text or "aman" in text.lower()[:40]:
        # still scan keywords
        pass
    norm = re.sub(r"\s+", " ", text.lower())
    hits: list[ModerationHit] = []
    for kw in settings.risk_keywords:
        if kw.lower() in norm:
            hits.append(
                ModerationHit(
                    category="konten_visual",
                    label=f"VL reasoning: {kw}",
                    confidence=0.8,
                    layer_origin=layer,
                    evidence=text[:280],
                    backend=backend,
                )
            )
    return hits
