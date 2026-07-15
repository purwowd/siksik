"""Enrichment teks media — screenshot/chat OCR, foto berteks, video ASR + OCR on-screen.

Aktif otomatis jika backend tersedia (PaddleOCR/EasyOCR/Tesseract/Whisper),
atau paksa lewat SADT_OCR_ENABLED / SADT_GPU_STACK_ENABLED / --gpu.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer

log = logging.getLogger(__name__)

_SCREENSHOT_HINTS = (
    "screenshot",
    "screen_shot",
    "screen-shot",
    "captures",
    "screencap",
    "whatsapp",
    "telegram",
    "chat",
    "line",
    "signal",
    "message",
    "notif",
)

# Aset di folder dokumen / unduhan sering poster berteks pada latar polos
# (edge heuristic gagal) — selalu coba OCR jika media_text on.
_FORCE_OCR_DIR_NAMES = frozenset(
    {
        "documents",
        "document",
        "download",
        "downloads",
        "screenshots",
        "screenshot",
        "dcim",
        "pictures",
        "browser",
        "telegram",
        "whatsapp",
        "messenger",
    }
)


def looks_like_chat_or_screenshot(path: Path) -> bool:
    hay = f"{path.parent.as_posix()} {path.name}".lower().replace("\\", "/")
    return any(h in hay for h in _SCREENSHOT_HINTS)


def looks_like_document_or_download(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return bool(parts & _FORCE_OCR_DIR_NAMES)


def looks_like_text_heavy_image(path: Path) -> bool:
    """Heuristic: screenshot/dokumen, atau edge density tinggi ≈ UI / teks / poster."""
    if looks_like_chat_or_screenshot(path) or looks_like_document_or_download(path):
        return True
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((384, 384))
            edge = ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).mean[0]
            return edge > 22
    except Exception:
        return False


def should_try_ocr(path: Path, *, force: bool = False) -> bool:
    if force:
        return True
    if not settings.media_text_enabled and not settings.gpu_stack_enabled:
        return False
    if settings.gpu_stack_enabled:
        return True
    if looks_like_text_heavy_image(path):
        return True
    # Mode FULL + ocr_full_gallery: OCR semua gambar di gallery / pictures / dcim
    if settings.ocr_full_gallery:
        from app.models.schemas import AcquisitionMode
        from app.services.hash_cache import get_analysis_mode

        mode = get_analysis_mode()
        if mode == AcquisitionMode.FULL:
            parts = {p.lower() for p in path.parts}
            if parts & {"gallery", "pictures", "dcim", "camera", "documents", "download", "downloads"}:
                return True
    return False


def _pick_ocr_backend():
    from app.services import ocr as ocr_mod

    preferred = [settings.ocr_backend, settings.gpu_ocr_backend, "paddleocr", "easyocr", "tesseract"]
    seen: set[str] = set()
    for name in preferred:
        if not name or name in seen or name == "fake":
            continue
        seen.add(name)
        cls = ocr_mod._BACKENDS.get(name)
        if not cls:
            continue
        inst = cls()
        if inst.available():
            return inst
    return None


def ocr_image_best_effort(path: Path, *, force: bool = False) -> list[dict]:
    """OCR foto/screenshot/dokumen → findings (word-boundary lexicon).

    Jika SADT_OCR_ENABLED=1, biarkan path legacy `analyze_image_ocr` menangani
    (kecuali force=True untuk keyframe video).
    """
    from app.services import ocr as ocr_mod

    if not should_try_ocr(path, force=force):
        return []

    # Avoid double OCR when legacy flag already covers the same image
    if settings.ocr_enabled and not force:
        return []

    backend = _pick_ocr_backend()
    if backend is None:
        return []

    from app.services.lexicon import video_keyword_corpus

    result = ocr_mod.run_ocr(path, backend=backend)
    if not result or not result.text:
        return []

    findings = ocr_mod.ocr_findings_from_text(
        result.text,
        backend=result.backend,
        keywords=video_keyword_corpus() if force else None,
    )
    if looks_like_chat_or_screenshot(path):
        for f in findings:
            f["label"] = f["label"].replace("OCR:", "OCR chat/screenshot:", 1)
    elif looks_like_document_or_download(path):
        for f in findings:
            f["label"] = f["label"].replace("OCR:", "OCR dokumen:", 1)
    return findings


def analyze_video_enrichment(path: Path) -> list[dict]:
    """Whisper (ucapan/lirik) + visual keyframe + OCR teks on-screen (satu pass ffmpeg)."""
    from app.services.vision import _analyze_pil_image, extract_video_keyframes

    findings: list[dict] = []

    if settings.media_text_enabled and settings.gpu_whisper_enabled:
        try:
            from app.services.gpu_stack import audio_whisper

            for hit in audio_whisper.moderate(path):
                findings.append(hit.as_finding())
        except Exception as exc:
            log.warning("Video ASR skip: %s", exc)

    n = max(3, int(settings.video_overlay_keyframes))
    frames = extract_video_keyframes(path, max_frames=n)
    try:
        for fr in frames:
            for f in _analyze_pil_image(fr):
                f["label"] = f"Video keyframe: {f['label']}"
                f["layer_origin"] = Layer.L4.value
                findings.append(f)

            if settings.media_text_enabled or settings.ocr_enabled:
                for f in ocr_image_best_effort(fr, force=True):
                    f["label"] = f"Video on-screen {f['label']}"
                    f["layer_origin"] = Layer.L4.value
                    findings.append(f)
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

    return findings
