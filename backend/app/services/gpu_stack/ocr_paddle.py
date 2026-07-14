"""OCR backend — PaddleOCR (GPU stack default)."""

from __future__ import annotations

import logging
import re
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
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR

        langs = settings.ocr_langs.split(",")[0].strip() or "en"
        _ocr = PaddleOCR(use_angle_cls=True, lang=langs, use_gpu=bool(settings.ocr_gpu), show_log=False)
    return _ocr


def extract_text(image_path: Path) -> str:
    if not status()["available"]:
        return ""
    try:
        ocr = _get_ocr()
        result = ocr.ocr(str(image_path), cls=True)
        texts: list[str] = []
        if not result:
            return ""
        for block in result:
            if not block:
                continue
            for line in block:
                if line and len(line) >= 2 and line[1]:
                    texts.append(str(line[1][0]))
        return " ".join(texts)
    except Exception as exc:
        log.warning("PaddleOCR failed: %s", exc)
        return ""


def moderate_image(path: Path) -> list[ModerationHit]:
    """OCR text → risk keyword findings."""
    if not settings.gpu_stack_enabled:
        return []
    # Prefer dedicated paddle path; fall back to existing ocr module if paddle missing
    text = ""
    if status()["available"]:
        text = extract_text(path)
    else:
        try:
            from app.services import ocr as ocr_mod

            if settings.ocr_enabled:
                result = ocr_mod.run_ocr(path)
                text = result.text if result else ""
        except Exception:
            text = ""
    if not text.strip():
        return []
    norm = re.sub(r"\s+", " ", text.lower())
    hits: list[ModerationHit] = []
    for kw in settings.risk_keywords:
        if kw.lower() in norm:
            hits.append(
                ModerationHit(
                    category="konten_teks",
                    label=f"OCR indikasi: {kw}",
                    confidence=0.78,
                    layer_origin=Layer.L3.value,
                    evidence=text[:280],
                    backend="paddleocr",
                )
            )
    return hits
