"""Audio moderation — Whisper ASR → risk lexicon."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)
_model = None
_model_key: str | None = None

# Prompt netral — jangan tanam keyword risiko (Whisper sering meniru prompt → FP)
_ID_PROMPT = "Berikut adalah ucapan atau lirik lagu dalam bahasa Indonesia."


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
        "lang": settings.gpu_whisper_lang or "auto",
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def reset_model() -> None:
    """Drop cached Whisper — panggil setelah ganti SADT_GPU_WHISPER_MODEL."""
    global _model, _model_key
    _model = None
    _model_key = None


def _get_model():
    global _model, _model_key
    device = "cuda" if settings.ocr_gpu else "cpu"
    try:
        import torch

        if not torch.cuda.is_available():
            device = "cpu"
    except Exception:
        device = "cpu"
    key = f"{settings.gpu_whisper_model}:{device}"
    if _model is None or _model_key != key:
        import whisper

        log.info("Loading Whisper model=%s device=%s", settings.gpu_whisper_model, device)
        _model = whisper.load_model(settings.gpu_whisper_model, device=device)
        _model_key = key
    return _model


def _extract_audio(video_path: Path, wav_path: Path, *, first_s: int = 0) -> bool:
    """Extract mono 16 kHz WAV; optional only first N seconds for speed."""
    if not shutil.which("ffmpeg"):
        return False
    cmd = ["ffmpeg", "-y"]
    if first_s > 0:
        cmd += ["-t", str(first_s)]
    cmd += [
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-af",
        "highpass=f=80,lowpass=f=8000,volume=1.5",
        str(wav_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
        return r.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 0
    except Exception as exc:
        log.warning("ffmpeg audio extract failed: %s", exc)
        return False


def _transcribe_kwargs() -> dict:
    lang = (settings.gpu_whisper_lang or "").strip() or None
    opts: dict = {
        "language": lang,
        "task": "transcribe",
        "temperature": 0.0,
        "best_of": 1,
        "beam_size": 3 if settings.gpu_whisper_model not in {"tiny", "tiny.en"} else 1,
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.55,
        "compression_ratio_threshold": 2.4,
        "fp16": False,
    }
    if lang in (None, "id", "ms"):
        opts["initial_prompt"] = _ID_PROMPT
    return opts


def transcribe(path: Path) -> str:
    if not settings.gpu_whisper_enabled:
        return ""
    if not status()["available"]:
        return ""
    max_d = int(settings.video_whisper_max_duration_s or 0)
    first_s = int(settings.video_whisper_transcribe_first_s or 0)
    if max_d > 0 and path.suffix.lower() in {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".3gp",
        ".webm",
        ".m4a",
    }:
        from app.services.vision import video_duration_s

        dur = video_duration_s(path)
        if dur is not None and dur > max_d:
            log.info("Whisper skip %s (%.0fs > cap %ds)", path.name, dur, max_d)
            return ""
    tmp: Path | None = None
    audio = path
    try:
        if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4a"}:
            tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
            if not _extract_audio(path, tmp, first_s=first_s):
                return ""
            audio = tmp
            if first_s > 0:
                log.info("Whisper ASR first %ds of %s", first_s, path.name)
        model = _get_model()
        result = model.transcribe(str(audio), **_transcribe_kwargs())
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


def moderate(path: Path, *, force: bool = False) -> list[ModerationHit]:
    allow = force or (
        (settings.gpu_stack_enabled or settings.media_text_enabled)
        and settings.gpu_whisper_enabled
    )
    if not allow:
        return []
    text = transcribe(path)
    if not text:
        return []
    from app.services.lexicon import match_keywords, video_keyword_corpus

    hits: list[ModerationHit] = []
    for matched in match_keywords(text, video_keyword_corpus()):
        hits.append(
            ModerationHit(
                category="konten_audio",
                label=f"Audio/lirik indikasi: {matched}",
                confidence=0.82,
                layer_origin=Layer.L4.value,
                evidence=text[:280],
                backend="whisper",
            )
        )
    return hits
