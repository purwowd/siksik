"""Hash-cache helpers — keyed by content hash + enrichment engine fingerprint."""

from __future__ import annotations

import json
import shutil
from contextvars import ContextVar
from hashlib import sha256

from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode

# Mode sesi aktif (QUICK/FULL) — dipakai media_text OCR policy
_analysis_mode: ContextVar[AcquisitionMode | None] = ContextVar("sadt_analysis_mode", default=None)


def set_analysis_mode(mode: AcquisitionMode | None):
    return _analysis_mode.set(mode)


def reset_analysis_mode(token) -> None:
    _analysis_mode.reset(token)


def get_analysis_mode() -> AcquisitionMode | None:
    return _analysis_mode.get()


def _config_digest(value: str | None) -> str:
    """Fingerprint config values without persisting machine-specific paths."""
    normalized = (value or "").strip()
    return sha256(normalized.encode("utf-8")).hexdigest()[:12] if normalized else "none"


def ocr_stage_fingerprint() -> str:
    """Stable OCR-only fingerprint so Qwen/policy changes reuse extracted text."""
    return "|".join(
        [
            "ocr-stage-v1",
            f"enabled={int(bool(settings.ocr_enabled or settings.media_text_enabled))}",
            f"backend={settings.ocr_backend}",
            f"langs={settings.ocr_langs}",
            f"gpu={int(bool(settings.ocr_gpu))}",
            f"max={settings.ocr_max_edge_px}",
            f"min={settings.ocr_min_edge_px}",
            f"sharpen={int(bool(settings.ocr_sharpen))}",
            f"paragraph={int(bool(settings.ocr_paragraph))}",
            f"confidence={settings.ocr_min_confidence}",
            f"mag={settings.ocr_mag_ratio}",
        ]
    )


def _stage_storage_key(content_sha256: str, stage: str) -> str:
    return sha256(f"siksik-stage\0{stage}\0{content_sha256}".encode("utf-8")).hexdigest()


