from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
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
ACCESS_LIMIT = 10
ACCESS_LABELS = {
    ACCESS_ALL: "Semua",
    ACCESS_FREQUENT: "10 paling sering diakses",
    ACCESS_RECENT: "10 terbaru diakses",
    ACCESS_FAVORITE: "Favorit",
}

RECOVERY_NORMAL = "normal"
RECOVERY_TRASH = "trash"
RECOVERY_DELETED = "recovered_deleted"
RECOVERY_ALBUMS = {
    RECOVERY_TRASH: "Trash",
    RECOVERY_DELETED: "Recovered image",
}
CLASSIFICATION_FILTERS = (
    ("state-normal", "Data normal", RECOVERY_NORMAL),
    ("state-trash", "Trash", RECOVERY_TRASH),
    ("state-recovered", "Recovered image", RECOVERY_DELETED),
)
CLASSIFICATION_BY_KEY = {key: state for key, _label, state in CLASSIFICATION_FILTERS}
ANDROID_DELETED_CLASSIFICATIONS = {
    "source_missing",
    "orphan_mediastore_id",
    "orphan_disk_cache",
    "unmatched_thumbdata_slot",
}
IOS_DELETED_CLASSIFICATIONS = {
    "photos_thumbnail_cache",
    "ithmb_jpeg_carve",
    "purged_metadata_only",
}

STRUCTURED_SOURCES = {
    "sms",
    "contacts",
    "contact",
    "visible_ui",
    "accessibility_visible_ui",
    "notification",
    "notification_listener",
    "whatsapp",
}
PATH_MAPPED_SOURCES = {
    "gallery",
    "media_image",
    "media_video",
    "media_audio",
    "video",
    "document",
    "documents",
    "ios_hidden",
}
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
    "audio": "Audio",
    "music": "Audio",
    "musik": "Audio",
    "messages": "Pesan",
    "message": "Pesan",
    "sms": "Pesan",
    "contacts": "Kontak",
    "contact": "Kontak",
    "notifications": "Notifikasi",
    "notification": "Notifikasi",
    "social": "Media Sosial",
    "whatsapp images": "WhatsApp",
    "whatsapp video": "WhatsApp",
    "telegram images": "Telegram",
    "telegram video": "Telegram",
    "preview": "Previews",
    "previews": "Previews",
    "email": "Email",
    "emails": "Email",
    "gmail": "Email",
    "mail": "Email",
    "browser history": "Riwayat Browser (lengkap)",
    "riwayat browser": "Riwayat Browser (lengkap)",
}
FAVORITE_TOKENS = (
    "favorite",
    "favorites",
    "favourite",
    "favourites",
    "favorit",
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
SEMANTIC_ALBUMS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "screenshots": "Screenshots",
    "screenshot": "Screenshots",
    "download": "Download",
    "downloads": "Download",
    "unduhan": "Download",
    "dcim": "Camera",
    "camera": "Camera",
    "pictures": "Pictures",
    "movies": "Movies",
    "videos": "Movies",
    "video": "Movies",
    "documents": "Documents",
    "document": "Documents",
    "audio": "Audio",
    "music": "Audio",
    "email": "Email",
    "gmail": "Email",
}
SOURCE_ALBUMS = {
    "sms": "Pesan",
    "contacts": "Kontak",
    "contact": "Kontak",
    "notification": "Notifikasi",
    "notification_listener": "Notifikasi",
    "visible_ui": "Media Sosial",
    "accessibility_visible_ui": "Media Sosial",
    "gallery": "Galeri",
    "media_image": "Pictures",
    "video": "Movies",
    "media_video": "Movies",
    "media_audio": "Audio",
    "documents": "Documents",
    "document": "Documents",
    "email": "Email",
    "gmail": "Email",
    "whatsapp": "WhatsApp",
    "browser_history_full": "Riwayat Browser (lengkap)",
    "browser_history_partial": "Riwayat Browser (sebagian)",
    "recovered_cache": "Pratinjau cache",
    "recovered_trash": "Recovered image",
    "ios_hidden": "Photos tersembunyi",
    "ios_recently_deleted": "Baru dihapus",
    "ios_recovered_cache": "Cache Photos",
    "ios_deleted_metadata": "Jejak hapus Photos",
}
SOURCE_FIRST_ALBUMS = STRUCTURED_SOURCES | {
    "email",
    "gmail",
    "browser_history_full",
    "browser_history_partial",
    "recovered_cache",
    "recovered_trash",
    "ios_hidden",
    "ios_recently_deleted",
    "ios_recovered_cache",
    "ios_deleted_metadata",
}
SOCIAL_PACKAGES = {
    "com.instagram.android": "Instagram",
    "com.instagram.barcelona": "Threads",
    "com.twitter.android": "X",
    "com.facebook.katana": "Facebook",
    "com.whatsapp": "WhatsApp",
    "org.telegram.messenger": "Telegram",
}
SOCIAL_TEXT_ONLY = {"com.twitter.android", "com.facebook.katana"}
INSTAGRAM_PACKAGE = "com.instagram.android"
BROWSER_HISTORY_SOURCES = frozenset({"browser_history_full", "browser_history_partial"})
BROWSER_ALBUM_ORDER = (
    ("browser_history_full", "Riwayat Browser (lengkap)", "riwayat-browser-lengkap"),
    ("browser_history_partial", "Riwayat Browser (sebagian)", "riwayat-browser-sebagian"),
)
BROWSER_ALBUM_KEYS = tuple(item[2] for item in BROWSER_ALBUM_ORDER)
SOCIAL_SCOPE_LABELS = {
    "own_profile": "Profil akun",
    "own_posts": "Postingan akun",
    "own_tweets": "Tweet akun",
    "own_story_archive": "Arsip story",
    "own_comments": "Komentar akun",
    "own_replies": "Balasan akun",
    "own_likes": "Aktivitas suka akun",
}
ACCESS_COUNT_KEYS = (
    "access_count",
    "open_count",
    "interaction_count",
    "times_opened",
)
VIEW_COUNT_KEYS = ("view_count", "views", "zviewcount")
PLAY_COUNT_KEYS = ("play_count", "plays", "zplaycount")
PENDING_VIEW_COUNT_KEYS = ("pending_view_count", "zpendingviewcount")
PENDING_PLAY_COUNT_KEYS = ("pending_play_count", "zpendingplaycount")
LAST_ACCESS_KEYS = (
    "last_accessed_at",
    "last_access_time",
    "last_viewed_at",
    "last_played_at",
    "last_opened_at",
    "accessed_at",
    "viewed_at",
    "played_at",
)
FREQUENT_FILENAME_RE = re.compile(
    r"(?:^|[/\\])(?:fav_)?v(?P<views>\d+)_p(?P<plays>\d+)_",
    re.IGNORECASE,
)


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
    is_flagged: bool
    finding_badges: tuple[str, ...]
    recency_ts: float
    touch_ts: float
    access_ts: float
    access_count: int
    added_ts: float
    taken_ts: float
    preview_path: str | None
    preview_mime: str | None
    preview_text: str | None
    source_path: str
    source_app: str | None
    social_scope: str | None
    presentation: str
    chat: dict[str, Any] | None
    artifact_role: str | None
    recovery_state: str


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


