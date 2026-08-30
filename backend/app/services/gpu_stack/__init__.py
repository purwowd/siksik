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


def analyze_image_gpu(
    path: Path,
    *,
    include_ocr: bool = True,
    include_reasoning: bool = True,
) -> list[dict]:
    """ICM-Assistant + PaddleOCR (+ optional Qwen VL synthesis)."""
    if not stack_enabled():
        return []
    from app.services.gpu_stack import image_icm, ocr_paddle, reason_qwen

    hits: list[ModerationHit] = []
    hits.extend(image_icm.moderate(path))
    if include_ocr:
        hits.extend(ocr_paddle.moderate_image(path))
    if include_reasoning:
        hits.extend(reason_qwen.moderate_image(path))
    return [h.as_finding() for h in hits]


def analyze_image_reasoning(path: Path) -> list[dict]:
    """Run only visual reasoning; never construct a second OCR backend."""
    if not stack_enabled():
        return []
    from app.services.gpu_stack import reason_qwen

    return [hit.as_finding() for hit in reason_qwen.moderate_image(path)]


def analyze_video_gpu(path: Path, *, frames: list[Path] | None = None) -> list[dict]:
    """Analyze one shared keyframe set and escalate only suspicious frames."""
    if not stack_enabled():
        return []
    from app.services import content_policy, content_visual, media_text
    from app.services.gpu_stack import audio_whisper, image_icm, ocr_paddle, reason_qwen, video_safewatch
    from app.services.vision import extract_video_keyframes

    findings: list[dict] = [
        hit.as_finding()
        for hit in (
            list(video_safewatch.moderate(path))
            + list(audio_whisper.moderate(path))
        )
    ]

    owns_frames = frames is None
    frame_values = (
        extract_video_keyframes(path, max_frames=settings.gpu_video_keyframes)
        if frames is None
        else list(frames)
    )
    try:
        reasoning_used = 0
        ocr_used = 0
        for fr in frame_values:
            visual_candidates = content_visual.analyze_image(fr)
            frame_support: list[dict] = []

            if (
                ocr_used < int(settings.video_ocr_max_frames)
                and media_text.looks_like_text_heavy_image(fr)
            ):
                for hit in ocr_paddle.moderate_image(fr):
                    finding = hit.as_finding()
                    finding["layer_origin"] = Layer.L4.value
                    finding["label"] = f"Video keyframe OCR: {finding['label']}"
                    frame_support.append(finding)
                ocr_used += 1

            for hit in image_icm.moderate(fr):
                finding = hit.as_finding()
                finding["layer_origin"] = Layer.L4.value
                finding["label"] = f"Video keyframe ICM: {finding['label']}"
                frame_support.append(finding)

            ambiguous_visual = content_policy.visual_candidates_requiring_reasoning(
                visual_candidates
            )
            decision = None
            if (
                settings.gpu_qwen_enabled
                and ambiguous_visual
                and reasoning_used < int(settings.gpu_qwen_video_max_frames)
            ):
                context = " ".join(
                    str(item.get("evidence") or "") for item in frame_support
                )[:3000]
                decision = reason_qwen.moderate_image_decision(
                    fr,
                    context_text=context,
                    candidate_categories=[
                        str(item.get("category") or "")
                        for item in ambiguous_visual
                    ],
                )
                reasoning_used += 1
                for hit in decision.hits:
                    finding = hit.as_finding()
                    finding["layer_origin"] = Layer.L4.value
                    finding["label"] = f"Video keyframe VL: {finding['label']}"
                    frame_support.append(finding)

            promoted = content_policy.confirm_visual_candidates(
                visual_candidates,
                frame_support,
                reasoning_verdict=(
                    decision.verdict if decision is not None else "unavailable"
                ),
            )
            for finding in promoted:
                finding["layer_origin"] = Layer.L4.value
                finding["label"] = f"Video keyframe: {finding['label']}"
            findings.extend(frame_support)
            findings.extend(promoted)
    finally:
        if owns_frames:
            for fr in frame_values:
                try:
                    fr.unlink(missing_ok=True)
                except OSError:
                    pass
            if frame_values:
                try:
                    frame_values[0].parent.rmdir()
                except OSError:
                    pass

    return content_policy.merge_content_findings(findings)