async def get_stage_cached(
    content_sha256: str,
    *,
    stage: str,
    fingerprint: str,
) -> dict | None:
    if not content_sha256:
        return None
    key = _stage_storage_key(content_sha256, stage)
    row = await db.fetchone("SELECT result_json FROM hash_cache WHERE sha256 = ?", (key,))
    if not row:
        return None
    try:
        payload = json.loads(row["result_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("_stage") != stage or payload.get("_fingerprint") != fingerprint:
        return None
    value = payload.get("value")
    return value if isinstance(value, dict) else None


async def set_stage_cached(
    content_sha256: str,
    *,
    stage: str,
    fingerprint: str,
    value: dict,
) -> None:
    if not content_sha256:
        return
    key = _stage_storage_key(content_sha256, stage)
    payload = {
        "_stage": stage,
        "_fingerprint": fingerprint,
        "value": value,
    }
    await db.execute(
        """
        INSERT INTO hash_cache (sha256, result_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET
            result_json=excluded.result_json,
            updated_at=excluded.updated_at
        """,
        (key, json.dumps(payload), utcnow()),
    )


def engine_fingerprint() -> str:
    """Bump semantics when enrichment knobs change so stale lean results miss."""
    from app.services.content_policy import CONTENT_FUSION_REVISION, CONTENT_POLICY_REVISION
    from app.services.content_text import CONTENT_TEXT_REVISION
    from app.services.content_visual import CONTENT_VISUAL_REVISION
    from app.services.document_text import DOCUMENT_TEXT_REVISION
    from app.services.gpu_stack.reason_qwen import (
        QWEN_DECODER_REVISION,
        QWEN_INPUT_REVISION,
        QWEN_PARSER_REVISION,
        QWEN_PROMPT_REVISION,
    )

    mode = get_analysis_mode()
    return "|".join(
        [
            "v18",  # explicit visual fast path + focused Qwen adjudication
            f"mode={mode.value if mode else 'none'}",
            f"ocr={int(bool(settings.ocr_enabled))}",
            f"mt={int(bool(settings.media_text_enabled))}",
            (
                f"wh={int(bool(settings.gpu_whisper_enabled))}:"
                f"{settings.gpu_whisper_model}:"
                f"{settings.gpu_whisper_lang or 'auto'}"
            ),
            f"wh1st={settings.video_whisper_transcribe_first_s}",
            f"stack={int(bool(settings.gpu_stack_enabled))}",
            f"ob={settings.ocr_backend}",
            f"ol={settings.ocr_langs}",
            f"full_gal={int(bool(settings.ocr_full_gallery))}",
            f"ocr_px={settings.ocr_max_edge_px}",
            f"ocr_min={settings.ocr_min_edge_px}",
            f"ocr_sh={int(bool(settings.ocr_sharpen))}",
            f"ocr_para={int(bool(settings.ocr_paragraph))}",
            f"ocr_mag={settings.ocr_mag_ratio}",
            f"vwh={settings.video_whisper_max_duration_s}",
            f"vkf={settings.video_overlay_keyframes}",
            (
                f"clip={int(bool(settings.clip_tokoh_enabled))}:"
                f"{settings.clip_tokoh_model.split('/')[-1]}:"
                f"{settings.clip_tokoh_threshold}:{settings.clip_tokoh_margin}"
            ),
            (
                f"content={int(bool(settings.content_detection_enabled))}:"
                f"{CONTENT_POLICY_REVISION}:{CONTENT_FUSION_REVISION}"
            ),
            (
                f"content_visual={int(bool(settings.content_visual_enabled))}:"
                f"{_config_digest(settings.content_visual_model)}:"
                f"{settings.content_visual_threshold}:"
                f"{settings.content_visual_threshold_lgbt}:"
                f"{settings.content_visual_threshold_political_meme}:"
                f"{settings.content_visual_threshold_political_campaign}:"
                f"{settings.content_visual_threshold_demonstration}:"
                f"{settings.content_visual_threshold_extremism}:"
                f"{settings.content_visual_min_share}:"
                f"{settings.content_visual_max_candidates}:"
                f"{int(bool(settings.content_visual_require_confirmation))}:"
                f"{int(bool(settings.content_visual_fast_path_enabled))}:"
                f"{settings.content_visual_strong_threshold}:"
                f"{settings.content_visual_strong_min_share}:"
                f"{settings.content_visual_flag_stripe_threshold}:"
                f"{settings.content_visual_fast_demonstration_threshold}:"
                f"{settings.content_visual_fast_manipulated_meme_threshold}:"
                f"{settings.content_visual_fast_satire_meme_threshold}:"
                f"{int(bool(settings.content_visual_skip_text_heavy_without_signal))}:"
                f"{CONTENT_VISUAL_REVISION}"
            ),
            (
                f"content_text={_config_digest(settings.content_text_model)}:"
                f"{settings.content_text_threshold}:"
                f"{settings.content_text_device}:{CONTENT_TEXT_REVISION}"
            ),
            f"content_local={int(bool(settings.content_models_local_only))}",
            f"content_qwen_json={int(bool(settings.content_qwen_structured))}",
            (
                f"qwen={int(bool(settings.gpu_qwen_enabled))}:"
                f"{_config_digest(settings.gpu_qwen_model)}:"
                f"{_config_digest(settings.gpu_qwen_plugin)}"
            ),
            f"qwen_px={settings.gpu_qwen_max_edge_px}",
            f"qwen_tokens={settings.gpu_qwen_image_max_new_tokens}:{settings.gpu_qwen_text_max_new_tokens}",
            f"qwen_video_frames={settings.gpu_qwen_video_max_frames}",
            f"bridge={int(bool(settings.gpu_bridge_fallbacks_enabled))}",
            f"qwen_input={QWEN_INPUT_REVISION}",
            f"qwen_decoder={QWEN_DECODER_REVISION}",
            f"qwen_parser={QWEN_PARSER_REVISION}",
            f"qwen_prompt={QWEN_PROMPT_REVISION}",
            f"meme={len(settings.meme_hate_keywords)}",
            (
                f"document={DOCUMENT_TEXT_REVISION}:"
                f"{settings.document_extract_max_chars}:"
                f"{settings.document_extract_max_bytes}"
            ),
            (
                f"nudity={int(bool(settings.nudity_detection_enabled))}:"
                f"{settings.nudity_onnx_device}:"
                f"{settings.nudity_threshold_anus}:"
                f"{settings.nudity_threshold_buttocks}:"
                f"{settings.nudity_threshold_female_breast}:"
                f"{settings.nudity_threshold_female_genitalia}:"
                f"{settings.nudity_threshold_male_genitalia}"
            ),
            (
                f"nudity_video={settings.nudity_video_frames_quick}:"
                f"{settings.nudity_video_frames_full}:"
                f"{settings.nudity_video_min_positive_frames}:"
                f"{settings.nudity_frame_max_edge_px}"
            ),
            (
                "nudity_codec="
                f"{int(shutil.which('ffmpeg') is not None)}:"
                f"{int(shutil.which('ffprobe') is not None)}"
            ),
        ]
    )


async def get_cached(sha256: str) -> list[dict] | None:
    row = await db.fetchone("SELECT result_json FROM hash_cache WHERE sha256 = ?", (sha256,))
    if not row:
        return None
    try:
        data = json.loads(row["result_json"])
    except json.JSONDecodeError:
        return None
    # Legacy bare list → treat as miss (forces re-enrichment after engine upgrades)
    if isinstance(data, list):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("_engine") != engine_fingerprint():
        return None
    findings = data.get("findings")
    return findings if isinstance(findings, list) else None


async def set_cached(sha256: str, results: list[dict]) -> None:
    payload = {"_engine": engine_fingerprint(), "findings": results}
    await db.execute(
        """
        INSERT INTO hash_cache (sha256, result_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET
            result_json=excluded.result_json,
            updated_at=excluded.updated_at
        """,
        (sha256, json.dumps(payload), utcnow()),
    )


async def clear_hash_cache() -> int:
    row = await db.fetchone("SELECT COUNT(*) AS c FROM hash_cache")
    await db.execute("DELETE FROM hash_cache")
    return int(row["c"]) if row else 0
