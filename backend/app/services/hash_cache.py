"""Hash-cache helpers — keyed by content hash + enrichment engine fingerprint."""

from __future__ import annotations

import json
from contextvars import ContextVar

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


def engine_fingerprint() -> str:
    """Bump semantics when enrichment knobs change so stale lean results miss."""
    return "|".join(
        [
            "v12",  # consolidate OCR/meme per image (no duplicate rows)
            f"ocr={int(bool(settings.ocr_enabled))}",
            f"mt={int(bool(settings.media_text_enabled))}",
            f"wh={int(bool(settings.gpu_whisper_enabled))}:{settings.gpu_whisper_model}:{settings.gpu_whisper_lang or 'auto'}",
            f"wh1st={settings.video_whisper_transcribe_first_s}",
            f"stack={int(bool(settings.gpu_stack_enabled))}",
            f"ob={settings.ocr_backend}",
            f"full_gal={int(bool(settings.ocr_full_gallery))}",
            f"ocr_px={settings.ocr_max_edge_px}",
            f"ocr_min={settings.ocr_min_edge_px}",
            f"ocr_sh={int(bool(settings.ocr_sharpen))}",
            f"ocr_para={int(bool(settings.ocr_paragraph))}",
            f"ocr_mag={settings.ocr_mag_ratio}",
            f"vwh={settings.video_whisper_max_duration_s}",
            f"vkf={settings.video_overlay_keyframes}",
            f"clip={int(bool(settings.clip_tokoh_enabled))}:{settings.clip_tokoh_model.split('/')[-1]}",
            f"meme={len(settings.meme_hate_keywords)}",
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
