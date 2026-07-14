"""OCR module — pluggable backends for GPU server.

Enable on server:
  export SADT_OCR_ENABLED=1
  export SADT_OCR_BACKEND=easyocr   # easyocr | paddleocr | tesseract
  export SADT_OCR_GPU=1
  pip install -r requirements-gpu.txt

Local/CI without GPU: OCR stays off; unit tests use FakeOCRBackend.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer

log = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    backend: str
    confidence: float | None = None
    device: str | None = None


class OcrBackend(ABC):
    name: str = "base"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def extract(self, image_path: Path) -> OcrResult: ...


class EasyOCRBackend(OcrBackend):
    name = "easyocr"

    def __init__(self) -> None:
        self._reader = None

    def available(self) -> bool:
        try:
            import easyocr  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_reader(self):
        if self._reader is None:
            import easyocr

            langs = [x.strip() for x in settings.ocr_langs.split(",") if x.strip()]
            # id not always in easyocr; use en+id fallback list
            use = [l for l in langs if l in ("en", "id", "ch_sim", "ch_tra", "ar", "fr", "de")] or ["en"]
            if "id" not in use and "en" not in use:
                use = ["en"]
            # EasyOCR: 'id' may not exist — prefer en
            use = ["en"] if "id" in use and "en" not in use else (["en"] if use == ["id"] else use)
            if use == ["id"]:
                use = ["en"]
            self._reader = easyocr.Reader(use, gpu=bool(settings.ocr_gpu))
        return self._reader

    def extract(self, image_path: Path) -> OcrResult:
        reader = self._get_reader()
        rows = reader.readtext(str(image_path), detail=1, paragraph=True)
        texts: list[str] = []
        confs: list[float] = []
        for row in rows:
            if len(row) >= 2:
                texts.append(str(row[1]))
            if len(row) >= 3 and isinstance(row[2], (int, float)):
                confs.append(float(row[2]))
        text = " ".join(texts).strip()
        avg = sum(confs) / len(confs) if confs else None
        return OcrResult(
            text=text,
            backend=self.name,
            confidence=avg,
            device="cuda" if settings.ocr_gpu else "cpu",
        )


class PaddleOCRBackend(OcrBackend):
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None

    def available(self) -> bool:
        try:
            from paddleocr import PaddleOCR  # noqa: F401

            return True
        except ImportError:
            return False

    def _get(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=True,
                lang="en",
                use_gpu=bool(settings.ocr_gpu),
                show_log=False,
            )
        return self._ocr

    def extract(self, image_path: Path) -> OcrResult:
        ocr = self._get()
        result = ocr.ocr(str(image_path), cls=True)
        texts: list[str] = []
        confs: list[float] = []
        if result:
            for block in result:
                if not block:
                    continue
                for line in block:
                    if line and len(line) >= 2:
                        texts.append(str(line[1][0]))
                        confs.append(float(line[1][1]))
        text = " ".join(texts).strip()
        avg = sum(confs) / len(confs) if confs else None
        return OcrResult(
            text=text,
            backend=self.name,
            confidence=avg,
            device="cuda" if settings.ocr_gpu else "cpu",
        )


class TesseractBackend(OcrBackend):
    name = "tesseract"

    def available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, image_path: Path) -> OcrResult:
        import pytesseract
        from PIL import Image

        with Image.open(image_path) as im:
            text = pytesseract.image_to_string(im, lang=settings.ocr_langs.replace(",", "+"))
        return OcrResult(
            text=(text or "").strip(),
            backend=self.name,
            confidence=None,
            device="cpu",
        )


class FakeOCRBackend(OcrBackend):
    """Deterministic backend for unit tests (no GPU / no heavy deps)."""

    name = "fake"

    def __init__(self, forced_text: str = "") -> None:
        self.forced_text = forced_text

    def available(self) -> bool:
        return True

    def extract(self, image_path: Path) -> OcrResult:
        # If filename embeds cue, surface it; else forced_text
        stem = image_path.stem.replace("_", " ").replace("-", " ")
        text = self.forced_text or stem
        return OcrResult(text=text, backend=self.name, confidence=0.99, device="fake")


_BACKENDS = {
    "easyocr": EasyOCRBackend,
    "paddleocr": PaddleOCRBackend,
    "tesseract": TesseractBackend,
    "fake": FakeOCRBackend,
}


def ocr_status() -> dict:
    chosen = settings.ocr_backend
    cls = _BACKENDS.get(chosen, EasyOCRBackend)
    inst = cls()
    return {
        "enabled": bool(settings.ocr_enabled),
        "backend": chosen,
        "gpu": bool(settings.ocr_gpu),
        "available": inst.available() if settings.ocr_enabled or chosen == "fake" else inst.available(),
        "langs": settings.ocr_langs,
    }


@lru_cache(maxsize=1)
def get_backend() -> OcrBackend | None:
    if not settings.ocr_enabled:
        return None
    cls = _BACKENDS.get(settings.ocr_backend)
    if not cls:
        log.warning("Unknown OCR backend %s", settings.ocr_backend)
        return None
    backend = cls()
    if not backend.available():
        log.warning("OCR backend %s not installed", settings.ocr_backend)
        return None
    return backend


def reset_backend_cache() -> None:
    get_backend.cache_clear()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def ocr_findings_from_text(text: str, *, backend: str, keywords: list[str] | None = None) -> list[dict]:
    """Map OCR text → L3 findings via risk lexicon."""
    if not text or not text.strip():
        return []
    kws = keywords or settings.risk_keywords
    norm = _normalize(text)
    findings: list[dict] = []
    for kw in kws:
        if kw in norm:
            findings.append(
                {
                    "category": "anti_pemerintah"
                    if kw not in ("narkoba", "judi online", "pornografi anak")
                    else "perilaku_menyimpang",
                    "label": f"OCR: {kw}",
                    "confidence": 0.86,
                    "layer_origin": Layer.L3.value,
                    "evidence": f"[{backend}] {text[:280]}",
                }
            )
    return findings


def run_ocr(image_path: Path, *, backend: OcrBackend | None = None) -> OcrResult | None:
    engine = backend if backend is not None else get_backend()
    if engine is None:
        return None
    try:
        return engine.extract(image_path)
    except Exception as exc:  # noqa: BLE001
        log.exception("OCR failed on %s: %s", image_path, exc)
        return None


def analyze_image_ocr(image_path: Path, *, backend: OcrBackend | None = None) -> list[dict]:
    """Public entry: OCR image → keyword findings (empty if OCR off/unavailable)."""
    if backend is None and not settings.ocr_enabled:
        return []
    result = run_ocr(image_path, backend=backend)
    if not result or not result.text:
        return []
    return ocr_findings_from_text(result.text, backend=result.backend)
