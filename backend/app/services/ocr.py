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
import os
import tempfile
import threading
from contextlib import nullcontext
from importlib.metadata import PackageNotFoundError, version
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

# EasyOCR/torch: one Reader + one in-flight extract. Parallel CPU workers OOM Mac labs
# (session 2e5f8be3: "Using CPU" × worker_concurrency → uvicorn Killed:9).
_OCR_EXTRACT_LOCK = threading.RLock()
_SHARED_BACKENDS: dict[str, object] = {}
_SHARED_BACKENDS_LOCK = threading.Lock()


def _version_major(value: str) -> int:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def paddle_stack_compatible(ocr_version: str, paddle_version: str) -> bool:
    """PaddleOCR 3.x requires PaddlePaddle 3.x (PaddleX / PP-OCRv6)."""
    ocr_major = _version_major(ocr_version)
    paddle_major = _version_major(paddle_version)
    if ocr_major <= 0:
        return False
    if ocr_major >= 3:
        return paddle_major >= 3
    return paddle_major >= 2


def _installed_paddle_version() -> str | None:
    for package in ("paddlepaddle-gpu", "paddlepaddle"):
        try:
            return version(package)
        except PackageNotFoundError:
            continue
    try:
        import paddle  # type: ignore

        return str(getattr(paddle, "__version__", "") or "")
    except Exception:
        return None


def _paddleocr_stack_ready() -> bool:
    try:
        ocr_version = version("paddleocr")
    except PackageNotFoundError:
        return False
    paddle_version = _installed_paddle_version()
    if not paddle_version:
        return False
    if paddle_stack_compatible(ocr_version, paddle_version):
        return True
    log.warning(
        "PaddleOCR %s is incompatible with PaddlePaddle %s; pin paddleocr>=2.7,<3 "
        "or upgrade paddlepaddle to 3.x",
        ocr_version,
        paddle_version,
    )
    return False


def prepare_ocr_path(
    image_path: Path,
    *,
    max_edge_px: int | None = None,
    min_edge_px: int | None = None,
    sharpen: bool | None = None,
) -> tuple[Path, Path | None]:
    """EXIF + upscale foto kecil + downscale besar + contraste/sharpen sebelum OCR."""
    max_edge = int(settings.ocr_max_edge_px if max_edge_px is None else max_edge_px)
    min_edge = int(settings.ocr_min_edge_px if min_edge_px is None else min_edge_px)
    do_sharpen = bool(settings.ocr_sharpen if sharpen is None else sharpen)
    try:
        from PIL import Image, ImageFilter, ImageOps

        with Image.open(image_path) as im:
            orientation = None
            try:
                orientation = im.getexif().get(274)  # Orientation
            except Exception:
                orientation = None
            need_exif = orientation not in (None, 1)
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            w, h = im.size
            longest = max(w, h)
            need_upscale = min_edge > 0 and longest < min_edge
            need_downscale = max_edge > 0 and longest > max_edge
            if not need_upscale and not need_downscale and not do_sharpen and not need_exif:
                return image_path, None
            if need_upscale:
                scale = min_edge / float(longest)
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
                w, h = im.size
                longest = max(w, h)
            if need_downscale and longest > max_edge:
                im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            im = ImageOps.autocontrast(im, cutoff=1)
            if do_sharpen:
                im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=150, threshold=2))
            fd, name = tempfile.mkstemp(suffix=".jpg", prefix="sadt_ocr_")
            os.close(fd)
            tmp = Path(name)
            im.save(tmp, "JPEG", quality=95)
            return tmp, tmp
    except Exception as exc:
        log.debug("OCR preprocess skip %s: %s", image_path.name, exc)
        return image_path, None


def normalize_ocr_text(text: str) -> str:
    """Rapikan hasil OCR sebelum lexicon (spasi huruf-digit, koreksi tipikal)."""
    import re
    import unicodedata

    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", t)
    t = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", t)
    # Tipikal EasyOCR Indo (termasuk yang nempel: DKIJAKARIA)
    t = re.sub(r"(?i)jakaria", "jakarta", t)
    t = re.sub(r"(?i)\bgantl\b", "ganti", t)
    t = re.sub(r"(?i)(dki)\s*(jakarta)", r"\1 \2", t)
    t = re.sub(r"(?i)(dki)(jakarta)", r"\1 \2", t)
    return re.sub(r"\s+", " ", t).strip()


