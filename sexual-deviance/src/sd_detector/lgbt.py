from __future__ import annotations

import re
from typing import Iterable, Optional

import cv2
import numpy as np

from .schema import LgbtContext, Orientation, Severity

# --- Teks: bendera, simbol, pakaian, scene ---

FLAG_COLOR_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("rainbow", ("rainbow flag", "rainbow banner", "rainbow colors", "rainbow stripe", "pride flag", "lgbt flag", "lgbtq flag")),
    ("progress", ("progress flag", "progress pride", "inclusive flag", "philadelphia flag")),
    ("trans", ("trans flag", "transgender flag", "trans pride", "light blue pink white flag")),
    ("bisexual", ("bisexual flag", "bi flag", "bi pride", "pink purple blue flag")),
    ("lesbian", ("lesbian flag", "sapphic flag", "orange pink white flag")),
    ("gay", ("gay flag", "mlm flag", "green blue teal flag")),
    ("pansexual", ("pansexual flag", "pan flag", "pan pride")),
    ("asexual", ("asexual flag", "ace flag", "ace pride")),
]

SYMBOL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("pride_flag", ("rainbow flag", "pride flag", "lgbt flag", "holding a flag", "waving a flag")),
    ("rainbow_symbol", ("rainbow", "rainbow badge", "rainbow pin", "rainbow emblem")),
    ("pride_symbol", ("pride symbol", "lgbt symbol", "equality symbol", "triangle badge")),
    ("rainbow_crosswalk", ("rainbow crosswalk", "rainbow pavement", "rainbow street")),
]

CLOTHING_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("rainbow_clothing", ("rainbow shirt", "rainbow t-shirt", "rainbow clothing", "rainbow outfit", "rainbow dress")),
    ("pride_merch", ("pride shirt", "pride clothing", "pride outfit", "pride merchandise", "pride apparel")),
    ("rainbow_accessory", ("rainbow bracelet", "rainbow necklace", "rainbow hat", "rainbow bandana")),
    ("trans_colors_clothing", ("trans colored", "trans pride shirt", "pink blue white shirt")),
]

SCENE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("pride_parade", ("pride parade", "pride march", "pride festival", "pride event", "pride celebration")),
    ("pride_street", ("pride street", "rainbow decorations", "rainbow banners on street")),
    ("protest_lgbt", ("lgbt protest", "equality march", "gay rights march")),
]

LGBT_GENERAL = (
    "lgbt", "lgbtq", "lgbtqia", "queer", "pride", "homosexual",
    "same-sex", "same sex", "gender identity",
)

NATURAL_RAINBOW = (
    "rainbow in the sky", "rainbow over", "rainbow appears", "rainbow arc",
    "double rainbow", "rainbow and", "after the rain", "rainbow behind",
    "waterfall", "stream", "river", "forest", "nature", "hiking", "mountain trail",
)

INTIMACY_CUES = ("kiss", "kissing", "hug", "hugging", "embrac", "danc", "hold hands", "holding hands")