def _semantic_album(directory_hint: str | None, path: str) -> str | None:
    combined = "/".join(part for part in (directory_hint, path) if part)
    parts = [part.strip().casefold() for part in combined.replace("\\", "/").split("/")]
    for needle in ("whatsapp", "telegram"):
        if any(needle in part for part in parts):
            return SEMANTIC_ALBUMS[needle]
    for needle, label in SEMANTIC_ALBUMS.items():
        if needle in parts:
            return label
    return None


def album_leaf(directory_hint: str | None, path: str, source: str) -> str:
    semantic = _semantic_album(directory_hint, path)
    if semantic:
        return semantic
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
    return SOURCE_ALBUMS.get(source.casefold()) or album_label(
        str(source or "lainnya").replace("_", " ")
    )


def looks_favorite(*parts: str | None) -> bool:
    haystack = " ".join(part or "" for part in parts).casefold()
    return any(token in haystack for token in FAVORITE_TOKENS)


def is_gallery_media(
    *,
    source: str,
    mime: str,
    path: str,
    role: str | None,
    source_app: str | None = None,
    is_canonical: bool = False,
    has_source_binary: bool = False,
    has_screenshot: bool = False,
) -> bool:
    """Expose one gallery item per logical record, not transfer companions."""
    from app.services.acquisition import is_agent_self_capture

    if is_agent_self_capture(path):
        return False
    del source, mime
    normalized_role = str(role or "").casefold()
    normalized_app = str(source_app or "").casefold()
    # Media/audio/video/document records contain both canonical metadata and
    # one source binary. The binary is the actual gallery/analyzer item.
    if is_canonical and has_source_binary:
        return False
    # Instagram is visual: show its screenshot attachment(s), not an additional
    # JSON card for the same snapshot. X/Facebook are text-only and must never
    # expose legacy screenshots.
    if is_canonical and normalized_app == INSTAGRAM_PACKAGE and has_screenshot:
        return False
    if normalized_role == "screenshot" and normalized_app in SOCIAL_TEXT_ONLY:
        return False
    return bool(str(path or "").strip())


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


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _compact_preview(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).replace("\x00", " ").split())
    return cleaned[:2000] or None


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def _metadata_maps(meta: dict[str, Any]) -> list[dict[str, Any]]:
    values = [meta]
    for key in ("access_metrics", "usage", "metadata"):
        nested = meta.get(key)
        if isinstance(nested, dict):
            values.append(nested)
    return values