def _bbox_sort_key(bbox) -> tuple[float, float]:
    """Urut baca kasar: atas→bawah, kiri→kanan."""
    try:
        ys = [float(p[1]) for p in bbox]
        xs = [float(p[0]) for p in bbox]
        return (min(ys), min(xs))
    except Exception:
        return (0.0, 0.0)


def _easyocr_lines(rows: list, *, paragraph: bool, min_conf: float) -> tuple[str, float | None]:
    """Parse EasyOCR rows → teks + rata-rata conf."""
    items: list[tuple[tuple[float, float], str, float]] = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        if paragraph:
            # paragraph mode: (bbox, text) tanpa conf — atau (bbox, text, conf)
            text = str(row[1]).strip()
            conf = float(row[2]) if len(row) >= 3 and isinstance(row[2], (int, float)) else 1.0
            bbox = row[0] if row else None
        else:
            bbox, text = row[0], str(row[1]).strip()
            conf = float(row[2]) if len(row) >= 3 and isinstance(row[2], (int, float)) else 1.0
        if not text:
            continue
        if conf < min_conf and len(text) < 12:
            continue
        if conf < max(0.08, min_conf * 0.5):
            continue
        key = _bbox_sort_key(bbox) if bbox is not None else (0.0, 0.0)
        items.append((key, text, conf))
    items.sort(key=lambda x: x[0])
    texts = [t for _, t, _ in items]
    confs = [c for _, _, c in items]
    joined = normalize_ocr_text(" ".join(texts))
    avg = sum(confs) / len(confs) if confs else None
    return joined, avg


@dataclass
class OcrRegion:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float | None = None


@dataclass
class OcrResult:
    text: str
    backend: str
    confidence: float | None = None
    device: str | None = None
    regions: tuple[OcrRegion, ...] = ()


def _easyocr_regions(rows: list, *, min_conf: float) -> tuple[OcrRegion, ...]:
    output: list[OcrRegion] = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        bbox = row[0]
        text = str(row[1]).strip()
        confidence = (
            float(row[2])
            if len(row) >= 3 and isinstance(row[2], (int, float))
            else 1.0
        )
        if (
            not text
            or (confidence < min_conf and len(text) < 12)
            or confidence < max(0.08, min_conf * 0.5)
        ):
            continue
        try:
            xs = [float(point[0]) for point in bbox]
            ys = [float(point[1]) for point in bbox]
        except (TypeError, ValueError, IndexError):
            continue
        if not xs or not ys:
            continue
        output.append(
            OcrRegion(
                text=text,
                left=max(0, int(min(xs))),
                top=max(0, int(min(ys))),
                right=max(0, int(max(xs))),
                bottom=max(0, int(max(ys))),
                confidence=confidence,
            )
        )
    output.sort(key=lambda value: (value.top, value.left))
    return tuple(output)


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
            use = [l for l in langs if l in ("en", "id", "ch_sim", "ch_tra", "ar", "fr", "de")] or ["en"]
            if "id" not in use and "en" not in use:
                use = ["en"]
            if "id" in use and "en" not in use:
                use.append("en")
            model_dir = settings.ocr_model_dir or (settings.data_dir / "easyocr")
            model_dir.mkdir(parents=True, exist_ok=True)
            self._reader = easyocr.Reader(
                use,
                gpu=bool(settings.ocr_gpu),
                model_storage_directory=str(model_dir),
                user_network_directory=str(model_dir / "user_network"),
            )
        return self._reader

    def extract(
        self,
        image_path: Path,
        *,
        mag_ratio: float | None = None,
    ) -> OcrResult:
        reader = self._get_reader()
        paragraph = bool(settings.ocr_paragraph)
        min_conf = float(settings.ocr_min_confidence)
        mag = float(mag_ratio if mag_ratio is not None else settings.ocr_mag_ratio or 1.5)
        # CPU: smaller canvas — default 3200 spikes RAM on archive grids.
        canvas = 3200 if settings.ocr_gpu else 1600
        with _OCR_EXTRACT_LOCK:
            rows = reader.readtext(
                str(image_path),
                detail=1,
                paragraph=paragraph,
                mag_ratio=mag,
                canvas_size=canvas,
            )
        text, avg = _easyocr_lines(rows, paragraph=paragraph, min_conf=min_conf)
        return OcrResult(
            text=text,
            backend=self.name,
            confidence=avg,
            device="cuda" if settings.ocr_gpu else "cpu",
            regions=_easyocr_regions(rows, min_conf=min_conf),
        )


