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
    "signal",
    "message",
    "notif",
    "messenger",
)

# Poster/dokumen di unduhan — bukan kamera-roll DCIM/Pictures.
_FORCE_OCR_DIR_NAMES = frozenset(
    {
        "documents",
        "document",
        "download",
        "downloads",
        "screenshots",
        "screenshot",
        "browser",
        "telegram",
        "whatsapp",
        "messenger",
    }
)


def origin_haystack(path: Path, origin_hint: str | None = None) -> str:
    parts = [path.parent.as_posix(), path.name]
    if origin_hint:
        parts.append(origin_hint)
    return " ".join(parts).lower().replace("\\", "/")


def looks_like_chat_or_screenshot(path: Path, origin_hint: str | None = None) -> bool:
    hay = origin_haystack(path, origin_hint)
    return any(h in hay for h in _SCREENSHOT_HINTS)


def looks_like_document_or_download(path: Path, origin_hint: str | None = None) -> bool:
    hay = origin_haystack(path, origin_hint)
    return any(name in hay for name in _FORCE_OCR_DIR_NAMES)


def looks_like_text_heavy_image(path: Path, origin_hint: str | None = None) -> bool:
    """Heuristic: screenshot/dokumen, atau edge density tinggi ≈ UI / teks / poster."""
    if looks_like_chat_or_screenshot(path, origin_hint) or looks_like_document_or_download(
        path,
        origin_hint,
    ):
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


def should_skip_generic_visual_model(path: Path, origin_hint: str | None = None) -> bool:
    """Identify flat application/document UI, not merely any high-edge image.

    Natural protest/campaign photos often have high edge density and readable
    banners, so they should receive both OCR and CLIP. Predominantly low-color
    portrait screenshots can safely use OCR/context without generic CLIP, which
    strongly confuses application settings pages with political posters.
    """
    try:
        from PIL import Image

        with Image.open(path) as source:
            image = source.convert("HSV")
            image.thumbnail((96, 96))
            width, height = image.size
            pixels = list(image.getdata())
    except Exception:
        return False
    if not pixels or width <= 0:
        return False
    portrait_ratio = height / width
    low_saturation_ratio = sum(pixel[1] < 38 for pixel in pixels) / len(pixels)
    return portrait_ratio >= 1.45 and low_saturation_ratio >= 0.62


def should_try_ocr(path: Path, *, force: bool = False, origin_hint: str | None = None) -> bool:
    if force:
        return True
    if not settings.media_text_enabled and not settings.gpu_stack_enabled:
        return False
    if looks_like_text_heavy_image(path, origin_hint):
        return True
    # Mode FULL + ocr_full_gallery: OCR semua gambar di gallery / pictures / dcim
    if settings.ocr_full_gallery:
        from app.models.schemas import AcquisitionMode
        from app.services.hash_cache import get_analysis_mode

        mode = get_analysis_mode()
        if mode == AcquisitionMode.FULL:
            hay = origin_haystack(path, origin_hint)
            if any(
                name in hay
                for name in (
                    "gallery",
                    "pictures",
                    "dcim",
                    "camera",
                    "documents",
                    "download",
                    "downloads",
                )
            ):
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


def ocr_image_best_effort(
    path: Path,
    *,
    force: bool = False,
    origin_hint: str | None = None,
) -> list[dict]:
    """OCR foto/screenshot/dokumen → findings (word-boundary lexicon).

    Jika SADT_OCR_ENABLED=1, biarkan path legacy `analyze_image_ocr` menangani
    (kecuali force=True untuk keyframe video).
    """
    from app.services import ocr as ocr_mod

    if not should_try_ocr(path, force=force, origin_hint=origin_hint):
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
    if looks_like_chat_or_screenshot(path, origin_hint):
        for f in findings:
            f["label"] = f["label"].replace("OCR:", "OCR chat/screenshot:", 1)
    elif looks_like_document_or_download(path, origin_hint):
        for f in findings:
            f["label"] = f["label"].replace("OCR:", "OCR dokumen:", 1)
    return findings


def analyze_video_enrichment(
    path: Path,
    *,
    frames: list[Path] | None = None,
) -> list[dict]:
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
    owns_frames = frames is None
    frame_values = extract_video_keyframes(path, max_frames=n) if frames is None else list(frames)
    try:
        from app.models.schemas import AcquisitionMode
        from app.services.hash_cache import get_analysis_mode

        # QUICK + OCR flag: still skip per-keyframe EasyOCR (iOS .mov dumps lag hard).
        # FULL keeps on-screen OCR when SADT_OCR_ENABLED / media_text on.
        do_frame_ocr = bool(settings.media_text_enabled or settings.ocr_enabled)
        quick_mode = get_analysis_mode() == AcquisitionMode.QUICK
        from app.services import content_policy, content_visual

        ocr_used = 0
        for fr in frame_values:
            frame_findings: list[dict] = []
            for f in _analyze_pil_image(fr):
                f["label"] = f"Video keyframe: {f['label']}"
                f["layer_origin"] = Layer.L4.value
                frame_findings.append(f)

            visual_candidates = content_visual.analyze_image(fr)

            should_ocr_frame = (
                do_frame_ocr
                and ocr_used < int(settings.video_ocr_max_frames)
                and (
                    not quick_mode
                    or should_try_ocr(fr)
                )
            )
            if should_ocr_frame:
                for f in ocr_image_best_effort(fr, force=True):
                    f["label"] = f"Video on-screen {f['label']}"
                    f["layer_origin"] = Layer.L4.value
                    frame_findings.append(f)
                ocr_used += 1
            promoted = content_policy.confirm_visual_candidates(
                visual_candidates,
                frame_findings,
            )
            for f in promoted:
                f["label"] = f"Video keyframe: {f['label']}"
                f["layer_origin"] = Layer.L4.value
            findings.extend(frame_findings)
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

    return findings
