"""Tanggal capture media — EXIF → pola nama file Android → mtime filesystem."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# Android Camera / Screenshots / common patterns
_NAME_PATTERNS = (
    re.compile(r"(?:IMG|PXL|VID|Screenshot)[_-]?(\d{4})(\d{2})(\d{2})", re.I),
    re.compile(r"(?:IMG|VID)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", re.I),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
)


def _parse_exif_datetime(raw: object) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def extract_captured_at(path: Path) -> tuple[datetime | None, str]:
    """Return (naive local-ish datetime, source: exif|filename|mtime|none)."""
    # 1) EXIF
    try:
        from PIL import Image, ExifTags

        with Image.open(path) as im:
            exif = im.getexif()
            if exif:
                tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    dt = _parse_exif_datetime(tags.get(key))
                    if dt:
                        return dt, "exif"
    except Exception:
        pass

    # 2) Filename
    name = path.name
    for pat in _NAME_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        g = m.groups()
        try:
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
            hh = int(g[3]) if len(g) > 3 else 0
            mm = int(g[4]) if len(g) > 4 else 0
            ss = int(g[5]) if len(g) > 5 else 0
            return datetime(y, mo, d, hh, mm, ss), "filename"
        except ValueError:
            continue

    # 3) Filesystem mtime
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None), "mtime"
    except OSError:
        return None, "none"


def capture_meta(path: Path) -> dict:
    dt, source = extract_captured_at(path)
    if not dt:
        return {"date_source": source}
    return {
        "captured_at": dt.isoformat(timespec="seconds"),
        "captured_year": dt.year,
        "date_source": source,
    }