class PaddleOCRBackend(OcrBackend):
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr = None
        self._device = "cpu"
        self._force_cpu = False

    @property
    def accelerator_requested(self) -> bool:
        return bool(settings.ocr_gpu and not self._force_cpu)

    def fallback_to_cpu(self) -> None:
        """Keep this shared instance on CPU after a broken CUDA/cuDNN runtime."""
        self._force_cpu = True
        self._ocr = None
        self._device = "cpu"

    def available(self) -> bool:
        try:
            from paddleocr import PaddleOCR  # noqa: F401

            return _paddleocr_stack_ready()
        except ImportError:
            return False

    def _get(self):
        if self._ocr is None:
            if not _paddleocr_stack_ready():
                raise RuntimeError(
                    "PaddleOCR/PaddlePaddle versions are incompatible "
                    "(need paddleocr 2.x with paddle 2.x, or both 3.x)."
                )
            from paddleocr import PaddleOCR

            langs = [value.strip() for value in settings.ocr_langs.split(",") if value.strip()]
            lang = langs[0] if langs else "en"
            try:
                major = int(version("paddleocr").split(".", 1)[0])
            except (PackageNotFoundError, ValueError):
                major = 2
            if major >= 3:
                device = "cpu"
                if self.accelerator_requested:
                    try:
                        import paddle

                        if paddle.device.is_compiled_with_cuda():
                            device = "gpu"
                    except Exception:
                        device = "cpu"
                self._device = "cuda" if device.startswith("gpu") else "cpu"
                self._ocr = PaddleOCR(
                    lang=lang,
                    device=device,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            else:
                use_gpu = self.accelerator_requested
                self._device = "cuda" if use_gpu else "cpu"
                self._ocr = PaddleOCR(
                    use_angle_cls=True,
                    lang=lang,
                    use_gpu=use_gpu,
                    show_log=False,
                )
        return self._ocr

    def extract(self, image_path: Path) -> OcrResult:
        ocr = self._get()
        try:
            major = int(version("paddleocr").split(".", 1)[0])
        except (PackageNotFoundError, ValueError):
            major = 2
        result = ocr.predict(str(image_path)) if major >= 3 else ocr.ocr(str(image_path), cls=True)
        texts: list[str] = []
        confs: list[float] = []
        regions: list[OcrRegion] = []
        if major >= 3 and result:
            for block in result:
                try:
                    block_texts = list(block["rec_texts"])
                    block_scores = list(block["rec_scores"])
                except (KeyError, TypeError, ValueError):
                    continue
                try:
                    block_boxes = list(block["rec_boxes"])
                except (KeyError, TypeError, ValueError):
                    try:
                        block_boxes = list(block["rec_polys"])
                    except (KeyError, TypeError, ValueError):
                        block_boxes = []
                for index, value in enumerate(block_texts):
                    recognized = str(value).strip()
                    if not recognized:
                        continue
                    score = float(block_scores[index]) if index < len(block_scores) else 1.0
                    texts.append(recognized)
                    confs.append(score)
                    if index >= len(block_boxes):
                        continue
                    try:
                        raw_box = block_boxes[index]
                        flattened = list(raw_box.tolist()) if hasattr(raw_box, "tolist") else list(raw_box)
                        if flattened and isinstance(flattened[0], (list, tuple)):
                            xs = [float(point[0]) for point in flattened]
                            ys = [float(point[1]) for point in flattened]
                        elif len(flattened) >= 4:
                            xs = [float(flattened[0]), float(flattened[2])]
                            ys = [float(flattened[1]), float(flattened[3])]
                        else:
                            continue
                        regions.append(
                            OcrRegion(
                                text=recognized,
                                left=max(0, int(min(xs))),
                                top=max(0, int(min(ys))),
                                right=max(0, int(max(xs))),
                                bottom=max(0, int(max(ys))),
                                confidence=score,
                            )
                        )
                    except (TypeError, ValueError, IndexError):
                        continue
        elif result:
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
            text=normalize_ocr_text(text),
            backend=self.name,
            confidence=avg,
            device=self._device,
            regions=tuple(sorted(regions, key=lambda value: (value.top, value.left))),
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
            text=normalize_ocr_text((text or "").strip()),
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
        stem = image_path.stem.replace("_", " ").replace("-", " ")
        text = self.forced_text or stem
        return OcrResult(text=normalize_ocr_text(text), backend=self.name, confidence=0.99, device="fake")


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
        "preload": bool(settings.ocr_preload),
        "available": inst.available() if settings.ocr_enabled or chosen == "fake" else inst.available(),
        "langs": settings.ocr_langs,
        "max_edge_px": settings.ocr_max_edge_px,
        "min_edge_px": settings.ocr_min_edge_px,
        "sharpen": bool(settings.ocr_sharpen),
        "paragraph": bool(settings.ocr_paragraph),
        "min_confidence": settings.ocr_min_confidence,
        "mag_ratio": settings.ocr_mag_ratio,
    }


