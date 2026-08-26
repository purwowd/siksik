from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from app.core.branding import crawl_record_filename_mime

logger = logging.getLogger("siksik.acquisition.media_types")

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".vcard", ".vcf"}
DOC_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".ods",
    ".ppt",
    ".pptx",
    ".pages",
    ".numbers",
    ".key",
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".imgmeta"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".vidmeta"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr"}
CHAT_HINTS = ("whatsapp", "telegram", "wa-", "msgstore", "chat")

_JUNK_BASENAMES = frozenset(
    {
        ".nomedia",
        ".database_uuid",
        ".ds_store",
        "thumbs.db",
        "desktop.ini",
        ".thumbnails",
    }
)
_MEDIA_EXT = IMG_EXT | VID_EXT | AUDIO_EXT | TEXT_EXT | DOC_EXT
_AGENT_SELF_CAPTURE_STEMS = frozenset({"sadt_shot", "satria_shot", "siksik_shot"})


def looks_favorite_path(path_str: str) -> bool:
    value = path_str.casefold()
    return any(token in value for token in ("favorite", "favourite", "favorit"))


def is_agent_self_capture(
    path_str: str | None = None,
    display_name: str | None = None,
) -> bool:
    """Screenshots of the SATRIA console captured during acquisition."""
    for raw in (path_str, display_name):
        if not raw:
            continue
        stem = Path(str(raw).replace("\\", "/")).stem.casefold()
        if stem in _AGENT_SELF_CAPTURE_STEMS:
            return True
    return False


def _is_junk_media_path(path_str: str) -> bool:
    """Skip hidden/junk yang sering ikut saat find pada Movies/Download."""
    name = Path(path_str).name
    low = name.lower()
    if low in _JUNK_BASENAMES:
        return True
    if name.startswith("."):
        return True
    ext = Path(path_str).suffix.lower()
    if not ext or ext not in _MEDIA_EXT:
        return True
    return False


def _classify_source(path_str: str) -> str:
    low = path_str.lower().replace("\\", "/")
    ext = Path(path_str).suffix.lower()
    if ext in VID_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMG_EXT:
        if any(x in low for x in ("whatsapp", "/wa/")):
            return "whatsapp"
        if "telegram" in low:
            return "telegram"
        return "gallery"
    if "whatsapp" in low or "/wa/" in low:
        return "whatsapp"
    if "telegram" in low:
        return "telegram"
    if any(x in low for x in ("dcim", "camera", "picture", "gallery", "img_")):
        return "gallery"
    if any(x in low for x in ("document", "download", "pdf", "doc")):
        return "documents"
    if any(x in low for x in ("movie", "video")):
        return "video"
    return "other"


def guess_mime(path: Path) -> str:
    crawl_mime = crawl_record_filename_mime(path.name)
    if crawl_mime:
        return crawl_mime
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = path.suffix.lower()
    if ext in IMG_EXT:
        return "image/jpeg"
    if ext in VID_EXT:
        return "video/mp4"
    if ext in AUDIO_EXT:
        return "audio/mpeg"
    if ext in TEXT_EXT or ext in DOC_EXT:
        return "text/plain"
    return "application/octet-stream"


def _zip_skip(name: str) -> bool:
    low = name.replace("\\", "/").lower()
    if "__macosx" in low.split("/"):
        return True
    return _is_junk_media_path(name)


def _bucket_for_file(name: str) -> str:
    ext = Path(name).suffix.lower()
    low = name.lower()
    if ext in {".eml", ".msg"} or "email" in low or "gmail" in low:
        return "email"
    if ext in VID_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMG_EXT:
        return "gallery"
    if ext in TEXT_EXT | DOC_EXT:
        return "documents"
    source = _classify_source(name)
    if source in {"gallery", "video", "audio", "documents", "whatsapp", "telegram", "email"}:
        return "gallery" if source in {"whatsapp", "telegram"} else source
    return "other"