def _access_metrics(meta: dict[str, Any], path: str) -> tuple[int, float]:
    maps = _metadata_maps(meta)
    direct_count = max(
        (_nonnegative_int(value.get(key)) for value in maps for key in ACCESS_COUNT_KEYS),
        default=0,
    )
    views = max(
        (_nonnegative_int(value.get(key)) for value in maps for key in VIEW_COUNT_KEYS),
        default=0,
    )
    views += max(
        (
            _nonnegative_int(value.get(key))
            for value in maps
            for key in PENDING_VIEW_COUNT_KEYS
        ),
        default=0,
    )
    plays = max(
        (_nonnegative_int(value.get(key)) for value in maps for key in PLAY_COUNT_KEYS),
        default=0,
    )
    plays += max(
        (
            _nonnegative_int(value.get(key))
            for value in maps
            for key in PENDING_PLAY_COUNT_KEYS
        ),
        default=0,
    )
    filename_match = FREQUENT_FILENAME_RE.search(path)
    if filename_match:
        views = max(views, _nonnegative_int(filename_match.group("views")))
        plays = max(plays, _nonnegative_int(filename_match.group("plays")))
    accessed = max(
        (_parse_epoch(value.get(key)) for value in maps for key in LAST_ACCESS_KEYS),
        default=0.0,
    )
    return max(direct_count, views + plays), accessed


def _recovery_state(meta: dict[str, Any], source: str, path: str) -> str:
    """Classify recovery provenance without guessing from ordinary filenames."""
    source_l = (source or "").casefold()
    android_class = str(meta.get("recovery_classification") or "").casefold()
    ios_class = str(meta.get("ios_library_classification") or "").casefold()

    if android_class == "trash_resident" or source_l == "ios_recently_deleted":
        return RECOVERY_TRASH
    if (
        android_class in ANDROID_DELETED_CLASSIFICATIONS
        or ios_class in IOS_DELETED_CLASSIFICATIONS
        or source_l in {"ios_recovered_cache", "ios_deleted_metadata"}
    ):
        return RECOVERY_DELETED

    # Backward-compatible fallback for older manifests that did not persist a
    # classification. Only the owned recovery namespace is considered here;
    # an unrelated normal path containing the word "trash" stays normal.
    parts = [part.casefold() for part in Path(path.replace("\\", "/")).parts]
    if source_l == "recovered_trash":
        if len(parts) >= 2 and parts[0] == "recovered_trash":
            if parts[1] == "trash":
                return RECOVERY_TRASH
            if parts[1] == "previews":
                return RECOVERY_DELETED
        # Unknown recovery artifacts must not inflate the current Trash count.
        return RECOVERY_DELETED
    return RECOVERY_NORMAL


def _resolved_album(
    *,
    source: str,
    source_app: str | None,
    directory_hint: str | None,
    path: str,
    metadata_album: Any,
    recovery_state: str,
) -> str:
    recovery_album = RECOVERY_ALBUMS.get(recovery_state)
    if recovery_album:
        return recovery_album
    platform = SOCIAL_PACKAGES.get((source_app or "").casefold())
    if platform:
        resolved = platform
    else:
        source_l = source.casefold()
        # Browser history must stay in its dedicated albums even if a path
        # fragment looks like Download/Camera. Other SOURCE_FIRST types keep
        # the previous semantic-first order so iOS/email grouping is unchanged.
        if source_l in BROWSER_HISTORY_SOURCES and source_l in SOURCE_ALBUMS:
            resolved = SOURCE_ALBUMS[source_l]
        else:
            semantic = _semantic_album(directory_hint, path)
            if semantic:
                resolved = semantic
            elif source_l in SOURCE_FIRST_ALBUMS and source_l in SOURCE_ALBUMS:
                resolved = SOURCE_ALBUMS[source_l]
            elif isinstance(metadata_album, str) and metadata_album.strip():
                resolved = album_label(metadata_album)
            else:
                resolved = album_leaf(directory_hint, path, source)
    special_keys = {album_key(value) for value in RECOVERY_ALBUMS.values()}
    if album_key(resolved) in special_keys:
        return f"Folder {resolved} (normal)"
    return resolved


def _origin_path(directory_hint: str | None, display_name: str, path: str) -> str:
    hint = (directory_hint or "").replace("\\", "/").strip().rstrip("/")
    name = Path(display_name.replace("\\", "/")).name.strip()
    if not hint:
        return path
    if name and Path(hint).name.casefold() != name.casefold():
        return f"{hint}/{name}"
    return hint