def get_shared_backend(backend_name: str | None = None) -> OcrBackend | None:
    """Process-wide OCR backend singleton (never construct a second EasyOCR Reader)."""
    name = (backend_name or settings.ocr_backend or "easyocr").strip().lower()
    with _SHARED_BACKENDS_LOCK:
        existing = _SHARED_BACKENDS.get(name)
        if existing is not None:
            return existing
        cls = _BACKENDS.get(name)
        if not cls:
            log.warning("Unknown OCR backend %s", name)
            return None
        backend = cls()
        if not backend.available():
            log.warning("OCR backend %s not installed", name)
            return None
        _SHARED_BACKENDS[name] = backend
        return backend


@lru_cache(maxsize=1)
def get_backend() -> OcrBackend | None:
    if not settings.ocr_enabled:
        return None
    return get_shared_backend(settings.ocr_backend)


def warmup_backend() -> dict[str, str | bool]:
    """Load the configured OCR predictors before the first analysis session."""
    backend = get_backend()
    if backend is None:
        return {"ready": False, "backend": settings.ocr_backend, "device": "unavailable"}
    try:
        if isinstance(backend, PaddleOCRBackend):
            try:
                backend._get()
            except Exception as exc:
                if backend.accelerator_requested and _is_paddle_accelerator_error(exc):
                    log.warning(
                        "PaddleOCR GPU warmup failed (%s); preloading CPU fallback",
                        type(exc).__name__,
                    )
                    backend.fallback_to_cpu()
                    backend._get()
                else:
                    raise
            device = backend._device
        elif isinstance(backend, EasyOCRBackend):
            backend._get_reader()
            device = "cuda" if settings.ocr_gpu else "cpu"
        else:
            device = "cpu"
        log.info("OCR backend preloaded backend=%s device=%s", backend.name, device)
        return {"ready": True, "backend": backend.name, "device": device}
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR preload failed backend=%s: %s", backend.name, exc)
        return {"ready": False, "backend": backend.name, "device": "unavailable"}


def reset_backend_cache() -> None:
    get_backend.cache_clear()
    with _SHARED_BACKENDS_LOCK:
        _SHARED_BACKENDS.clear()


def ocr_keyword_corpus() -> list[str]:
    """Lexicon OCR for findings; public-figure names stay context-only.

    ``tokoh_keywords`` are intentionally excluded.  They are consumed by
    :func:`fuse_tokoh_and_text` to identify the target of a meme/insult, but a
    name such as Prabowo, Jokowi, or ``presiden`` is not itself risky content.
    """
    seen: set[str] = set()
    out: list[str] = []
    # Contextual meme/insult phrases are classified by ``content_policy``
    # below. Keeping this legacy pass limited to the explicit risk corpus also
    # prevents token fallback from reducing "lengserkan Jokowi" to "Jokowi".
    for kw in settings.risk_keywords:
        low = kw.lower().strip()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(kw)
    return out


