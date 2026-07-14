"""Audio moderation — Whisper ASR → risk lexicon."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)
_model = None


def status() -> dict:
    ok = False
    detail = "not loaded"
    try:
        import whisper  # noqa: F401

        ok = True
        detail = f"openai-whisper ({settings.gpu_whisper_model})"
    except Exception as exc:
        detail = f"unavailable: {exc}"
    return {
        "name": "Whisper",
        "configured": bool(settings.gpu_whisper_enabled),
        "available": ok and bool(shutil.which("ffmpeg")),
        "detail": detail,
        "model": settings.gpu_whisper_model,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def _get_model():
    global _model
    if _model is None:
        import whisper

        device = "cuda" if settings.ocr_gpu else "cpu"
        try:
            import torch

            if not torch.cuda.is_available():
                device = "cpu"
        except Exception:
            device = "cpu"
        _model = whisper.load_model(settings.gpu_whisper_model, device=device)
    return _model


def _extract_audio(video_path: Path, wav_path: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(wav_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        return r.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 0
    except Exception as exc:
        log.warning("ffmpeg audio extract failed: %s", exc)
        return False


def transcribe(path: Path) -> str:
    if not settings.gpu_whisper_enabled or not status()["available"]:
        return ""
    tmp: Path | None = None
    audio = path
    try:
        if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4a"}:
            tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
            if not _extract_audio(path, tmp):
                return ""
            audio = tmp
        model = _get_model()
        result = model.transcribe(str(audio), language=settings.gpu_whisper_lang or None)
        return str(result.get("text") or "").strip()
    except Exception as exc:
        log.warning("Whisper failed on %s: %s", path.name, exc)
        return ""
    finally:
        if tmp:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def moderate(path: Path) -> list[ModerationHit]:
    if not settings.gpu_stack_enabled or not settings.gpu_whisper_enabled:
        return []
    text = transcribe(path)
    if not text:
        return []
    norm = re.sub(r"\s+", " ", text.lower())
    hits: list[ModerationHit] = []
    for kw in settings.risk_keywords:
        if kw.lower() in norm:
            hits.append(
                ModerationHit(
                    category="konten_audio",
                    label=f"Audio/lirik indikasi: {kw}",
                    confidence=0.82,
                    layer_origin=Layer.L4.value,
                    evidence=text[:280],
                    backend="whisper",
                )
            )
    # Always keep a transcript cue when stack debugging? No — only keyword hits.
    return hits
