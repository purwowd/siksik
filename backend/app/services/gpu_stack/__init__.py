"""GPU moderation stack — orchestrator & backend loaders.

Stack (server NVIDIA):
  Video  → SafeWatch
  Image  → ICM-Assistant
  Reason → Qwen2.5-VL-7B
  Audio  → Whisper
  OCR    → PaddleOCR

Enable:
  python run.py --reload --host 127.0.0.1 --port 8000 --gpu
  # atau SADT_GPU_STACK_ENABLED=1
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit, StackStatus

log = logging.getLogger(__name__)


def _cuda_name() -> str | None:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        return None
    return None


@lru_cache(maxsize=1)
def stack_enabled() -> bool:
    return bool(settings.gpu_stack_enabled)


def clear_stack_cache() -> None:
    stack_enabled.cache_clear()
    get_stack_status.cache_clear()


@lru_cache(maxsize=1)
def get_stack_status() -> StackStatus:
    from app.services.gpu_stack import audio_whisper, image_icm, ocr_paddle, reason_qwen, video_safewatch

    backends = {
        "video": video_safewatch.status(),
        "image": image_icm.status(),
        "reason": reason_qwen.status(),
        "audio": audio_whisper.status(),
        "ocr": ocr_paddle.status(),
    }
    return StackStatus(
        enabled=stack_enabled(),
        device=_cuda_name() or ("cpu" if stack_enabled() else None),
        backends=backends,
    )


def analyze_image_gpu(path: Path) -> list[dict]:
    """ICM-Assistant + PaddleOCR (+ optional Qwen VL synthesis)."""
    if not stack_enabled():
        return []
    from app.services.gpu_stack import image_icm, ocr_paddle, reason_qwen

    hits: list[ModerationHit] = []
    hits.extend(image_icm.moderate(path))
    hits.extend(ocr_paddle.moderate_image(path))
    # reasoning over image if available
    hits.extend(reason_qwen.moderate_image(path))
    return [h.as_finding() for h in hits]


def analyze_video_gpu(path: Path) -> list[dict]:
    """SafeWatch + Whisper audio + OCR/ICM on keyframes + optional Qwen."""
    if not stack_enabled():
        return []
    from app.services.gpu_stack import audio_whisper, image_icm, ocr_paddle, reason_qwen, video_safewatch
    from app.services.vision import extract_video_keyframes

    hits: list[ModerationHit] = []
    hits.extend(video_safewatch.moderate(path))
    hits.extend(audio_whisper.moderate(path))

    frames = extract_video_keyframes(path, max_frames=settings.gpu_video_keyframes)
    try:
        for fr in frames:
            for h in image_icm.moderate(fr):
                h.layer_origin = Layer.L4.value
                h.label = f"Video keyframe ICM: {h.label}"
                hits.append(h)
            for h in ocr_paddle.moderate_image(fr):
                h.layer_origin = Layer.L4.value
                h.label = f"Video keyframe OCR: {h.label}"
                hits.append(h)
            for h in reason_qwen.moderate_image(fr):
                h.layer_origin = Layer.L4.value
                h.label = f"Video keyframe VL: {h.label}"
                hits.append(h)
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

    hits.extend(reason_qwen.moderate_video_summary(path, hits))
    return [h.as_finding() for h in hits]
