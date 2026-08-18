"""Gallery albums for transferred session media.

Lists files already indexed in the session. Does not pull extra device
content. The external android-media-puller script is reference-only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.time_scope import build_time_scope
from app.core.db import db
from app.models.schemas import (
    AcquisitionMode,
    GalleryAlbumOut,
    GalleryItemOut,
    PaginatedGallery,
)

ACCESS_ALL = "all"
ACCESS_FREQUENT = "frequent"
ACCESS_RECENT = "recent"
ACCESS_FAVORITE = "favorite"
ACCESS_ORDER = (ACCESS_FREQUENT, ACCESS_RECENT, ACCESS_FAVORITE)
RESERVED_ALBUMS = (ACCESS_ALL, *ACCESS_ORDER)
ACCESS_LABELS = {
    ACCESS_ALL: "Semua",
    ACCESS_FREQUENT: "Paling sering",
    ACCESS_RECENT: "Terbaru diakses",
    ACCESS_FAVORITE: "Favorit",
}

EXCLUDE_SOURCES = {
    "sms",
    "contacts",
    "contact",
    "visible_ui",
    "accessibility_visible_ui",
    "notification",
    "notification_listener",
}
EXCLUDE_ROLES = {"screenshot"}
GENERIC_LEAVES = {
    "0",
    "emulated",
    "storage",
    "sdcard",
    "self",
    "primary",
    "files",
    "file",
    "media",
}
ALBUM_ALIASES = {
    "screenshot": "Screenshots",
    "screenshots": "Screenshots",
    "download": "Download",
    "downloads": "Download",
    "unduhan": "Download",
    "camera": "Camera",
    "dcim": "Camera",
    "pictures": "Pictures",
    "foto": "Pictures",
    "movies": "Movies",
    "video": "Movies",
    "videos": "Movies",
    "documents": "Documents",
    "document": "Documents",
    "dokumen": "Documents",
    "whatsapp images": "WhatsApp",
    "whatsapp video": "WhatsApp",
    "telegram images": "Telegram",
    "telegram video": "Telegram",
    "preview": "Previews",
    "previews": "Previews",
}
FAVORITE_TOKENS = (
    "favorite",
    "favorites",
    "favourite",
    "favourites",
    "favorit",
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".3gp", ".avi", ".m4v"}


@dataclass(frozen=True, slots=True)
class GalleryRecord:
    file_id: str
    path: str
    source: str
    mime: str
    sha256: str
    display_name: str
    album_key: str
    album_label: str
    is_favorite: bool
    recency_ts: float
    touch_ts: float
    added_ts: float
    taken_ts: float
    preview_path: str


def album_label(raw: str) -> str:
    cleaned = " ".join(raw.replace("_", " ").replace("-", " ").split()).strip()
    if not cleaned:
        return "Lainnya"
    aliased = ALBUM_ALIASES.get(cleaned.casefold())
    if aliased:
        return aliased
    return cleaned[:1].upper() + cleaned[1:]


def album_key(label: str) -> str:
    slug = _SLUG_RE.sub("-", label.casefold()).strip("-")
    return (slug or "lainnya")[:64]


def album_leaf(directory_hint: str | None, path: str, source: str) -> str:
    hint = (directory_hint or "").replace("\\", "/").strip().strip("/")
    if hint:
        leaf = hint.split("/")[-1].strip()
        if leaf and leaf.casefold() not in GENERIC_LEAVES:
            return album_label(leaf)
    parts = Path(str(path).replace("\\", "/")).parts
    for part in reversed(parts[:-1]):
        if part.startswith("_") or part.casefold() in GENERIC_LEAVES:
            continue
        if part.casefold() in {"gallery", "video", "documents", "media_image", "media_video"}:
            continue
        return album_label(part)
    return album_label(str(source or "lainnya").replace("_", " "))


def looks_favorite(*parts: str | None) -> bool:
    haystack = " ".join(part or "" for part in parts).casefold()
    return any(token in haystack for token in FAVORITE_TOKENS)


def is_gallery_media(*, source: str, mime: str, path: str, role: str | None) -> bool:
    if (source or "").lower() in EXCLUDE_SOURCES:
        return False
    if (role or "").lower() in EXCLUDE_ROLES:
        return False
    mime_l = (mime or "").lower()
    ext = Path(path).suffix.lower()
    if mime_l.startswith("image/") or ext in IMAGE_EXTS:
        return True
    if mime_l.startswith("video/") or ext in VIDEO_EXTS:
        return True
    return False


def _parse_epoch(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            return number / 1000.0
        return number
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def frequent_rank(record: GalleryRecord) -> tuple[int, float]:
    hint = " ".join([record.path, record.album_label, record.display_name]).casefold()
    bonus = 0
    if "screenshot" in hint:
        bonus += 4
    if "dcim" in hint or "camera" in hint:
        bonus += 4
    if "download" in hint or "document" in hint or "unduhan" in hint:
        bonus += 3
    if record.touch_ts > record.added_ts + 60:
        bonus += 2
    return (bonus, record.touch_ts)


def gallery_meta_from_canonical(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    directory_hint = metadata.get("directory_hint")
    display_name = metadata.get("display_name")
    path_hint = str(directory_hint or display_name or "")
    favorite = bool(metadata.get("is_favorite")) or looks_favorite(
        str(directory_hint or ""),
        str(display_name or ""),
        path_hint,
    )
    return {
        "directory_hint": directory_hint if isinstance(directory_hint, str) else None,
        "display_name": display_name if isinstance(display_name, str) else None,
        "is_favorite": favorite,
        "date_added": metadata.get("date_added"),
        "date_modified": metadata.get("date_modified"),
        "date_taken": metadata.get("date_taken") or metadata.get("capture_time"),
        "album": album_leaf(
            directory_hint if isinstance(directory_hint, str) else None,
            str(display_name or ""),
            str(payload.get("source_kind") or ""),
        ),
    }


def _record_from_row(row: Any) -> GalleryRecord | None:
    meta: dict[str, Any]
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    path = str(row["path"] or "")
    source = str(row["source"] or "")
    mime = str(row["mime"] or "")
    role = str(meta.get("crawl_artifact_role") or "")
    if not is_gallery_media(source=source, mime=mime, path=path, role=role):
        return None
    sha256 = str(row["sha256"] or "")
    identity = sha256 or str(row["id"])
    if not identity:
        return None
    directory_hint = meta.get("directory_hint") if isinstance(meta.get("directory_hint"), str) else None
    display_name = (
        meta.get("display_name")
        if isinstance(meta.get("display_name"), str) and meta.get("display_name")
        else Path(path).name
    )
    label = album_leaf(directory_hint, path, source)
    if isinstance(meta.get("album"), str) and meta["album"].strip():
        label = album_label(meta["album"])
    added = _parse_epoch(meta.get("date_added"))
    modified = _parse_epoch(meta.get("date_modified"))
    taken = _parse_epoch(meta.get("date_taken") or meta.get("capture_time"))
    captured = _parse_epoch(meta.get("captured_at"))
    recency = taken or added or modified or captured
    touch = modified or added or recency
    favorite = bool(meta.get("is_favorite")) or looks_favorite(directory_hint, display_name, path, label)
    return GalleryRecord(
        file_id=str(row["id"]),
        path=path,
        source=source,
        mime=mime,
        sha256=sha256,
        display_name=str(display_name),
        album_key=album_key(label),
        album_label=label,
        is_favorite=favorite,
        recency_ts=recency,
        touch_ts=touch,
        added_ts=added or captured,
        taken_ts=taken or captured,
        preview_path=path,
    )


def _dedupe(records: list[GalleryRecord]) -> list[GalleryRecord]:
    seen: set[str] = set()
    out: list[GalleryRecord] = []
    for record in records:
        key = record.sha256 or record.file_id
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def _in_time_window(record: GalleryRecord, not_before_epoch: float) -> bool:
    if record.is_favorite:
        return True
    stamps = [
        stamp
        for stamp in (record.taken_ts, record.added_ts, record.touch_ts, record.recency_ts)
        if stamp > 0
    ]
    if not stamps:
        return True
    return any(stamp >= not_before_epoch for stamp in stamps)


def _parse_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _session_not_before(session_id: str, mode: AcquisitionMode) -> float:
    row = await db.fetchone("SELECT created_at FROM sessions WHERE id = ?", (session_id,))
    reference = _parse_created_at(row["created_at"] if row else None)
    return build_time_scope(mode, reference=reference).not_before.timestamp()


def _to_item(session_id: str, record: GalleryRecord) -> GalleryItemOut:
    return GalleryItemOut(
        id=record.file_id,
        session_id=session_id,
        file_id=record.file_id,
        source=record.source,
        path=record.path,
        album=record.album_label,
        album_key=record.album_key,
        label=record.display_name,
        mime=record.mime,
        preview_path=record.preview_path,
        captured_at=(
            datetime.fromtimestamp(record.recency_ts, tz=timezone.utc).isoformat()
            if record.recency_ts > 0
            else None
        ),
        favorite=record.is_favorite,
    )


async def _load_records(session_id: str, mode: AcquisitionMode) -> list[GalleryRecord]:
    rows = await db.fetchall(
        """
        SELECT id, source, path, mime, sha256, meta_json
        FROM files
        WHERE session_id = ? AND pull_status = 'pulled'
        """,
        (session_id,),
    )
    not_before = await _session_not_before(session_id, mode)
    records: list[GalleryRecord] = []
    for row in rows:
        record = _record_from_row(row)
        if record is None:
            continue
        if not _in_time_window(record, not_before):
            continue
        records.append(record)
    return _dedupe(records)


def _access_sets(
    records: list[GalleryRecord],
) -> dict[str, list[GalleryRecord]]:
    favorites = [item for item in records if item.is_favorite]
    favorites.sort(key=lambda item: item.recency_ts, reverse=True)
    favorite_keys = {item.sha256 or item.file_id for item in favorites}
    rest = [
        item
        for item in records
        if (item.sha256 or item.file_id) not in favorite_keys
    ]
    recent = sorted(rest, key=lambda item: item.recency_ts, reverse=True)
    newest_cut = max(1, len(recent) // 3) if recent else 0
    newest_keys = {item.sha256 or item.file_id for item in recent[:newest_cut]}
    frequent = [
        item
        for item in rest
        if (item.sha256 or item.file_id) not in newest_keys
    ]
    frequent.sort(key=frequent_rank, reverse=True)
    if not frequent:
        frequent = sorted(rest, key=frequent_rank, reverse=True)
    return {
        ACCESS_FAVORITE: favorites,
        ACCESS_RECENT: recent,
        ACCESS_FREQUENT: frequent,
    }


async def list_albums(session_id: str, mode: AcquisitionMode) -> list[GalleryAlbumOut]:
    records = await _load_records(session_id, mode)
    access = _access_sets(records)
    albums: list[GalleryAlbumOut] = [
        GalleryAlbumOut(
            id=ACCESS_ALL,
            label=ACCESS_LABELS[ACCESS_ALL],
            kind="access",
            count=len(records),
        )
    ]
    albums.extend(
        GalleryAlbumOut(
            id=key,
            label=ACCESS_LABELS[key],
            kind="access",
            count=len(access[key]),
        )
        for key in ACCESS_ORDER
    )
    origin_counts: dict[str, tuple[str, int]] = {}
    for record in records:
        current = origin_counts.get(record.album_key)
        if current is None:
            origin_counts[record.album_key] = (record.album_label, 1)
        else:
            origin_counts[record.album_key] = (current[0], current[1] + 1)
    for key, (label, count) in sorted(
        origin_counts.items(),
        key=lambda item: (-item[1][1], item[1][0].casefold()),
    ):
        if key in RESERVED_ALBUMS:
            continue
        albums.append(
            GalleryAlbumOut(id=key, label=label, kind="album", count=count)
        )
    return albums


def _paginate(items: list[GalleryItemOut], page: int, page_size: int) -> PaginatedGallery:
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    return PaginatedGallery(
        items=items[start : start + page_size],
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


async def list_items(
    session_id: str,
    mode: AcquisitionMode,
    album: str,
    page: int,
    page_size: int,
) -> PaginatedGallery:
    records = await _load_records(session_id, mode)
    key = album.strip().lower()
    if key == ACCESS_ALL:
        selected = sorted(records, key=lambda item: item.recency_ts, reverse=True)
    elif key in ACCESS_ORDER:
        selected = _access_sets(records)[key]
    else:
        selected = [item for item in records if item.album_key == key]
        selected.sort(key=lambda item: item.recency_ts, reverse=True)
    return _paginate(
        [_to_item(session_id, item) for item in selected],
        page,
        page_size,
    )
