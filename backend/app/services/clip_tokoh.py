"""CLIP zero-shot — indikasi foto tokoh / pejabat (presiden).

Butuh: pip install transformers
Model default: openai/clip-vit-base-patch32 (lazy download sekali).
Tanpa transformers / gagal load → no-op (OCR tetap jadi jalur utama).
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer

log = logging.getLogger(__name__)

_model = None
_processor = None
_model_id: str | None = None
_MODEL_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()
_TEXT_FEATURE_CACHE: dict[tuple[str, str, tuple[str, ...]], object] = {}

# (id, prompt Inggris CLIP, apakah “hit” tokoh/pejabat)
_LABELS: list[tuple[str, str, bool]] = [
    ("presiden_ri", "an official presidential portrait photograph of the Indonesian President with national symbols", True),
    ("jokowi", "a close-up portrait photograph of former president Joko Widodo Jokowi", True),
    ("prabowo", "a close-up portrait photograph of Indonesian president Prabowo Subianto", True),
    ("political_portrait", "a formal political campaign portrait of an Indonesian politician with political party logo", True),
    ("campaign_poster", "an Indonesian election campaign poster with political candidate ballot numbers", True),
    ("civil_servant_uniform", "a photo of an Indonesian civil servant ASN PNS in formal office uniform", False),
    ("ordinary_person_batik", "a photo of an ordinary Indonesian person wearing batik shirt or casual clothes", False),
    ("ordinary_selfie", "a casual selfie of an ordinary person", False),
    ("family_photo", "a family photo of ordinary people", False),
    ("office_staff", "an ordinary corporate office meeting or workplace photo", False),
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
    _TEXT_FEATURE_CACHE.clear()


def _device() -> str:
    if settings.ocr_gpu:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
    return "cpu"


def _pooled_feature_tensor(value):
    """Normalize transformers CLIP feature API variants to one tensor.

    Older releases return a tensor from ``get_*_features`` while newer builds
    may return ``BaseModelOutputWithPooling``. Treating the latter as a tensor
    silently disabled all CLIP findings through the best-effort exception path.
    """
    if hasattr(value, "norm"):
        return value
    pooled = getattr(value, "pooler_output", None)
    if pooled is not None:
        return pooled
    embeds = getattr(value, "image_embeds", None)
    if embeds is None:
        embeds = getattr(value, "text_embeds", None)
    if embeds is not None:
        return embeds
    if isinstance(value, (tuple, list)):
        for item in reversed(value):
            if hasattr(item, "norm"):
                return item
    raise TypeError(f"Unsupported CLIP feature output: {type(value).__name__}")


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
    with _MODEL_LOCK:
        if _model is not None and _model_id == mid:
            return _model, _processor
        try:
            log.info("Loading CLIP tokoh model=%s device=%s", mid, _device())
            load_kwargs = {
                "local_files_only": bool(settings.content_models_local_only),
            }
            _processor = CLIPProcessor.from_pretrained(mid, **load_kwargs)
            _model = CLIPModel.from_pretrained(mid, **load_kwargs)
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
        device = _device()
        from app.services.inference_guard import gpu_inference_slot

        with _INFER_LOCK, gpu_inference_slot(), torch.no_grad():
            if hasattr(model, "get_text_features") and hasattr(model, "get_image_features"):
                cache_key = (
                    str(_model_id or settings.clip_tokoh_model),
                    device,
                    tuple(prompts),
                )
                text_features = _TEXT_FEATURE_CACHE.get(cache_key)
                if text_features is None:
                    text_inputs = processor(
                        text=prompts,
                        return_tensors="pt",
                        padding=True,
                    )
                    text_inputs = {
                        key: value.to(device) for key, value in text_inputs.items()
                    }
                    text_features = _pooled_feature_tensor(
                        model.get_text_features(**text_inputs)
                    )
                    text_features = text_features / text_features.norm(
                        dim=-1,
                        keepdim=True,
                    ).clamp_min(1e-12)
                    # Only a handful of stable prompt banks exist. Bound the
                    # cache so plugin callers cannot grow GPU/CPU memory forever.
                    if len(_TEXT_FEATURE_CACHE) >= 8:
                        _TEXT_FEATURE_CACHE.clear()
                    _TEXT_FEATURE_CACHE[cache_key] = text_features.detach()
                image_inputs = processor(images=image, return_tensors="pt")
                image_inputs = {
                    key: value.to(device) for key, value in image_inputs.items()
                }
                image_features = _pooled_feature_tensor(
                    model.get_image_features(**image_inputs)
                )
                image_features = image_features / image_features.norm(
                    dim=-1,
                    keepdim=True,
                ).clamp_min(1e-12)
                scale = model.logit_scale.exp()
                logits = scale * image_features @ text_features.T
            else:
                inputs = processor(
                    text=prompts,
                    images=image,
                    return_tensors="pt",
                    padding=True,
                )
                inputs = {key: value.to(device) for key, value in inputs.items()}
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
