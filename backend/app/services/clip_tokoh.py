"""CLIP zero-shot — indikasi foto tokoh / pejabat (presiden).

Butuh: pip install transformers
Model default: openai/clip-vit-base-patch32 (lazy download sekali).
Tanpa transformers / gagal load → no-op (OCR tetap jadi jalur utama).
"""

from __future__ import annotations

import logging
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


def analyze_image_tokoh(path: Path) -> list[dict]:
    """Zero-shot: foto tokoh/presiden vs konten lain."""
    if not settings.clip_tokoh_enabled:
        return []
    model, processor = _get_pipeline()
    if model is None or processor is None:
        return []
    try:
        import torch
        from PIL import Image
    except Exception:
        return []

    try:
        with Image.open(path) as im:
            image = im.convert("RGB")
            image.thumbnail((384, 384))
        texts = [prompt for _, prompt, _ in _LABELS]
        inputs = processor(text=texts, images=image, return_tensors="pt", padding=True)
        device = _device()
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            out = model(**inputs)
            probs = out.logits_per_image.softmax(dim=-1)[0].tolist()
    except Exception as exc:
        log.debug("CLIP infer skip %s: %s", path.name, exc)
        return []

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
