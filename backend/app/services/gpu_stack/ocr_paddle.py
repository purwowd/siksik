"""OCR backend — PaddleOCR (GPU stack default)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.gpu_stack.types import ModerationHit

log = logging.getLogger(__name__)
_ocr = None


def status() -> dict:
    ok = False
    detail = "not loaded"
    try:
        from paddleocr import PaddleOCR  # noqa: F401

        ok = True
        detail = "paddleocr import ok"
    except Exception as exc:
        detail = f"unavailable: {exc}"
    return {
        "name": "PaddleOCR",
        "configured": settings.gpu_ocr_backend == "paddleocr" or settings.ocr_backend == "paddleocr",
        "available": ok,
        "detail": detail,
    }


def _get_ocr():
    """Compatibility wrapper around the process-wide OCR backend."""
    global _ocr
    if _ocr is None:
        from app.services import ocr as ocr_mod

        _ocr = ocr_mod.get_shared_backend("paddleocr")
    return _ocr


def extract_text(image_path: Path) -> str:
    if not status()["available"]:
        return ""
    try:
        from app.services import ocr as ocr_mod

        backend = _get_ocr()
        if backend is None:
            return ""
        result = ocr_mod.run_ocr(image_path, backend=backend)
        return result.text if result else ""
    except Exception as exc:
        log.warning("PaddleOCR failed: %s", exc)
        return ""


def moderate_image(path: Path) -> list[ModerationHit]:
    """OCR text → risk keyword findings (word-boundary)."""
    if not settings.gpu_stack_enabled:
        return []
    from app.services.lexicon import category_for_keyword, match_keywords

    text = ""
    if status()["available"]:
        text = extract_text(path)
    else:
        try:
            from app.services import ocr as ocr_mod

            if settings.ocr_enabled or settings.media_text_enabled:
                result = ocr_mod.run_ocr(path)
                text = result.text if result else ""
        except Exception:
            text = ""
    if not text.strip():
        return []
    kws = match_keywords(text)
    return [
        ModerationHit(
            category=category_for_keyword(kw),
            label=f"OCR indikasi: {kw}",
            confidence=0.78,
            layer_origin=Layer.L3.value,
            evidence=text[:280],
            backend="paddleocr",
        )
        for kw in kws[:5]
    ]
