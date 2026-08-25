"""CLIP zero-shot — indikasi foto tokoh / pejabat (presiden).

Butuh: pip install transformers
Model default: openai/clip-vit-base-patch32 (lazy download sekali).
Tanpa transformers / gagal load → no-op (OCR tetap jadi jalur utama).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer

log = logging.getLogger(__name__)

_model = None
_processor = None
_model_id: str | None = None

# (id, prompt Inggris CLIP, apakah “hit” tokoh/pejabat)
_LABELS: list[tuple[str, str, bool]] = [
    ("presiden_ri", "a photo of the president of Indonesia", True),
    ("jokowi", "a photograph of Joko Widodo Jokowi Indonesian president", True),
    ("prabowo", "a photograph of Prabowo Subianto Indonesian president", True),
    ("political_portrait", "a formal political portrait of an Indonesian leader", True),
    ("campaign_poster", "an Indonesian election campaign poster with a politician", True),
    ("ordinary_selfie", "a casual selfie of an ordinary person", False),
    ("family_photo", "a family photo of ordinary people", False),
    ("landscape", "a landscape nature photo without people", False),
    ("food", "a photo of food", False),
    ("document", "a photo of a text document or receipt", False),
]


def status() -> dict:
    ok = False
    detail = "not loaded"
    try:
        import transformers  # noqa: F401

        ok = True
        detail = f"transformers CLIP ({settings.clip_tokoh_model})"
    except Exception as exc:
        detail = f"unavailable: {exc}"
    return {
        "name": "CLIP-tokoh",
        "configured": bool(settings.clip_tokoh_enabled),
        "available": ok,
        "detail": detail,
        "model": settings.clip_tokoh_model,
        "threshold": settings.clip_tokoh_threshold,
    }


def reset_model() -> None:
    global _model, _processor, _model_id
    _model = None
    _processor = None
    _model_id = None


def _device() -> str:
    if settings.ocr_gpu:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
    return "cpu"


def _get_pipeline():
    global _model, _processor, _model_id
    if not settings.clip_tokoh_enabled:
        return None, None
    mid = settings.clip_tokoh_model
    if _model is not None and _model_id == mid:
        return _model, _processor
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except Exception as exc:
        log.debug("CLIP skip — transformers missing: %s", exc)
        return None, None
    try:
        log.info("Loading CLIP tokoh model=%s device=%s", mid, _device())
        _processor = CLIPProcessor.from_pretrained(mid)
        _model = CLIPModel.from_pretrained(mid)
        _model.to(_device())
        _model.eval()
        _model_id = mid
        return _model, _processor
    except Exception as exc:
        log.warning("CLIP load failed: %s", exc)
        reset_model()
        return None, None


def score_image_prompts(path: Path, prompts: list[str]) -> list[float] | None:
    """Return raw image/text logits while reusing the existing CLIP instance.

    The shared scorer lets the new content classifier add prompts without
    loading a second copy of CLIP into the 6 GB GPU.  ``None`` preserves the
    existing best-effort/no-op behavior when the model is unavailable.
    """
    if not prompts:
        return []
    model, processor = _get_pipeline()
    if model is None or processor is None:
        return None
    try:
        import torch
        from PIL import Image

        with Image.open(path) as im:
            image = im.convert("RGB")
            image.thumbnail((384, 384))
        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        device = _device()
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            output = model(**inputs)
        logits = getattr(output, "logits_per_image", None)
        if logits is None:
            return None
        return [float(value) for value in logits[0].detach().float().cpu().tolist()]
    except Exception as exc:
        log.debug("CLIP prompt scoring skip %s: %s", path.name, exc)
        return None


def analyze_image_tokoh(path: Path) -> list[dict]:
    """Zero-shot: foto tokoh/presiden vs konten lain."""
    if not settings.clip_tokoh_enabled:
        return []
    logits = score_image_prompts(path, [prompt for _, prompt, _ in _LABELS])
    if logits is None or not logits:
        return []
    peak = max(logits)
    weights = [math.exp(value - peak) for value in logits]
    denominator = sum(weights) or 1.0
    probs = [value / denominator for value in weights]

    scored = list(zip(_LABELS, probs, strict=True))
    best_hit = max((x for x in scored if x[0][2]), key=lambda x: x[1], default=None)
    best_neg = max((x for x in scored if not x[0][2]), key=lambda x: x[1], default=None)
    if not best_hit:
        return []
    (hid, hprompt, _), hscore = best_hit
    nscore = best_neg[1] if best_neg else 0.0
    if hscore < float(settings.clip_tokoh_threshold):
        return []
    if hscore - nscore < float(settings.clip_tokoh_margin):
        return []

    label_map = {
        "presiden_ri": "Tokoh: indikasi foto Presiden RI",
        "jokowi": "Tokoh: indikasi foto Jokowi",
        "prabowo": "Tokoh: indikasi foto Prabowo",
        "political_portrait": "Tokoh: potret pejabat/politisi",
        "campaign_poster": "Tokoh: poster kampanye politis",
    }
    return [
        {
            "category": "konten_visual",
            "label": label_map.get(hid, f"Tokoh: {hid}"),
            "confidence": round(min(0.93, 0.55 + hscore), 3),
            "layer_origin": Layer.L3.value,
            "evidence": (
                f"[clip:{settings.clip_tokoh_model.split('/')[-1]}] "
                f"{path.name} | {hid} p={hscore:.3f} (neg={nscore:.3f}) | {hprompt}"
            )[:320],
        }
    ]