def _match_patterns(text: str, patterns: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    t = text.lower()
    found: list[str] = []
    for label, keys in patterns:
        if any(k in t for k in keys):
            found.append(label)
    return found


def _gender_counts(text: str) -> tuple[int, int]:
    t = text.lower()
    men = len(re.findall(r"\bmen\b", t)) + len(re.findall(r"\bman\b", t))
    women = len(re.findall(r"\bwomen\b", t)) + len(re.findall(r"\bwoman\b", t))
    if re.search(r"\btwo men\b", t):
        men = max(men, 2)
    if re.search(r"\btwo women\b", t):
        women = max(women, 2)
    return men, women


def _has_intimacy(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in INTIMACY_CUES)


CLASSICAL_ART = (
    "statue", "sculpture", "michelangelo", "david", "marble", "classical art",
    "botticelli", "birth of venus", "museum display", "art gallery",
)


def _is_classical_art_scene(texts: list[str]) -> bool:
    combined = " ".join(texts).lower()
    if not any(k in combined for k in CLASSICAL_ART):
        return False
    return not any(k in combined for k in ("pride parade", "pride march", "pride festival", "pride event"))


def _is_natural_rainbow_scene(texts: list[str]) -> bool:
    combined = " ".join(texts).lower()
    if any(k in combined for k in ("waterfall", "cliff", "riverbank", "stream", "mountain", "hiking", "desert", "rocky")):
        if not any(k in combined for k in ("pride parade", "pride march", "pride festival", "pride event")):
            return True
    strong_lgbt = any(k in combined for k in (
        "rainbow flag", "pride flag", "lgbt flag", "lgbtq flag", "pride parade",
        "pride march", "pride shirt", "pride t-shirt", "rainbow shirt", "rainbow t-shirt",
    ))
    if strong_lgbt:
        return False
    return (
        any(k in combined for k in NATURAL_RAINBOW)
        or ("rainbow" in combined and any(k in combined for k in ("sky", "cloud", "mountain", "landscape", "over the")))
        or any(k in combined for k in ("waterfall", "stream", "river", "forest", "nature", "hiking", "mountain trail", "bank of a"))
    )


def infer_lgbt_from_text(*texts: str) -> LgbtContext:
    if _is_classical_art_scene(list(texts)) or _is_natural_rainbow_scene(list(texts)):
        return LgbtContext()

    combined = " ".join(texts).lower()
    flag_colors = _match_patterns(combined, FLAG_COLOR_PATTERNS)
    symbols = _match_patterns(combined, SYMBOL_PATTERNS)
    clothing = _match_patterns(combined, CLOTHING_PATTERNS)
    scene = _match_patterns(combined, SCENE_PATTERNS)

    if "pink" in combined and "blue" in combined and ("purple" in combined or "bi " in combined or "bisexual" in combined):
        if "bisexual" not in flag_colors:
            flag_colors.append("bisexual")
    if ("light blue" in combined or "pink" in combined) and "white" in combined and "trans" in combined:
        if "trans" not in flag_colors:
            flag_colors.append("trans")
    if "three colors" in combined and "pink" in combined and "blue" in combined:
        if "bisexual" not in flag_colors:
            flag_colors.append("bisexual")

    signals: list[str] = []
    for group in (flag_colors, symbols, clothing, scene):
        signals.extend(group)
    if any(k in combined for k in LGBT_GENERAL):
        signals.append("lgbt_context")
    if "rainbow" in combined and not _is_natural_rainbow_scene(list(texts)):
        if "rainbow" not in flag_colors:
            signals.append("rainbow")
            if "rainbow" not in flag_colors:
                flag_colors.append("rainbow")

    present = bool(signals) or bool(flag_colors)

    orientation_hint = "none"
    men, women = _gender_counts(combined)
    intimate = _has_intimacy(combined)
    has_lgbt_visual = bool(flag_colors or symbols or clothing or scene or "pride" in combined or "rainbow" in combined)

    if intimate and has_lgbt_visual:
        if men >= 2 and women == 0:
            orientation_hint = "gay"
        elif women >= 2 and men == 0:
            orientation_hint = "lesbian"
        elif men >= 1 and women >= 1:
            orientation_hint = "heterosexual"
        elif "lesbian" in flag_colors or "lesbian" in combined:
            orientation_hint = "lesbian"
        elif "gay" in flag_colors or ("gay" in combined and "lesbian" not in combined):
            orientation_hint = "gay"
        elif "bisexual" in flag_colors or "bisexual" in combined:
            orientation_hint = "bisexual"
    elif has_lgbt_visual and not intimate:
        if "lesbian" in flag_colors:
            orientation_hint = "lesbian"
        elif "gay" in flag_colors:
            orientation_hint = "gay"
        elif "bisexual" in flag_colors:
            orientation_hint = "bisexual"
        elif "trans" in flag_colors:
            orientation_hint = "queer"

    return LgbtContext(
        present=present,
        flag_colors=sorted(set(flag_colors)),
        symbols=sorted(set(symbols)),
        clothing=sorted(set(clothing)),
        scene=sorted(set(scene)),
        signals=sorted(set(signals)),
        orientation_hint=orientation_hint,
    )


def detect_rainbow_pixels(bgr: np.ndarray) -> list[str]:
    """Heuristic: deteksi stripe warna pelangi di pixel (tanpa filename)."""
    if bgr is None or bgr.size == 0:
        return []
    h, w = bgr.shape[:2]
    if h < 32 or w < 32:
        return []

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    found: list[str] = []

    # Cek beberapa horizontal band (typical pride flag stripes)
    bands = 6
    band_h = max(1, h // bands)
    hue_hits: set[str] = set()

    for i in range(bands):
        strip = hsv[i * band_h : (i + 1) * band_h, :]
        if strip.size == 0:
            continue
        hue = strip[:, :, 0]
        sat = strip[:, :, 1]
        val = strip[:, :, 2]
        mask = (sat > 80) & (val > 80)
        if mask.sum() < 50:
            continue
        med_hue = float(np.median(hue[mask]))
        if med_hue < 10 or med_hue >= 170:
            hue_hits.add("red")
        elif med_hue < 25:
            hue_hits.add("orange")
        elif med_hue < 35:
            hue_hits.add("yellow")
        elif med_hue < 85:
            hue_hits.add("green")
        elif med_hue < 130:
            hue_hits.add("blue")
        else:
            hue_hits.add("purple")

    if len(hue_hits) >= 4:
        found.append("rainbow")

    # Trans flag: dominasi pink + biru muda
    pink_mask = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 160)) & (hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 150)
    blue_mask = (hsv[:, :, 0] > 95) & (hsv[:, :, 0] < 115) & (hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 150)
    pink_ratio = pink_mask.sum() / max(1, pink_mask.size)
    blue_ratio = blue_mask.sum() / max(1, blue_mask.size)
    if pink_ratio > 0.08 and blue_ratio > 0.08:
        found.append("trans")

    return found


