"""Optional fine-tuned multi-label Indonesian text classifier adapter."""

from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.models.schemas import Layer
from app.services.content_policy import CONTENT_CATEGORY_LABELS, normalize_content_category

log = logging.getLogger(__name__)

CONTENT_TEXT_REVISION = "hf-multilabel-v1"
_model = None
_tokenizer = None
_model_id: str | None = None
_label_map: dict[int, str] = {}
_load_failed = False
_lock = threading.RLock()


def _device() -> str:
    requested = (settings.content_text_device or "cpu").strip().casefold()
    if requested in {"cpu", "cuda", "mps"}:
        return requested
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
    global _model, _tokenizer, _model_id, _label_map, _load_failed
    _model = None
    _tokenizer = None
    _model_id = None
    _label_map = {}
    _load_failed = False


def _try_load():
    global _model, _tokenizer, _model_id, _label_map, _load_failed
    model_id = (settings.content_text_model or "").strip()
    if not model_id or _load_failed:
        return None, None
    if _model is not None and _model_id == model_id:
        return _model, _tokenizer
    with _lock:
        if _model is not None and _model_id == model_id:
            return _model, _tokenizer
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            kwargs = {
                "local_files_only": bool(settings.content_models_local_only),
                "trust_remote_code": False,
            }
            tokenizer = AutoTokenizer.from_pretrained(model_id, **kwargs)
            model = AutoModelForSequenceClassification.from_pretrained(model_id, **kwargs)
            raw_map = getattr(model.config, "id2label", {}) or {}
            label_map = {
                int(index): category
                for index, label in raw_map.items()
                if (category := normalize_content_category(str(label))) is not None
            }
            if not label_map:
                raise ValueError(
                    "checkpoint id2label does not contain SIKSIK content taxonomy labels"
                )
            model.to(_device())
            model.eval()
            _model = model
            _tokenizer = tokenizer
            _model_id = model_id
            _label_map = label_map
            return _model, _tokenizer
        except Exception as exc:
            log.warning("Content text model unavailable (%s): %s", model_id, exc)
            _load_failed = True
            return None, None


def analyze_text(text: str, *, layer: str = Layer.L2.value) -> list[dict]:
    value = " ".join((text or "").replace("\x00", " ").split())[:8000]
    if not value or not settings.content_detection_enabled or not settings.content_text_model:
        return []
    model, tokenizer = _try_load()
    if model is None or tokenizer is None:
        return []
    try:
        import torch

        encoded = tokenizer(
            value,
            truncation=True,
            max_length=384,
            padding=False,
            return_tensors="pt",
        )
        encoded = {key: tensor.to(_device()) for key, tensor in encoded.items()}
        with _lock, torch.no_grad():
            logits = model(**encoded).logits[0]
        probabilities = torch.sigmoid(logits).detach().float().cpu().tolist()
    except Exception as exc:
        log.warning("Content text inference failed: %s", exc)
        return []

    threshold = float(settings.content_text_threshold)
    backend = (settings.content_text_model or "content-text").split("/")[-1]
    output: list[dict] = []
    for index, category in _label_map.items():
        if index >= len(probabilities):
            continue
        confidence = float(probabilities[index])
        if confidence < threshold:
            continue
        output.append(
            {
                "category": category,
                "label": CONTENT_CATEGORY_LABELS[category],
                "confidence": round(min(0.99, confidence), 3),
                "layer_origin": layer,
                "evidence": f"[text-ai:{backend}] {value[:280]}"[:320],
            }
        )
    return output


def status() -> dict:
    return {
        "name": "content-text",
        "configured": bool(settings.content_text_model),
        "model": settings.content_text_model or None,
        "device": _device(),
        "threshold": float(settings.content_text_threshold),
        "local_files_only": bool(settings.content_models_local_only),
        "revision": CONTENT_TEXT_REVISION,
    }
