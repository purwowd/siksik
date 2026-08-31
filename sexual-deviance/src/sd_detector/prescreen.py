from __future__ import annotations

import cv2
import numpy as np


def skin_ratio(bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([0, 20, 70], dtype=np.uint8)
    upper = np.array([25, 255, 255], dtype=np.uint8)
    mask1 = cv2.inRange(hsv, lower, upper)
    lower2 = np.array([160, 20, 70], dtype=np.uint8)
    upper2 = np.array([180, 255, 255], dtype=np.uint8)
    mask2 = cv2.inRange(hsv, lower2, upper2)
    skin = cv2.bitwise_or(mask1, mask2)
    return float(np.count_nonzero(skin)) / skin.size


def landscape_score(bgr: np.ndarray) -> float:
    """Semakin tinggi = semakin mirip pemandangan/objek statis (aman)."""
    small = cv2.resize(bgr, (160, 160), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    # Banyak hijau/biru langit = landscape
    green_blue = cv2.inRange(hsv, np.array([35, 30, 30]), np.array([130, 255, 255]))
    nature_ratio = float(np.count_nonzero(green_blue)) / green_blue.size
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    upper = gray[:80, :]
    lower = gray[80:, :]
    horizon = abs(float(np.mean(upper)) - float(np.mean(lower))) / 255.0
    return float(np.clip(0.6 * nature_ratio + 0.4 * (1.0 - horizon), 0.0, 1.0))


def prescreen_score(bgr: np.ndarray) -> float:
    """
    Skor 0–1: semakin tinggi semakin perlu analisis lanjut.
    Hanya skip LLM jika skor sangat rendah + mirip landscape.
    """
    small = cv2.resize(bgr, (160, 160), interpolation=cv2.INTER_AREA)
    skin = skin_ratio(small)
    lscape = landscape_score(small)

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(np.count_nonzero(edges)) / edges.size

    # Kulit dominan → perlu scan; landscape dominan → aman
    score = 0.50 * skin + 0.20 * edge_density + 0.30 * (1.0 - lscape)
    return float(np.clip(score, 0.0, 1.0))


def is_likely_safe(bgr: np.ndarray, threshold: float) -> bool:
    """Hanya true untuk pemandangan/objek — bukan orang berpakaian sedikit."""
    score = prescreen_score(bgr)
    skin = skin_ratio(cv2.resize(bgr, (160, 160), interpolation=cv2.INTER_AREA))
    lscape = landscape_score(bgr)
    return score < threshold and skin < 0.18 and lscape > 0.35


def pil_to_bgr(img) -> np.ndarray:
    rgb = np.array(img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
