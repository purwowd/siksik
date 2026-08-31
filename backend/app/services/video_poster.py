"""Cached JPEG poster frames for operator video thumbnails."""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".webm", ".mkv", ".3gp", ".avi", ".m4v"}
)
_FFMPEG_TIMEOUT_S = 20


def is_video_path(path: Path, mime: str | None = None) -> bool:
    if (mime or "").casefold().startswith("video/"):
        return True
    return path.suffix.casefold() in _VIDEO_SUFFIXES


def _cache_dir() -> Path:
    return settings.data_dir / "video_posters"


def _cache_path(path: Path) -> Path:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return _cache_dir() / f"{digest}.jpg"


def extract_video_poster(path: Path) -> Path | None:
    """Return a small JPEG still for ``path``, or None if ffmpeg cannot decode it."""
    if not path.is_file() or not is_video_path(path):
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    dest = _cache_path(path)
    try:
        src_mtime = path.stat().st_mtime
        if dest.is_file() and dest.stat().st_size > 32 and dest.stat().st_mtime >= src_mtime:
            return dest
    except OSError:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}.tmp.jpg")
    for seek in ("1", "0"):
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            seek,
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-vf",
            "scale=320:-2",
            "-q:v",
            "5",
            str(tmp),
        ]
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if not tmp.is_file() or tmp.stat().st_size <= 32:
            continue
        try:
            tmp.replace(dest)
            return dest
        except OSError:
            log.warning("video_poster_cache_write_failed")
            tmp.unlink(missing_ok=True)
            return None
    tmp.unlink(missing_ok=True)
    return None