def merge_lgbt_contexts(contexts: Iterable[LgbtContext]) -> LgbtContext:
    items = list(contexts)
    if not items:
        return LgbtContext()

    flag_colors: set[str] = set()
    symbols: set[str] = set()
    clothing: set[str] = set()
    scene: set[str] = set()
    signals: set[str] = set()
    hints: list[str] = []

    for ctx in items:
        flag_colors.update(ctx.flag_colors)
        symbols.update(ctx.symbols)
        clothing.update(ctx.clothing)
        scene.update(ctx.scene)
        signals.update(ctx.signals)
        if ctx.orientation_hint != "none":
            hints.append(ctx.orientation_hint)

    orientation_hint = "none"
    for preferred in ("lesbian", "gay", "bisexual", "heterosexual", "queer"):
        if preferred in hints:
            orientation_hint = preferred
            break

    return LgbtContext(
        present=any(c.present for c in items),
        flag_colors=sorted(flag_colors),
        symbols=sorted(symbols),
        clothing=sorted(clothing),
        scene=sorted(scene),
        signals=sorted(signals),
        orientation_hint=orientation_hint,
    )


def analyze_lgbt(
    bgr: Optional[np.ndarray],
    texts: list[str],
) -> LgbtContext:
    if _is_classical_art_scene(texts) or _is_natural_rainbow_scene(texts):
        return LgbtContext()

    text_ctx = infer_lgbt_from_text(*texts)
    pixel_colors: list[str] = []
    if bgr is not None and not _is_natural_rainbow_scene(texts):
        pixel_colors = detect_rainbow_pixels(bgr)

    # Pixel rainbow saja tanpa teks pride/LGBT → abaikan (langit/pelangi alami / false positive)
    text_has_lgbt = text_ctx.present or any(
        k in " ".join(texts).lower()
        for k in ("pride", "lgbt", "flag", "parade", "queer", "rainbow flag", "bisexual", "transgender")
    )
    if pixel_colors and not text_has_lgbt:
        pixel_colors = []

    if pixel_colors:
        flags = sorted(set(text_ctx.flag_colors + pixel_colors))
        signals = sorted(set(text_ctx.signals + pixel_colors))
        if "rainbow" in pixel_colors:
            signals.append("pixel_rainbow")
        text_ctx = text_ctx.model_copy(update={
            "present": True,
            "flag_colors": flags,
            "signals": signals,
        })

    return text_ctx


def orientation_from_lgbt_hint(hint: str) -> Orientation:
    mapping = {
        "gay": Orientation.GAY,
        "lesbian": Orientation.LESBIAN,
        "bisexual": Orientation.BISEXUAL,
        "heterosexual": Orientation.HETEROSEXUAL,
        "queer": Orientation.OTHER,
    }
    return mapping.get(hint, Orientation.NONE)


def resolve_orientation_with_lgbt(
    current: Orientation,
    lgbt: LgbtContext,
    texts: list[str],
    severity: Severity,
) -> Orientation:
    """Gabung orientasi dari gender + konteks LGBT (bendera, ciuman, dll.)."""
    if severity == Severity.SAFE:
        return Orientation.NONE

    combined = " ".join(texts).lower()
    intimate = _has_intimacy(combined)
    men, women = _gender_counts(combined)

    for text in texts:
        men_t, women_t = _gender_counts(text)
        men = max(men, men_t)
        women = max(women, women_t)

    # Pasangan jelas man+woman + adegan romantis → hetero (prioritas)
    if intimate and (
        re.search(r"\bman\b.{0,80}\bwoman\b", combined)
        or re.search(r"\bwoman\b.{0,80}\bman\b", combined)
        or ("man" in combined and "woman" in combined and any(k in combined for k in ("kiss", "hug", "embrac")))
    ):
        return Orientation.HETEROSEXUAL

    if current == Orientation.HETEROSEXUAL:
        return current

    has_lgbt_visual = bool(
        lgbt.flag_colors or lgbt.symbols or lgbt.clothing or lgbt.scene
        or "pride" in combined or "rainbow" in combined
    )

    # Aturan user: ciuman + warna/simbol LGBT → gay / lesbian
    if lgbt.present and intimate and has_lgbt_visual:
        if men >= 2 and women == 0:
            return Orientation.GAY
        if women >= 2 and men == 0:
            return Orientation.LESBIAN
        if re.search(r"man.{0,120}man", combined) and "woman" not in combined:
            return Orientation.GAY
        if re.search(r"woman.{0,120}woman", combined):
            return Orientation.LESBIAN

    if lgbt.orientation_hint != "none" and has_lgbt_visual:
        hinted = orientation_from_lgbt_hint(lgbt.orientation_hint)
        if hinted == Orientation.GAY and men >= 2 and women == 0:
            return Orientation.GAY
        if hinted == Orientation.LESBIAN and women >= 2 and men == 0:
            return Orientation.LESBIAN
        if hinted == Orientation.BISEXUAL and intimate:
            return Orientation.BISEXUAL

    if current != Orientation.NONE:
        return current

    if lgbt.present and intimate and has_lgbt_visual:
        if "lesbian" in lgbt.flag_colors:
            return Orientation.LESBIAN
        if "gay" in lgbt.flag_colors:
            return Orientation.GAY

    return current