def _social_display_name(
    source_app: str,
    social_scope: str | None,
    preview_text: str | None,
    fallback: str,
) -> str:
    scope = SOCIAL_SCOPE_LABELS.get(social_scope or "", "Data akun")
    snippet = _compact_preview(preview_text)
    if snippet:
        return f"{scope} · {snippet[:96]}"
    fallback_name = Path(fallback).name
    if fallback_name.endswith((".json", ".siksik-record.json")):
        return scope
    return fallback_name or f"{SOCIAL_PACKAGES.get(source_app, 'Media sosial')} · {scope}"


def frequent_rank(record: GalleryRecord) -> tuple[int, float, float, str]:
    return (
        record.access_count,
        record.access_ts,
        record.recency_ts,
        record.file_id,
    )


def _fallback_frequent_rank(record: GalleryRecord) -> tuple[int, float, float, str]:
    """Rank activity when the source platform exposes no open/view counter."""
    hint = " ".join(
        (record.source_path, record.path, record.album_label, record.display_name)
    ).casefold()
    activity = 0
    if "screenshot" in hint:
        activity += 4
    if "dcim" in hint or "camera" in hint:
        activity += 4
    if any(token in hint for token in ("download", "document", "unduhan")):
        activity += 3
    if record.touch_ts > record.added_ts + 60:
        activity += 2
    return (activity, record.touch_ts, record.recency_ts, record.file_id)


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
    from app.acquisition.source_app_hints import inferred_album_label

    inferred = inferred_album_label(
        directory_hint=directory_hint if isinstance(directory_hint, str) else None,
        display_name=display_name if isinstance(display_name, str) else None,
        path=path_hint,
    )
    result = {
        "directory_hint": directory_hint if isinstance(directory_hint, str) else None,
        "display_name": display_name if isinstance(display_name, str) else None,
        "is_favorite": favorite,
        "date_added": metadata.get("date_added"),
        "date_modified": metadata.get("date_modified"),
        "date_taken": metadata.get("date_taken") or metadata.get("capture_time"),
        "album": inferred
        or album_leaf(
            directory_hint if isinstance(directory_hint, str) else None,
            str(display_name or ""),
            str(payload.get("source_kind") or ""),
        ),
    }
    for key in (
        *ACCESS_COUNT_KEYS,
        *VIEW_COUNT_KEYS,
        *PLAY_COUNT_KEYS,
        *PENDING_VIEW_COUNT_KEYS,
        *PENDING_PLAY_COUNT_KEYS,
        *LAST_ACCESS_KEYS,
    ):
        if key in metadata:
            result[key] = metadata[key]
    return result


