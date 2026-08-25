"""Portable zero-shot visual signals for the shared content taxonomy."""

from __future__ import annotations

import logging
import math
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.content_policy import (
    CONTENT_CATEGORY_LABELS,
    DEMONSTRATION,
    EXTREMISM,
    LGBT_CONTENT,
    POLITICAL_CAMPAIGN,
    POLITICAL_MEME,
)

log = logging.getLogger(__name__)

CONTENT_VISUAL_REVISION = "paired-prompts-v1"

# Each category is compared with a hard negative in the same forward pass.
# This is deliberately content-based and never classifies a person's identity
# or sexual orientation from appearance.
_PROMPT_PAIRS: tuple[tuple[str, str, str], ...] = (
    (
        LGBT_CONTENT,
        "a clearly visible LGBTQ pride flag, rainbow pride flag, or transgender pride flag",
        "an ordinary rainbow in the sky or a colorful object that is not an LGBTQ flag",
    ),
    (
        POLITICAL_MEME,
        "an Indonesian political meme with overlaid satirical text about a government or politician",
        "an ordinary social media screenshot or personal photo without a political meme",
    ),
    (
        POLITICAL_CAMPAIGN,
        "an Indonesian political election campaign poster, candidate rally, or party campaign event",
        "an ordinary group photo or public event without political campaign material",
    ),
    (
        DEMONSTRATION,
        "a street demonstration or protest with banners, placards, and protesting crowds",
        "a concert, sports crowd, ceremony, or ordinary gathering without a protest",
    ),
    (
        EXTREMISM,
        "extremist propaganda imagery or a clearly identifiable extremist organization symbol",
        "an ordinary national flag, religious event, or generic symbol without extremist propaganda",
    ),
)

_model = None
_processor = None
_model_id: str | None = None
_load_failed = False


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def reset_model() -> None:
    global _model, _processor, _model_id, _load_failed
    _model = None
    _processor = None
    _model_id = None
    _load_failed = False


def _get_configured_model():
    global _model, _processor, _model_id, _load_failed
    model_id = (settings.content_visual_model or "").strip()
    if not model_id:
        return None, None
    if _model is not None and _model_id == model_id:
        return _model, _processor
    if _load_failed:
        return None, None
    try:
        import torch
        from transformers import AutoModel, AutoProcessor

        kwargs = {
            "local_files_only": bool(settings.content_models_local_only),
            "trust_remote_code": False,
        }
        processor = AutoProcessor.from_pretrained(model_id, **kwargs)
        dtype = torch.float16 if _device() == "cuda" else torch.float32
        model = AutoModel.from_pretrained(model_id, torch_dtype=dtype, **kwargs)
        model.to(_device())
        model.eval()
        _model = model
        _processor = processor
        _model_id = model_id
        return _model, _processor
    except Exception as exc:
        # Missing optional weights must degrade to the existing CLIP path, not
        # block a running acquisition while trying to reach the network.
        log.warning("Content visual model unavailable (%s): %s", model_id, exc)
        _load_failed = True
        return None, None


def _score_configured(path: Path, prompts: list[str]) -> list[float] | None:
    model, processor = _get_configured_model()
    if model is None or processor is None:
        return None
    try:
        import torch
        from PIL import Image

        with Image.open(path) as image_file:
            image = image_file.convert("RGB")
            image.thumbnail((384, 384))
        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        inputs = {key: value.to(_device()) for key, value in inputs.items()}
        with torch.no_grad():
            output = model(**inputs)
        logits = getattr(output, "logits_per_image", None)
        if logits is None:
            return None
        return [float(value) for value in logits[0].detach().float().cpu().tolist()]
    except Exception as exc:
        log.debug("Content visual inference skip %s: %s", path.name, exc)
        return None


def _score(path: Path, prompts: list[str]) -> tuple[list[float] | None, str]:
    configured = (settings.content_visual_model or "").strip()
    if configured:
        scores = _score_configured(path, prompts)
        if scores is not None:
            return scores, configured.split("/")[-1]
    try:
        from app.services.clip_tokoh import score_image_prompts

        return score_image_prompts(path, prompts), settings.clip_tokoh_model.split("/")[-1]
    except Exception as exc:
        log.debug("Content visual fallback unavailable: %s", exc)
        return None, "unavailable"


def _paired_probability(positive: float, negative: float) -> float:
    delta = max(-30.0, min(30.0, positive - negative))
    return 1.0 / (1.0 + math.exp(-delta))


def analyze_image(path: Path) -> list[dict]:
    if not settings.content_detection_enabled or not settings.content_visual_enabled:
        return []
    prompts = [prompt for _, positive, negative in _PROMPT_PAIRS for prompt in (positive, negative)]
    logits, backend = _score(path, prompts)
    if logits is None or len(logits) != len(prompts):
        return []

    findings: list[dict] = []
    base_threshold = float(settings.content_visual_threshold)
    for index, (category, positive_prompt, _negative_prompt) in enumerate(_PROMPT_PAIRS):
        positive = logits[index * 2]
        negative = logits[index * 2 + 1]
        probability = _paired_probability(positive, negative)
        # Symbolic extremism is particularly context-sensitive, so require a
        # stricter visual score and let OCR/Qwen handle ambiguous cases.
        threshold = max(base_threshold, 0.78) if category == EXTREMISM else base_threshold
        if probability < threshold:
            continue
        findings.append(
            {
                "category": category,
                "label": CONTENT_CATEGORY_LABELS[category],
                "confidence": round(min(0.95, probability), 3),
                "layer_origin": Layer.L3.value,
                "evidence": (
                    f"[visual:{backend}] {path.name} | score={probability:.3f} | "
                    f"{positive_prompt}"
                )[:320],
            }
        )
    return findings


def status() -> dict:
    configured_model = (settings.content_visual_model or "").strip()
    fallback = settings.clip_tokoh_model if settings.clip_tokoh_enabled else None
    return {
        "name": "content-visual",
        "configured": bool(settings.content_detection_enabled and settings.content_visual_enabled),
        "model": configured_model or fallback,
        "local_files_only": bool(settings.content_models_local_only),
        "threshold": float(settings.content_visual_threshold),
        "device": _device(),
        "revision": CONTENT_VISUAL_REVISION,
    }
