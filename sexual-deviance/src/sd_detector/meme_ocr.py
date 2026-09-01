from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .meme_bands import iter_band_crops
from .meme_rules import _fuzzy_phrase_match, _norm_ocr, load_meme_rules, match_phrase_figures

_OCR_SKIP = frozenset({"none", "no text", "unknown", ""})

_WEAK_OVERLAY_PHRASES = frozenset({
    "indonesian text", "text in indonesian", "some indonesian text",
    "indonesian language", "foreign text", "text overlay", "caption text",
    "overlay text", "unknown text", "illegible text", "unreadable text",
    "text on the image", "text on image", "visible text", "some text",
    "meme text", "written text", "words on the image",
})

_VLM_OVERLAY_GARBAGE = (
    "at the top", "at the bottom", "top and bottom", "showing a man",
    "showing a woman", "picture of a", "photo of a", "image of a",
    "meme with text", "text about", "with indonesian text",
    "president obama", "obama is shown", "is displayed", "is shown",
    "serious expression", "speech bubble with",
)


@dataclass
class OcrConfig:
    enabled: bool = True
    lazy: bool = True
    lang: str = "ind+eng"
    min_chars: int = 2
    vlm_band_fallback: bool = False
    full_res: bool = True
    max_size: int = 1280
    workers: int = 2