def extract_image_text(image_path: Path, *, backend: OcrBackend | None = None) -> tuple[str, str | None]:
    """OCR sekali → (teks, backend_name)."""
    result = run_ocr(image_path, backend=backend)
    if not result or not result.text:
        return "", None
    return result.text, result.backend


def ocr_findings_from_text(text: str, *, backend: str, keywords: list[str] | None = None) -> list[dict]:
    """Map OCR text → L3 findings via risk lexicon (word-boundary)."""
    from app.services.lexicon import findings_from_text, layer_l3

    corpus = keywords if keywords is not None else ocr_keyword_corpus()
    findings = findings_from_text(
        text,
        label_prefix="OCR",
        layer=layer_l3(),
        confidence=0.86,
        backend=backend,
        keywords=corpus,
    )
    if settings.content_detection_enabled:
        from app.services import content_policy

        findings.extend(
            content_policy.findings_from_text(
                text,
                backend=backend,
                layer=layer_l3(),
                image_context=True,
            )
        )
    return findings


def fuse_tokoh_and_text(
    *,
    path: Path,
    ocr_text: str,
    ocr_backend: str | None,
    tokoh_findings: list[dict],
    ocr_findings: list[dict],
) -> list[dict]:
    """Combine a public figure with explicit meme/insult text on one image.

    A detected public figure is supporting context, never an
    ``anti_pemerintah`` finding by itself.  Canonical political categories are
    emitted only when the text contains an actual satire or insult cue.
    """
    from app.services.content_policy import (
        CONTENT_CATEGORY_LABELS,
        POLITICAL_INSULT,
        POLITICAL_MEME,
    )
    from app.services.lexicon import (
        layer_l3,
        match_keywords,
        meme_insult_corpus,
        tokoh_name_hits,
    )

    if not ocr_text.strip() and not tokoh_findings:
        return list(ocr_findings) + list(tokoh_findings)

    tokoh_ocr = tokoh_name_hits(ocr_text)
    has_tokoh_vis = any(
        str(f.get("label", "")).lower().startswith("tokoh:") for f in tokoh_findings
    )
    has_tokoh = has_tokoh_vis or bool(tokoh_ocr)

    fused: list[dict] = []
    fused.extend(ocr_findings)
    fused.extend(tokoh_findings)

    if not has_tokoh or not ocr_text.strip():
        return fused

    political = match_keywords(
        ocr_text,
        list(settings.meme_hate_keywords),
        allow_token_fallback=False,
    )
    insults = match_keywords(ocr_text, meme_insult_corpus(), allow_token_fallback=False)
    if not political and not insults:
        return fused

    tokoh_bits: list[str] = []
    if has_tokoh_vis:
        for f in tokoh_findings:
            lab = str(f.get("label", ""))
            if lab.lower().startswith("tokoh:"):
                tokoh_bits.append(lab.replace("Tokoh:", "").strip())
    tokoh_bits.extend(tokoh_ocr)
    seen_t: set[str] = set()
    tokoh_uniq = []
    for t in tokoh_bits:
        k = t.lower()
        if k not in seen_t:
            seen_t.add(k)
            tokoh_uniq.append(t)

    tokoh_s = ", ".join(tokoh_uniq[:3]) or "tokoh"
    be = ocr_backend or "ocr"
    evidence = (
        f"[{be}+clip] {path.name} | tokoh={tokoh_s} | teks={ocr_text[:220]}"
    )[:320]
    existing_categories = {str(item.get("category") or "") for item in fused}
    if political and POLITICAL_MEME not in existing_categories:
        fused.append(
            {
                "category": POLITICAL_MEME,
                "label": CONTENT_CATEGORY_LABELS[POLITICAL_MEME],
                "confidence": 0.93,
                "layer_origin": layer_l3(),
                "evidence": evidence,
            }
        )
    if insults and POLITICAL_INSULT not in existing_categories:
        fused.append(
            {
                "category": POLITICAL_INSULT,
                "label": CONTENT_CATEGORY_LABELS[POLITICAL_INSULT],
                "confidence": 0.93,
                "layer_origin": layer_l3(),
                "evidence": evidence,
            }
        )
    return fused


