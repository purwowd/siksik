from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PIL import Image


def caption_band_height(h: int) -> int:
    return max(8, h // 8)


def _edge_density(region: np.ndarray) -> float:
    edges = cv2.Canny(region, 80, 160)
    return float(edges.mean()) / 255.0


def iter_band_crops(bgr: np.ndarray) -> list[tuple[Image.Image, bool]]:
    """Return (PIL crop, is_dark_bar) per teks band."""
    if bgr is None or bgr.size == 0:
        return []

    h, w = bgr.shape[:2]
    if h < 64 or w < 64:
        return []

    band_h = caption_band_height(h)
    top = bgr[:band_h, :]
    bottom = bgr[h - band_h :, :]
    mid = bgr[band_h : h - band_h, :]

    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())
    mid_mean = float(mid.mean())
    mid_edges = _edge_density(mid)
    top_edges = _edge_density(top)
    bottom_edges = _edge_density(bottom)

    dark_bars = top_mean < mid_mean * 0.55 and bottom_mean < mid_mean * 0.55
    has_top_text = top_edges > 0.06 and top_edges > mid_edges * 0.65
    has_bottom_text = bottom_edges > 0.06 and bottom_edges > mid_edges * 0.65

    crops: list[tuple[Image.Image, bool]] = []

    def _to_pil(part: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(part, cv2.COLOR_BGR2RGB))

    if dark_bars or has_top_text:
        crops.append((_to_pil(top), dark_bars or top_mean < mid_mean * 0.7))
    if dark_bars or has_bottom_text:
        crops.append((_to_pil(bottom), dark_bars or bottom_mean < mid_mean * 0.7))
    if not crops:
        crops.append((_to_pil(bottom), bottom_mean < mid_mean * 0.85))

    return crops


def build_band_strip(bgr: np.ndarray) -> Optional[Image.Image]:
    """Gabungkan band crops jadi satu strip (fallback VLM transcribe)."""
    crops = iter_band_crops(bgr)
    if not crops:
        return None

    parts = [cv2.cvtColor(np.array(c.convert("RGB")), cv2.COLOR_RGB2BGR) for c, _ in crops]
    strip = np.vstack(parts)
    img = Image.fromarray(cv2.cvtColor(strip, cv2.COLOR_BGR2RGB))
    max_w = 640
    if img.width > max_w:
        scale = max_w / img.width
        img = img.resize((max_w, max(32, int(img.height * scale))), Image.Resampling.LANCZOS)
    return img


def parse_transcribe_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip().strip('"').strip("'")
        if len(cleaned) >= 2 and cleaned.lower() not in ("none", "no text", "unknown"):
            lines.append(cleaned[:160])
    if not lines and raw.strip():
        lines.append(raw.strip()[:160])
    return lines[:6]
