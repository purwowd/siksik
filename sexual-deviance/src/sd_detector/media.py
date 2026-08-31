from __future__ import annotations

import base64
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def resize_image(img: Image.Image, max_size: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_size:
        return img
    scale = max_size / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)


def load_image(path: str | Path, max_size: int = 512) -> Image.Image:
    img = Image.open(path).convert("RGB")
    return resize_image(img, max_size)


def load_image_bytes(data: bytes, max_size: int = 512) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return resize_image(img, max_size)


def image_to_base64(img: Image.Image, fmt: str = "JPEG", quality: int = 85) -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def extract_video_frames(
    path: str | Path,
    interval_sec: float = 2.0,
    max_frames: int = 30,
    max_size: int = 512,
    include_nudenet_bgr: bool = False,
) -> list[tuple[float, Image.Image]] | list[tuple[float, Image.Image, np.ndarray]]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps > 0 else 0

    if duration <= 0:
        cap.release()
        raise ValueError(f"Invalid video duration: {path}")

    step = max(1, int(fps * interval_sec))
    indices = list(range(0, total_frames, step))[:max_frames]

    frames: list[tuple[float, Image.Image]] = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        ts = idx / fps
        if include_nudenet_bgr:
            frames.append((ts, resize_image(img, max_size), bgr.copy()))
        else:
            frames.append((ts, resize_image(img, max_size)))

    cap.release()
    if not frames:
        raise ValueError(f"No frames extracted from: {path}")
    return frames


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