def consolidate_image_findings(findings: list[dict]) -> list[dict]:
    """Satu foto — gabung OCR berulang; meme composite menggantikan OCR+tokoh terpisah."""
    ocr: list[dict] = []
    tokoh: list[dict] = []
    meme: list[dict] = []
    other: list[dict] = []
    for f in findings:
        lab = str(f.get("label", ""))
        low = lab.lower()
        if (
            low.startswith("meme/poster tokoh + ujaran:")
            or str(f.get("category") or "") in {"political_meme", "political_insult"}
        ):
            meme.append(f)
        elif low.startswith("tokoh:"):
            tokoh.append(f)
        elif low.startswith("ocr"):
            ocr.append(f)
        else:
            other.append(f)

    out = list(other)
    if meme:
        out.extend(meme)
        return out

    if ocr:
        kws: list[str] = []
        seen_kw: set[str] = set()
        for f in ocr:
            lab = str(f.get("label", ""))
            kw = lab.split(":", 1)[-1].strip()
            if not kw:
                continue
            key = kw.lower()
            if key in seen_kw:
                continue
            seen_kw.add(key)
            kws.append(kw)
        best = max(ocr, key=lambda x: float(x.get("confidence", 0)))
        prefix = str(best.get("label", "OCR")).split(":", 1)[0]
        out.append(
            {
                **best,
                "label": f"{prefix}: {', '.join(kws[:8])}",
                "confidence": max(float(x.get("confidence", 0)) for x in ocr),
            }
        )

    out.extend(tokoh)
    return out


def run_ocr(
    image_path: Path,
    *,
    backend: OcrBackend | None = None,
    max_edge_px: int | None = None,
    min_edge_px: int | None = None,
    sharpen: bool | None = None,
    mag_ratio: float | None = None,
) -> OcrResult | None:
    engine = backend if backend is not None else get_backend()
    if engine is None:
        return None
    ocr_path, tmp = prepare_ocr_path(
        image_path,
        max_edge_px=max_edge_px,
        min_edge_px=min_edge_px,
        sharpen=sharpen,
    )
    try:
        from app.services.inference_guard import gpu_inference_slot

        accelerator_requested = bool(settings.ocr_gpu)
        if isinstance(engine, PaddleOCRBackend):
            accelerator_requested = engine.accelerator_requested
        gpu_slot = gpu_inference_slot() if accelerator_requested else nullcontext()
        # Every shared backend instance is single-flight. PaddleOCR is not
        # thread-safe either, while EasyOCR already uses this re-entrant lock.
        with _OCR_EXTRACT_LOCK, gpu_slot:
            try:
                if isinstance(engine, EasyOCRBackend):
                    return engine.extract(ocr_path, mag_ratio=mag_ratio)
                return engine.extract(ocr_path)
            except Exception as exc:
                if (
                    isinstance(engine, PaddleOCRBackend)
                    and engine.accelerator_requested
                    and _is_paddle_accelerator_error(exc)
                ):
                    log.warning(
                        "PaddleOCR GPU runtime unavailable (%s); switching the "
                        "shared backend to CPU",
                        type(exc).__name__,
                    )
                    engine.fallback_to_cpu()
                    return engine.extract(ocr_path)
                raise
    except Exception as exc:  # noqa: BLE001
        log.exception("OCR failed on %s: %s", image_path, exc)
        return None
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _is_paddle_accelerator_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in message
        for marker in (
            "cudnn",
            "cublas",
            "libcudart",
            "cuda error",
            "cuda driver",
            "gpu place",
        )
    )


def analyze_image_ocr(image_path: Path, *, backend: OcrBackend | None = None) -> list[dict]:
    """Public entry: OCR image → keyword findings (empty if OCR off/unavailable)."""
    if backend is None and not settings.ocr_enabled:
        return []
    result = run_ocr(image_path, backend=backend)
    if not result or not result.text:
        return []
    findings = ocr_findings_from_text(result.text, backend=result.backend)
    if settings.content_detection_enabled:
        from app.services import content_policy

        return content_policy.merge_content_findings(findings)
    return findings
