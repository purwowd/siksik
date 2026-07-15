"""General multimodal reasoning — Qwen2.5-VL-7B.

Load via SADT_GPU_QWEN_MODEL (HF id or local path), e.g. Qwen/Qwen2.5-VL-7B-Instruct.
Optional: SADT_GPU_QWEN_PLUGIN for custom moderate(path).
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
_model = None
_processor = None
_load_failed = False


def status() -> dict:
    configured = bool(settings.gpu_qwen_enabled)
    available = False
    detail = "not loaded"
    plugin = (settings.gpu_qwen_plugin or "").strip()
    if plugin:
        available = True
        detail = f"plugin={plugin}"
    else:
        try:
            import transformers  # noqa: F401

            if settings.gpu_qwen_model:
                available = True
                detail = f"transformers ready ({settings.gpu_qwen_model})"
            else:
                detail = "SADT_GPU_QWEN_MODEL empty"
        except Exception as exc:
            detail = f"transformers unavailable: {exc}"
    return {
        "name": "Qwen2.5-VL-7B",
        "configured": configured,
        "available": available and (bool(plugin) or bool(settings.gpu_qwen_model)),
        "model": settings.gpu_qwen_model,
        "plugin": plugin,
        "detail": detail,
    }


def _try_load():
    global _model, _processor, _load_failed
    if _model is not None:
        return _model, _processor
    if _load_failed or not settings.gpu_qwen_model:
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
        _load_failed = True
        _model = None
        return None, None


def _hits_from_text(text: str, *, layer: str, backend: str) -> list[ModerationHit]:
    if not text:
        return []
    # Jawab AMAN di awal → tidak emit hit kecuali ada keyword jelas
    head = text.strip().lower()[:48]
    kws = match_keywords(text)
    if head.startswith("aman") and not kws:
        return []
    if not kws:
        return []
    return [
        ModerationHit(
            category=category_for_keyword(kw),
            label=f"VL reasoning: {kw}",
            confidence=0.8,
            layer_origin=layer,
            evidence=text[:280],
            backend=backend,
        )
        for kw in kws[:3]
    ]


def moderate_image(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_qwen_enabled:
        return []

    model_dir = settings.gpu_qwen_model or None
    if model_dir:
        p = Path(model_dir)
        if p.is_dir():
            pass
        elif p.is_file():
            model_dir = str(p.parent)
        else:
            model_dir = None

    plugin_hits = run_plugin(
        path,
        plugin=settings.gpu_qwen_plugin or None,
        model_dir=model_dir,
        default_layer=Layer.L3.value,
        default_backend="qwen2.5-vl",
    )
    if plugin_hits is not None:
        return plugin_hits

    model, processor = _try_load()
    if not model or not processor:
        return []
    try:
        from PIL import Image
        import torch

        prompt = (
            "You are a forensic media moderator for Indonesian risk policy "
            "(provokasi, makar, radikal, narkoba, senjata, judi, pornografi anak). "
            "Describe unsafe signals in this image in one short Indonesian sentence, "
            "or say AMAN if safe."
        )
        image = Image.open(path).convert("RGB")

        # Prefer Qwen VL chat helpers when installed
        try:
            from qwen_vl_utils import process_vision_info

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": str(path)},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
        except Exception:
            text = f"<image>\n{prompt}"
            inputs = processor(text=[text], images=[image], return_tensors="pt")

        inputs = {k: v.to(model.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=96)
        answer = processor.batch_decode(out, skip_special_tokens=True)[0]
        return _hits_from_text(answer, layer=Layer.L3.value, backend="qwen2.5-vl")
    except Exception as exc:
        log.warning("Qwen VL image failed: %s", exc)
        return []


def moderate_video_summary(path: Path, prior_hits: list[ModerationHit]) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_qwen_enabled:
        return []
    if not prior_hits:
        return []
    blob = " ".join(h.evidence for h in prior_hits)
    return _hits_from_text(blob, layer=Layer.L4.value, backend="qwen-synth")