def _record_from_row(row: Any) -> GalleryRecord | None:
    meta: dict[str, Any]
    try:
        meta = json.loads(_row_get(row, "meta_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    path = str(_row_get(row, "path", "") or "")
    source = str(_row_get(row, "source", "") or "")
    mime = str(_row_get(row, "mime", "") or "")
    role = str(
        _row_get(row, "resolved_role")
        or meta.get("crawl_artifact_role")
        or meta.get("artifact_role")
        or ""
    )
    source_app = _optional_text(_row_get(row, "crawl_source_app")) or _optional_text(
        meta.get("source_app")
    )
    social_scope = _optional_text(_row_get(row, "crawl_social_scope")) or _optional_text(
        meta.get("social_scope")
    )
    is_canonical = bool(_row_get(row, "is_canonical", 0))
    has_source_binary = bool(_row_get(row, "has_source_binary", 0))
    has_screenshot = bool(_row_get(row, "has_screenshot", 0))
    if not is_gallery_media(
        source=source,
        mime=mime,
        path=path,
        role=role,
        source_app=source_app,
        is_canonical=is_canonical,
        has_source_binary=has_source_binary,
        has_screenshot=has_screenshot,
    ):
        return None
    sha256 = str(_row_get(row, "sha256", "") or "")
    identity = str(_row_get(row, "id", "") or "")
    if not identity:
        return None
    directory_hint = _optional_text(meta.get("directory_hint"))
    display_name = (
        meta.get("display_name")
        if isinstance(meta.get("display_name"), str) and meta.get("display_name")
        else meta.get("ios_original_filename")
        if isinstance(meta.get("ios_original_filename"), str)
        and meta.get("ios_original_filename")
        else Path(path).name
    )
    from app.services.acquisition import is_agent_self_capture

    if meta.get("acquisition_self_capture") or is_agent_self_capture(
        path, str(display_name)
    ):
        return None
    if not source_app:
        from app.acquisition.source_app_hints import infer_source_app

        source_app = infer_source_app(
            directory_hint=directory_hint,
            display_name=str(display_name),
            path=path,
        )
    preview_text = _compact_preview(_row_get(row, "preview_text")) or _compact_preview(
        meta.get("preview_text") or meta.get("normalized_text")
    )
    if preview_text is None and source.casefold() in {"email", "gmail"} | BROWSER_HISTORY_SOURCES:
        preview_text = _compact_preview(display_name)
    recovery_state = _recovery_state(meta, source, path)
    label = _resolved_album(
        source=source,
        source_app=source_app,
        directory_hint=directory_hint,
        path=path,
        metadata_album=meta.get("album"),
        recovery_state=recovery_state,
    )
    added = _parse_epoch(meta.get("date_added") or meta.get("source_created_at"))
    modified = _parse_epoch(meta.get("date_modified") or meta.get("source_modified_at"))
    taken = _parse_epoch(meta.get("date_taken") or meta.get("capture_time"))
    captured = _parse_epoch(meta.get("captured_at") or meta.get("observed_at"))
    recency = max(taken, added, modified, captured)
    touch = max(modified, added, recency)
    access_count, explicit_access = _access_metrics(meta, path)
    access_ts = explicit_access
    favorite = bool(meta.get("is_favorite") or meta.get("message_starred")) or looks_favorite(
        directory_hint,
        str(display_name),
        path,
        label,
    )
    source_app_l = (source_app or "").casefold()
    source_l = source.casefold()
    is_whatsapp_message = bool(
        source_l == "whatsapp"
        and _optional_text(meta.get("conversation_id"))
        and _optional_text(meta.get("message_id"))
    )
    is_social_crawl = bool(
        source_app
        and not meta.get("source_app_inferred")
        and (
            social_scope
            or meta.get("crawl_record_id")
            or source_l in {"visible_ui", "accessibility_visible_ui"}
        )
    )
    presentation = (
        "chat"
        if is_whatsapp_message
        else "text"
        if (is_social_crawl and source_app_l in SOCIAL_TEXT_ONLY)
        or source_l in BROWSER_HISTORY_SOURCES
        else "visual"
        if is_social_crawl and source_app_l == INSTAGRAM_PACKAGE
        else "file"
    )
    if is_social_crawl:
        display_name = _social_display_name(
            source_app,
            social_scope,
            preview_text,
            str(display_name),
        )
    elif is_whatsapp_message:
        display_name = (
            _optional_text(meta.get("conversation_name"))
            or _optional_text(meta.get("display_name"))
            or "Percakapan WhatsApp"
        )
    source_locator = _optional_text(_row_get(row, "source_locator"))
    preview_path = path
    preview_mime = mime
    if role.casefold() == "email_metadata":
        preview_path = _optional_text(_row_get(row, "canonical_preview_path"))
        preview_mime = "text/html" if preview_path else None
    elif source_app_l in SOCIAL_TEXT_ONLY and role.casefold() == "screenshot":
        # A legacy/invalid X or Facebook screenshot remains accounted for as a
        # pulled file, but it can only render the canonical text record.
        preview_path = _optional_text(_row_get(row, "canonical_preview_path"))
        preview_mime = (
            "application/vnd.siksik.crawl-record+json" if preview_path else None
        )
    elif presentation == "visual" and is_canonical and role.casefold() != "screenshot":
        preview_path = _optional_text(_row_get(row, "visual_preview_path"))
        preview_mime = _optional_text(_row_get(row, "visual_preview_mime"))
    origin_path = _origin_path(directory_hint, str(display_name), path)
    if is_social_crawl:
        source_path = source_locator or origin_path
    elif is_whatsapp_message:
        source_path = f"WhatsApp/{display_name}"
    elif directory_hint:
        source_path = origin_path
    elif recovery_state != RECOVERY_NORMAL:
        source_path = source_locator or path
    elif source.casefold() in PATH_MAPPED_SOURCES and Path(str(display_name)).name:
        source_path = f"{label}/{Path(str(display_name)).name}"
    else:
        source_path = source_locator or origin_path
    chat = None
    if is_whatsapp_message:
        conversation_id = _optional_text(meta.get("conversation_id"))
        message_id = _optional_text(meta.get("message_id"))
        direction = _optional_text(meta.get("message_direction"))
        if conversation_id and message_id and direction in {"IN", "OUT"}:
            chat = {
                "conversation_id": conversation_id,
                "conversation_name": str(display_name),
                "conversation_address": _optional_text(
                    meta.get("conversation_address")
                ),
                "conversation_type": (
                    "group" if meta.get("conversation_type") == "group" else "chat"
                ),
                "message_id": message_id,
                "direction": direction,
                "sender": _optional_text(meta.get("message_sender")),
                "message_type": _optional_text(meta.get("message_type")) or "text",
                "text": _optional_text(meta.get("message_text")) or preview_text,
                "timestamp": _optional_text(meta.get("message_timestamp")),
                "quoted_text": _compact_preview(meta.get("quoted_text")),
                "starred": bool(meta.get("message_starred")),
                "revoked": bool(meta.get("message_revoked")),
                "forwarded": _nonnegative_int(meta.get("message_forward_score")) > 0,
                "edited_at": _optional_text(meta.get("message_edited_at")),
            }
    return GalleryRecord(
        file_id=identity,
        path=path,
        source=source,
        mime=mime,
        sha256=sha256,
        display_name=str(display_name),
        album_key=album_key(label),
        album_label=label,
        is_favorite=favorite,
        is_flagged=bool(_row_get(row, "is_flagged", 0)),
        finding_badges=tuple(_row_get(row, "finding_badges", ()) or ()),
        recency_ts=recency,
        touch_ts=touch,
        access_ts=access_ts,
        access_count=access_count,
        added_ts=added or captured,
        taken_ts=taken or captured,
        preview_path=preview_path,
        preview_mime=preview_mime if preview_path else None,
        preview_text=preview_text,
        source_path=source_path,
        source_app=source_app,
        social_scope=social_scope,
        presentation=presentation,
        chat=chat,
        artifact_role=role or None,
        recovery_state=recovery_state,
    )


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
        preview_mime=record.preview_mime,
        source_path=record.source_path,
        source_app=record.source_app,
        social_scope=record.social_scope,
        presentation=record.presentation,
        chat=record.chat,
        artifact_role=record.artifact_role,
        recovery_state=record.recovery_state,
        captured_at=(
            datetime.fromtimestamp(record.recency_ts, tz=timezone.utc).isoformat()
            if record.recency_ts > 0
            else None
        ),
        accessed_at=(
            datetime.fromtimestamp(record.access_ts, tz=timezone.utc).isoformat()
            if record.access_ts > 0
            else None
        ),
        access_count=record.access_count,
        favorite=record.is_favorite,
        flagged=record.is_flagged,
        finding_badges=list(record.finding_badges),
        preview_text=record.preview_text,
    )


async def _unindexed_recovery_rows(
    session_id: str,
    indexed_paths: set[str],
) -> list[dict[str, Any]]:
    """Expose validated recovery payloads even for sessions indexed by old code."""
    from app.acquisition.android_recovery.service import (
        detect_recovery_mime_type,
        recovery_metadata,
    )
    from app.acquisition.file_identity import stable_file_id

    staging = settings.staging_dir / session_id

    def load() -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for relative, artifact in sorted(recovery_metadata(staging).items()):
            if relative in indexed_paths:
                continue
            target = staging / relative
            mime = detect_recovery_mime_type(target, artifact.mime_type)
            display_suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "image/heic": ".heic",
                "video/mp4": ".mp4",
            }.get(mime, target.suffix.lower())
            captured_at = datetime.fromtimestamp(
                target.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
            meta = {
                "ext": target.suffix.lower(),
                "captured_at": captured_at,
                "date_source": "recovery_manifest_staging",
                "display_name": f"{artifact.candidate_id}{display_suffix}",
                "acquisition_method": "android_recovery_v1",
                "recovery_candidate_id": artifact.candidate_id,
                "recovery_source": artifact.source,
                "recovery_classification": artifact.classification,
                "recovery_confidence": artifact.confidence,
                "recovery_expires_epoch_s": artifact.expires_epoch_s,
            }
            output.append(
                {
                    "id": stable_file_id(session_id, relative),
                    "source": "recovered_trash",
                    "path": relative,
                    "mime": mime,
                    "sha256": artifact.sha256,
                    "meta_json": json.dumps(
                        meta,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        return output

    return load()


async def _load_records(session_id: str, mode: AcquisitionMode) -> list[GalleryRecord]:
    # Acquisition owns the QUICK/FULL time scope. Reapplying it here used to hide
    # valid transferred records (including recovery and explicitly selected data).
    del mode
    rows = list(
        await db.fetchall(
            """
            SELECT id, source, path, mime, sha256, meta_json
            FROM files
            WHERE session_id = ? AND pull_status = 'pulled'
            ORDER BY path, id
            """,
            (session_id,),
        )
    )
    indexed_paths = {str(row["path"]) for row in rows}
    rows.extend(await _unindexed_recovery_rows(session_id, indexed_paths))
    crawl_rows = await db.fetchall(
        """
        SELECT
            cr.record_id,
            cr.crawl_id,
            cr.source_app,
            cr.social_scope,
            cr.normalized_text,
            cr.canonical_json,
            cr.canonical_path,
            cr.ingested_at,
            e.ocr_text
        FROM crawl_records cr
        LEFT JOIN social_snapshot_enrichments e
          ON e.crawl_id = cr.crawl_id AND e.record_id = cr.record_id
        WHERE cr.session_id = ?
        ORDER BY cr.ingested_at, cr.record_id
        """,
        (session_id,),
    )
    artifact_rows = await db.fetchall(
        """
        SELECT record_id, role, mime_type, relative_path
        FROM crawl_artifacts
        WHERE session_id = ? AND verified = 1
        ORDER BY record_id, relative_path
        """,
        (session_id,),
    )
    flagged_rows = await db.fetchall(
        "SELECT DISTINCT file_id, category FROM findings WHERE session_id = ?",
        (session_id,),
    )

    pulled_paths = {str(row["path"]) for row in rows}
    flagged_ids = {str(row["file_id"]) for row in flagged_rows}
    from app.services.content_policy import gallery_badge

    badges_by_file: dict[str, set[str]] = {}
    for finding in flagged_rows:
        badge = gallery_badge(str(_row_get(finding, "category", "") or ""))
        if badge:
            badges_by_file.setdefault(str(finding["file_id"]), set()).add(badge)
    crawl_by_record: dict[str, Any] = {}
    crawl_by_path: dict[str, Any] = {}
    crawl_by_companion: dict[str, Any] = {}
    for crawl in crawl_rows:
        record_id = str(crawl["record_id"])
        crawl_by_record[record_id] = crawl
        canonical_path = str(crawl["canonical_path"] or "")
        if canonical_path:
            crawl_by_path[canonical_path] = crawl
            canonical = Path(canonical_path.replace("\\", "/"))
            crawl_by_companion[(canonical.parent / canonical.stem).as_posix()] = crawl
    artifact_roles: dict[str, set[str]] = {}
    artifact_by_path: dict[str, Any] = {}
    visual_artifacts: dict[str, list[tuple[str, str | None]]] = {}
    for artifact in artifact_rows:
        relative_path = str(artifact["relative_path"] or "")
        if relative_path not in pulled_paths:
            continue
        record_id = str(artifact["record_id"])
        role = str(artifact["role"] or "")
        artifact_roles.setdefault(record_id, set()).add(role)
        artifact_by_path[relative_path] = artifact
        if role == "screenshot":
            visual_artifacts.setdefault(record_id, []).append(
                (relative_path, _optional_text(_row_get(artifact, "mime_type")))
            )

    file_links: dict[str, tuple[dict[str, Any], Any | None, str | None, Any | None]] = {}
    for row in rows:
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        path = str(row["path"] or "")
        artifact = artifact_by_path.get(path)
        metadata_record_id = _optional_text(meta.get("crawl_record_id")) or _optional_text(
            meta.get("record_id")
        )
        artifact_record_id = (
            _optional_text(_row_get(artifact, "record_id")) if artifact is not None else None
        )
        linked_hint = metadata_record_id or artifact_record_id
        crawl = crawl_by_record.get(linked_hint) if linked_hint else crawl_by_path.get(path)
        if crawl is None and str(row["source"] or "").casefold() in {"email", "gmail"}:
            companion = Path(path.replace("\\", "/"))
            crawl = crawl_by_companion.get((companion.parent / companion.stem).as_posix())
        linked_record_id = str(crawl["record_id"]) if crawl is not None else metadata_record_id
        if linked_record_id is None:
            linked_record_id = artifact_record_id
        file_links[str(row["id"])] = (meta, crawl, linked_record_id, artifact)
    flagged_record_ids = {
        linked_record_id
        for file_id, (_meta, _crawl, linked_record_id, _artifact) in file_links.items()
        if file_id in flagged_ids and linked_record_id
    }
    records: list[GalleryRecord] = []
    for row in rows:
        meta, crawl, linked_record_id, artifact = file_links[str(row["id"])]
        roles = artifact_roles.get(linked_record_id or "", set())
        source_locator: str | None = None
        preview_text: str | None = None
        canonical_path: str | None = None
        if crawl is not None:
            try:
                canonical = json.loads(crawl["canonical_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                canonical = {}
            source_locator = _optional_text(canonical.get("source_locator"))
            preview_text = _optional_text(crawl["normalized_text"]) or _optional_text(
                crawl["ocr_text"]
            )
            canonical_path = _optional_text(crawl["canonical_path"])
            canonical_gallery_meta = gallery_meta_from_canonical(canonical)
            for name, value in canonical_gallery_meta.items():
                if value is not None and meta.get(name) in {None, ""}:
                    meta[name] = value
        resolved_role = _optional_text(meta.get("crawl_artifact_role"))
        if resolved_role is None and artifact is not None:
            resolved_role = _optional_text(_row_get(artifact, "role"))
        if (
            resolved_role is None
            and crawl is not None
            and str(row["source"] or "").casefold() in {"email", "gmail"}
            and str(row["mime"] or "").casefold() == "application/json"
        ):
            resolved_role = "email_metadata"
        visual_preview = next(iter(visual_artifacts.get(linked_record_id or "", [])), None)
        enriched = dict(row)
        enriched["meta_json"] = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        enriched.update(
            {
                "preview_text": preview_text,
                "crawl_source_app": crawl["source_app"] if crawl is not None else None,
                "crawl_social_scope": crawl["social_scope"] if crawl is not None else None,
                "source_locator": source_locator,
                "is_canonical": bool(
                    crawl is not None and str(crawl["canonical_path"] or "") == str(row["path"])
                ),
                "has_source_binary": "source_binary" in roles,
                "has_screenshot": "screenshot" in roles,
                "resolved_role": resolved_role,
                "canonical_preview_path": (
                    canonical_path if canonical_path in pulled_paths else None
                ),
                "visual_preview_path": visual_preview[0] if visual_preview else None,
                "visual_preview_mime": visual_preview[1] if visual_preview else None,
                "is_flagged": (
                    str(row["id"]) in flagged_ids
                    or linked_record_id in flagged_record_ids
                ),
                # Category badges deliberately use the exact session/file
                # finding join.  Existing linked-record flag propagation is
                # left untouched, but a badge must not leak to a companion
                # artifact that was not itself classified in this session.
                "finding_badges": sorted(
                    badges_by_file.get(str(row["id"]), set())
                ),
            }
        )
        record = _record_from_row(enriched)
        if record is None:
            continue
        records.append(record)
    return records


def _access_sets(
    records: list[GalleryRecord],
) -> dict[str, list[GalleryRecord]]:
    favorites = [item for item in records if item.is_favorite]
    favorites.sort(
        key=lambda item: (item.recency_ts, item.access_ts, item.file_id),
        reverse=True,
    )
    explicit_recent = sorted(
        (item for item in records if item.access_ts > 0),
        key=lambda item: (item.access_ts, item.recency_ts, item.file_id),
        reverse=True,
    )
    recent_ids = {item.file_id for item in explicit_recent}
    inferred_recent = sorted(
        (item for item in records if item.file_id not in recent_ids),
        key=lambda item: (item.touch_ts, item.recency_ts, item.file_id),
        reverse=True,
    )
    recent = (explicit_recent + inferred_recent)[:ACCESS_LIMIT]

    explicit_frequent = sorted(
        (item for item in records if item.access_count > 0),
        key=frequent_rank,
        reverse=True,
    )
    frequent_ids = {item.file_id for item in explicit_frequent}
    inferred_frequent = sorted(
        (item for item in records if item.file_id not in frequent_ids),
        key=_fallback_frequent_rank,
        reverse=True,
    )
    frequent = (explicit_frequent + inferred_frequent)[:ACCESS_LIMIT]
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
    state_counts = {
        state: sum(record.recovery_state == state for record in records)
        for _key, _label, state in CLASSIFICATION_FILTERS
    }
    albums.extend(
        GalleryAlbumOut(
            id=key,
            label=label,
            kind="classification",
            count=state_counts[state],
        )
        for key, label, state in CLASSIFICATION_FILTERS
    )
    origin_counts: dict[str, tuple[str, int]] = {}
    for record in records:
        current = origin_counts.get(record.album_key)
        if current is None:
            origin_counts[record.album_key] = (record.album_label, 1)
        else:
            origin_counts[record.album_key] = (current[0], current[1] + 1)
    for _source, label, key in BROWSER_ALBUM_ORDER:
        count = origin_counts.get(key, (label, 0))[1]
        if count <= 0:
            continue
        albums.append(GalleryAlbumOut(id=key, label=label, kind="album", count=count))
    reserved_origin = set(RESERVED_ALBUMS) | {
        album_key(value) for value in RECOVERY_ALBUMS.values()
    } | set(BROWSER_ALBUM_KEYS)
    for key, (label, count) in sorted(
        origin_counts.items(),
        key=lambda item: (-item[1][1], item[1][0].casefold()),
    ):
        if key in reserved_origin:
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
    elif key in CLASSIFICATION_BY_KEY:
        state = CLASSIFICATION_BY_KEY[key]
        selected = [item for item in records if item.recovery_state == state]
        selected.sort(key=lambda item: item.recency_ts, reverse=True)
    else:
        selected = [item for item in records if item.album_key == key]
        selected.sort(key=lambda item: item.recency_ts, reverse=True)
    return _paginate(
        [_to_item(session_id, item) for item in selected],
        page,
        page_size,
    )