def prepare_ocr_bgr(bgr: np.ndarray, max_size: int = 1280) -> np.ndarray:
    """Downscale full-res hanya jika perlu — OCR lebih baik dari crop VLM 512px."""
    if bgr is None or bgr.size == 0:
        return bgr
    h, w = bgr.shape[:2]
    side = max(h, w)
    if side <= max_size:
        return bgr
    scale = max_size / side
    return cv2.resize(
        bgr,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_available(cfg: Optional[OcrConfig] = None) -> bool:
    cfg = cfg or OcrConfig()
    if not cfg.enabled:
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return tesseract_available()


@lru_cache(maxsize=1)
def _resolve_ocr_lang(preferred: str) -> str:
    if not tesseract_available():
        return "eng"
    try:
        import pytesseract

        langs = set(pytesseract.get_languages(config=""))
        parts = [p.strip() for p in preferred.split("+") if p.strip()]
        ok = [p for p in parts if p in langs]
        if ok:
            return "+".join(ok)
        if "eng" in langs:
            return "eng"
    except Exception:
        pass
    return "eng"


def _clean_ocr_line(line: str) -> str:
    cleaned = re.sub(r"\s+", " ", line).strip(" \"'|-")
    cleaned = cleaned.replace("|", "I")
    if len(cleaned) < 2:
        return ""
    if cleaned.lower() in _OCR_SKIP:
        return ""
    if re.fullmatch(r"[\W_]+", cleaned):
        return ""
    return cleaned[:160]


def _ocr_phrase_hit(text: str) -> bool:
    return bool(match_phrase_figures(text))


def _direct_phrase_hit(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in _cached_phrase_keys())


def _extract_embedded_phrases(line: str) -> list[str]:
    low = line.lower()
    found: list[str] = []
    for phrase in _cached_phrase_keys():
        if phrase in low and phrase not in found:
            found.append(phrase)
    return found


def _ocr_line_garbled(line: str) -> bool:
    cleaned = _clean_ocr_line(line)
    if not cleaned:
        return True
    if _direct_phrase_hit(cleaned):
        words = cleaned.split()
        avg = sum(len(w) for w in words) / max(1, len(words))
        if avg >= 4.0 and not re.search(r"\d{3,}", cleaned):
            return False
    alpha = sum(c.isalpha() for c in cleaned)
    if alpha / max(1, len(cleaned)) < 0.5:
        return True
    words = cleaned.split()
    if len(words) >= 2:
        avg = sum(len(w) for w in words) / len(words)
        if avg >= 4.5:
            return False
    return len(cleaned) >= 8 and bool(re.search(r"[a-z].*[A-Z]|[A-Z].*[a-z]{1,2}[A-Z]", cleaned))


def canonicalize_ocr_line(line: str) -> str:
    cleaned = _clean_ocr_line(line)
    if not cleaned:
        return line

    from .meme_rules import lookup_ocr_alias

    alias = lookup_ocr_alias(cleaned)
    if alias:
        return alias

    embedded = _extract_embedded_phrases(cleaned)
    if embedded and _ocr_line_garbled(cleaned):
        return embedded[0]

    if _direct_phrase_hit(cleaned) and not _ocr_line_garbled(cleaned):
        return cleaned

    rules = load_meme_rules()
    for phrase in sorted(rules.phrase_figures, key=len, reverse=True):
        if phrase in cleaned.lower():
            continue
        if _fuzzy_phrase_match(cleaned, phrase):
            if _ocr_line_garbled(cleaned) or len(cleaned) <= max(len(phrase) * 2, 12):
                return phrase

    return cleaned


def finalize_ocr_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        canon = canonicalize_ocr_line(raw)
        for candidate in (canon, *(_extract_embedded_phrases(raw) if _ocr_line_garbled(raw) else ())):
            key = candidate.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(candidate)

    out = _dedupe_overlay_lines(out)
    out.sort(key=lambda ln: (not _ocr_phrase_hit(ln), not _direct_phrase_hit(ln), -len(ln)))
    return out[:6]


_OVERLAY_FRAGMENT_CUES = (
    "saya tahu bahwa", "saya tahu", "tahu bahwa",
)


def is_overlay_fragment(line: str) -> bool:
    """Fragmen OCR/VLM parcial — bukan caption utuh."""
    cleaned = re.sub(r"\s+", " ", line).strip(" .\"'")
    if not cleaned or len(cleaned) < 4:
        return True
    if _ocr_phrase_hit(cleaned) or _direct_phrase_hit(cleaned):
        return False
    key = cleaned.lower()
    if any(c in key for c in _OVERLAY_FRAGMENT_CUES):
        return True
    if key.startswith(".") or key.startswith("saya tahu"):
        return True
    if ")" in cleaned and not _ocr_phrase_hit(cleaned) and len(cleaned) < 16:
        return True
    words = cleaned.split()
    if len(words) <= 3 and cleaned.endswith(".") and not _ocr_phrase_hit(cleaned):
        return True
    if len(words) == 2 and words[0] in ("saya", "tangan", "bahwa") and words[1] in ("tahu", "uu", "bahwa"):
        return True
    if "tangan uu" in key and "tanda tangan" not in key:
        return True
    return False


def clean_overlay_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if is_overlay_fragment(line) or is_vlm_description_overlay(line):
            continue
        key = line.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return out


def is_vlm_description_overlay(line: str) -> bool:
    """Teks overlay dari narasi VLM, bukan transkrip caption meme."""
    key = line.lower().strip()
    if not key:
        return True
    if any(g in key for g in _VLM_OVERLAY_GARBAGE):
        return True
    if key.startswith(("a meme ", "a picture ", "a photo ", "an image ", "the meme ")):
        return True
    if re.search(r"\b(showing|displayed|depicting|featuring)\b", key) and len(key.split()) >= 6:
        return True
    return False


def _overlay_line_substantive(line: str) -> bool:
    cleaned = _clean_ocr_line(line)
    if not cleaned:
        return False
    if is_vlm_description_overlay(cleaned):
        return False
    key = cleaned.lower()
    if key in _WEAK_OVERLAY_PHRASES:
        return False
    if any(key == w or key.startswith(w + " ") for w in _WEAK_OVERLAY_PHRASES):
        return False
    if _direct_phrase_hit(cleaned):
        return True
    words = cleaned.split()
    if len(words) >= 3:
        return True
    if cleaned.isupper() and len(cleaned) >= 6:
        return True
    id_particles = (
        "yang", "dan", "ko", "bro", "gak", "ga", "nggak", "tidak", "saya", "kamu",
        "lu", "gue", "nya", "deh", "sih", "dong", "aja", "kah", "lah", "jangan",
        "gapapa", "bilang", "rakyat", "harga", "naik", "uu", "tanda",
    )
    if len(words) >= 2 and any(p in key for p in id_particles):
        return True
    return False


def overlay_is_placeholder(overlay: list[str]) -> bool:
    if not overlay:
        return True
    return not any(_overlay_line_substantive(line) for line in overlay)


def overlay_is_weak(overlay: list[str], layout: Optional[list[str]] = None) -> bool:
    if overlay_is_placeholder(overlay):
        return True
    substantive = [ln for ln in overlay if _overlay_line_substantive(ln)]
    if layout and "caption_bars" in layout and len(substantive) < 2:
        return True
    return False


def _dedupe_overlay_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    lowered = [ln.lower() for ln in lines]
    out: list[str] = []
    for i, line in enumerate(lines):
        if any(
            i != j and len(lowered[i]) < len(lowered[j]) and lowered[i] in lowered[j]
            for j in range(len(lines))
        ):
            continue
        if any(
            i != j
            and SequenceMatcher(None, lowered[i], lowered[j]).ratio() >= 0.88
            and len(lowered[i]) < len(lowered[j])
            for j in range(len(lines))
        ):
            continue
        out.append(line)
    return out


def _ocr_line_useful(line: str, *, min_conf: float = 0.0) -> bool:
    cleaned = _clean_ocr_line(line)
    if not cleaned:
        return False
    if _ocr_phrase_hit(cleaned):
        return True
    alpha = sum(ch.isalnum() for ch in cleaned)
    if alpha < max(3, len(cleaned) * 0.45):
        return False
    if min_conf >= 40:
        return True
    return len(cleaned) >= 6 and alpha / len(cleaned) >= 0.58


def _ocr_line_quality(line: str) -> bool:
    cleaned = _clean_ocr_line(line)
    if not cleaned:
        return False
    if _direct_phrase_hit(cleaned) or _ocr_phrase_hit(cleaned):
        return True
    words = cleaned.split()
    if len(words) < 2:
        return False
    real = sum(1 for w in words if sum(c.isalpha() for c in w) >= 3)
    if real < max(2, (len(words) + 1) // 2):
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    return avg_len >= 4.0


def _fuzzy_phrase_in_text(text: str, phrase: str) -> bool:
    return _fuzzy_phrase_match(text, phrase)


def _ocr_blob_has_phrase(blob: str) -> bool:
    if _ocr_phrase_hit(blob):
        return True
    return any(_fuzzy_phrase_in_text(blob, phrase) for phrase in _cached_phrase_keys() if len(phrase) >= 6)


def _ocr_results_sufficient(lines: list[str], layout: Optional[list[str]] = None) -> bool:
    quality = [ln for ln in lines if _ocr_line_quality(ln)]
    if not quality:
        return False
    layout_set = set(layout or ())
    if "caption_bars" in layout_set:
        return len(quality) >= 2
    return bool(_ocr_blob_has_phrase(" ".join(quality)) or len(quality) >= 1)


@lru_cache(maxsize=1)
def _cached_phrase_keys() -> tuple[str, ...]:
    return tuple(sorted(load_meme_rules().phrase_figures.keys(), key=len, reverse=True))


def _upscale_gray(gray: np.ndarray, *, min_side: int = 600) -> np.ndarray:
    h, w = gray.shape[:2]
    side = max(h, w)
    if side < 400:
        min_side = max(min_side, 960)
    if side >= min_side:
        return gray
    scale = max(2, (min_side + side - 1) // side)
    return cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)


def _preprocess_variants(gray: np.ndarray, *, dark_bar: bool) -> list[np.ndarray]:
    gray = _upscale_gray(gray)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    variants: list[np.ndarray] = []

    if dark_bar:
        _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(otsu)
        variants.append(cv2.bitwise_not(otsu))
    else:
        edges = cv2.Canny(blur, 50, 150)
        kernel = np.ones((2, 2), np.uint8)
        variants.append(cv2.dilate(edges, kernel, iterations=1))
        adapt = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9,
        )
        variants.append(adapt if float(adapt.mean()) >= 127 else cv2.bitwise_not(adapt))

    return variants


def _ocr_with_confidence(proc: np.ndarray, lang: str, psm: int) -> list[tuple[str, float]]:
    import pytesseract

    config = f"--psm {psm} --oem 3 -l {_resolve_ocr_lang(lang)}"
    data = pytesseract.image_to_data(proc, config=config, output_type=pytesseract.Output.DICT)
    by_line: dict[tuple[int, int, int], list[tuple[str, float]]] = {}
    n = len(data["text"])
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        by_line.setdefault(key, []).append((word, conf))

    lines: list[tuple[str, float]] = []
    for words in by_line.values():
        text = " ".join(w for w, _ in words)
        cleaned = _clean_ocr_line(text)
        if not cleaned:
            continue
        avg_conf = sum(c for _, c in words) / len(words)
        lines.append((cleaned, avg_conf))
    return lines


def _ocr_single_crop(img: Image.Image, *, dark_bar: bool, lang: str) -> list[tuple[str, float]]:
    if not ocr_available():
        return []

    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    scored: dict[str, tuple[str, float]] = {}

    for proc in _preprocess_variants(gray, dark_bar=dark_bar):
        for line, conf in _ocr_with_confidence(proc, lang, 7):
            lk = line.lower()
            prev = scored.get(lk)
            if prev is None or conf > prev[1]:
                scored[lk] = (line, conf)

        if scored and _ocr_blob_has_phrase(" ".join(scored.keys())):
            break

        if not scored:
            for line, conf in _ocr_with_confidence(proc, lang, 11):
                lk = line.lower()
                prev = scored.get(lk)
                if prev is None or conf > prev[1]:
                    scored[lk] = (line, conf)

    return sorted(scored.values(), key=lambda x: x[1], reverse=True)


def _layout_band_crops(bgr: np.ndarray, layout: list[str]) -> list[tuple[Image.Image, bool]]:
    h, w = bgr.shape[:2]
    band_h = max(12, h // 4)
    top = bgr[:band_h, :]
    bottom = bgr[h - band_h :, :]
    mid = bgr[band_h : h - band_h, :]
    mid_mean = float(mid.mean()) if mid.size else float(bottom.mean())
    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())

    def _to_pil(part: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(part, cv2.COLOR_BGR2RGB))

    layout_set = set(layout)
    crops: list[tuple[Image.Image, bool]] = []
    if "speech_bubble" in layout_set:
        cy1 = int(h * 0.55)
        bubble = bgr[:cy1, :]
        if bubble.size:
            crops.append((_to_pil(bubble), False))
        if "caption_bars" not in layout_set and "top_text_band" not in layout_set:
            return crops
    if "top_text_band" in layout_set or "caption_bars" in layout_set:
        crops.append((_to_pil(top), top_mean < mid_mean * 0.75))
    if "bottom_text_band" in layout_set or "caption_bars" in layout_set:
        crops.append((_to_pil(bottom), bottom_mean < mid_mean * 0.75))
    return crops


def _center_text_crops(bgr: np.ndarray) -> list[tuple[Image.Image, bool]]:
    h, w = bgr.shape[:2]

    def _to_pil(part: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(part, cv2.COLOR_BGR2RGB))

    crops: list[tuple[Image.Image, bool]] = []
    for y0, y1, x0, x1 in (
        (int(h * 0.12), int(h * 0.88), int(w * 0.04), int(w * 0.96)),
        (int(h * 0.30), int(h * 0.72), int(w * 0.08), int(w * 0.92)),
    ):
        part = bgr[y0:y1, x0:x1]
        if part.size:
            crops.append((_to_pil(part), False))
    return crops


def _iter_ocr_regions(
    bgr: np.ndarray,
    layout: Optional[list[str]] = None,
    *,
    expand: bool = False,
) -> list[tuple[Image.Image, bool]]:
    if bgr is None or bgr.size == 0:
        return []

    h, w = bgr.shape[:2]
    if h < 64 or w < 64:
        return []

    def _to_pil(part: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(part, cv2.COLOR_BGR2RGB))

    if layout and not expand:
        targeted = _layout_band_crops(bgr, layout)
        if targeted:
            return targeted

    crops: list[tuple[Image.Image, bool]] = list(iter_band_crops(bgr))
    if not expand:
        return crops

    layout_set = set(layout or ())
    band_h = max(12, h // 4)
    bottom = bgr[h - band_h :, :]
    mid = bgr[band_h : h - band_h, :]
    mid_mean = float(mid.mean()) if mid.size else float(bottom.mean())
    bottom_mean = float(bottom.mean())
    bottom_crop = (_to_pil(bottom), bottom_mean < mid_mean * 0.75)

    if "bottom_text_band" in layout_set or not layout_set:
        crops.append(bottom_crop)

    needs_center = (
        not layout_set
        or "speech_bubble" in layout_set
        or ("top_text_band" not in layout_set and "bottom_text_band" not in layout_set)
    )
    if needs_center:
        cy0, cy1 = h // 5, h * 4 // 5
        center = bgr[cy0:cy1, :]
        if center.size:
            crops.append((_to_pil(center), float(center.mean()) < float(bgr.mean()) * 0.85))

    return crops


def _run_ocr_on_regions(
    regions: list[tuple[Image.Image, bool]],
    lang: str,
    *,
    stop_on_phrase: bool = True,
    workers: int = 2,
) -> dict[str, float]:
    if not regions:
        return {}

    scored: dict[str, float] = {}

    def _scan(crop: Image.Image, dark_bar: bool) -> list[tuple[str, float]]:
        return _ocr_single_crop(crop, dark_bar=dark_bar, lang=lang)

    if len(regions) == 1:
        crop, dark_bar = regions[0]
        for line, conf in _scan(crop, dark_bar):
            lk = line.lower()
            if lk not in scored or conf > scored[lk]:
                scored[lk] = conf
        return scored

    pool_workers = min(max(1, workers), len(regions))
    with ThreadPoolExecutor(max_workers=pool_workers) as pool:
        futures = [
            pool.submit(_scan, crop, dark_bar)
            for crop, dark_bar in regions
        ]
        for fut in as_completed(futures):
            for line, conf in fut.result():
                lk = line.lower()
                if lk not in scored or conf > scored[lk]:
                    scored[lk] = conf
            if stop_on_phrase and _ocr_blob_has_phrase(" ".join(scored.keys())):
                for pending in futures:
                    pending.cancel()
                break

    return scored


def _select_useful_lines(scored: dict[str, float], *, multi_band: bool = False) -> list[str]:
    if not scored:
        return []

    ranked = sorted(scored.keys(), key=lambda k: scored[k], reverse=True)
    if multi_band:
        strong = [line for line in ranked if scored[line] >= 78 and _clean_ocr_line(line)]
        if len(strong) >= 2:
            return strong[:6]

    phrase_lines = [line for line in ranked if _ocr_phrase_hit(line)]
    if phrase_lines:
        extra = [line for line in ranked if line not in phrase_lines and scored[line] >= 75]
        return (phrase_lines + extra)[:6]

    high_conf = [line for line in ranked if scored[line] >= 80 and _clean_ocr_line(line)]
    if high_conf:
        return high_conf[:6]

    useful = [line for line in ranked if _ocr_line_useful(line, min_conf=scored[line])]
    if useful:
        return useful[:6]

    if _ocr_blob_has_phrase(" ".join(ranked)):
        return ranked[:4]
    return []


def extract_band_ocr(
    bgr: np.ndarray,
    cfg: Optional[OcrConfig] = None,
    layout: Optional[list[str]] = None,
) -> list[str]:
    cfg = cfg or OcrConfig()
    if not ocr_available(cfg):
        return []

    layout_set = set(layout or ())
    caption_meme = "caption_bars" in layout_set

    fast_scored = _run_ocr_on_regions(
        _iter_ocr_regions(bgr, layout, expand=False),
        cfg.lang,
        stop_on_phrase=not caption_meme,
        workers=cfg.workers,
    )
    if caption_meme:
        strong = [k for k, v in fast_scored.items() if v >= 78]
        if len(strong) >= 2:
            return finalize_ocr_lines(_select_useful_lines(fast_scored, multi_band=True))

    fast_lines = _select_useful_lines(fast_scored, multi_band=caption_meme)
    if fast_lines and _ocr_blob_has_phrase(" ".join(fast_lines)) and not caption_meme:
        return finalize_ocr_lines(fast_lines)

    full_scored = dict(fast_scored)
    for lk, conf in _run_ocr_on_regions(
        _iter_ocr_regions(bgr, layout, expand=True),
        cfg.lang,
        stop_on_phrase=False,
        workers=cfg.workers,
    ).items():
        if lk not in full_scored or conf > full_scored[lk]:
            full_scored[lk] = conf

    lines = _select_useful_lines(full_scored, multi_band=caption_meme)
    if not _ocr_blob_has_phrase(" ".join(lines)):
        lines.extend(_phrases_from_scored(full_scored))
    return finalize_ocr_lines(lines)


def _phrases_from_scored(scored: dict[str, float]) -> list[str]:
    if not scored:
        return []
    blob = " ".join(scored.keys())
    return _extract_embedded_phrases(blob)


def _center_ocr(bgr: np.ndarray, cfg: OcrConfig) -> list[str]:
    scored = _run_ocr_on_regions(
        _center_text_crops(bgr),
        cfg.lang,
        stop_on_phrase=False,
        workers=cfg.workers,
    )
    lines = _select_useful_lines(scored, multi_band=False)
    return finalize_ocr_lines([ln for ln in lines if _ocr_line_quality(ln)] or lines)


def extract_meme_ocr(
    bgr_vlm: np.ndarray,
    cfg: Optional[OcrConfig] = None,
    layout: Optional[list[str]] = None,
    bgr_full: Optional[np.ndarray] = None,
) -> list[str]:
    """OCR optimal: band 512px (cepat), full-res untuk speech/center."""
    cfg = cfg or OcrConfig()
    if not ocr_available(cfg):
        return []

    layout_set = set(layout or ())
    full = bgr_full if bgr_full is not None else bgr_vlm

    if "caption_bars" in layout_set:
        band_bgr = full if cfg.full_res else bgr_vlm
        lines = extract_band_ocr(band_bgr, cfg, layout)
        if _ocr_results_sufficient(lines, layout):
            return lines
        if band_bgr is not bgr_vlm:
            lines = list(dict.fromkeys([*lines, *extract_band_ocr(bgr_vlm, cfg, layout)]))
            if _ocr_results_sufficient(lines, layout):
                return finalize_ocr_lines(lines)
        extra = _center_ocr(full, cfg)
        combined = list(dict.fromkeys([*lines, *extra]))
        return finalize_ocr_lines(combined)

    if "speech_bubble" in layout_set:
        scored = _run_ocr_on_regions(
            _layout_band_crops(full, list(layout_set)),
            cfg.lang,
            stop_on_phrase=False,
            workers=cfg.workers,
        )
        lines = finalize_ocr_lines(_select_useful_lines(scored))
        if _ocr_results_sufficient(lines, layout):
            return lines
        extra = _center_ocr(full, cfg)
        return finalize_ocr_lines(list(dict.fromkeys([*lines, *extra])))

    if layout_set:
        lines = extract_band_ocr(full, cfg, layout)
        if _ocr_results_sufficient(lines, layout):
            return lines

    return _center_ocr(full, cfg)


def extract_band_ocr_with_fallback(
    bgr: np.ndarray,
    cfg: Optional[OcrConfig] = None,
    layout: Optional[list[str]] = None,
) -> list[str]:
    """Backward-compatible wrapper."""
    return extract_meme_ocr(bgr, cfg, layout, bgr_full=bgr)


def ocr_lines_actionable(lines: list[str], *, ctx_present: bool = False) -> bool:
    if not lines:
        return False
    return _ocr_blob_has_phrase(" ".join(lines))
