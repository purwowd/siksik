from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.device_identity import (
    hints_from_document,
    merge_device_identity_hints,
)
from app.acquisition.social_ocr import repair_ocr_link_text
from app.acquisition.source_app_hints import SOCIAL_PACKAGE_LABELS, infer_source_app
from app.core.branding import PRODUCT_FULL_NAME, PRODUCT_NAME, PRODUCT_TAGLINE
from app.core.config import settings
from app.core.db import db

SOCIAL_PACKAGES = dict(SOCIAL_PACKAGE_LABELS)
SOCIAL_SCOPES = {
    "own_profile": "Profil akun",
    "own_posts": "Postingan akun",
    "own_tweets": "Tweet akun",
    "own_story_archive": "Arsip story",
    "own_comments": "Komentar akun",
    "own_replies": "Balasan akun",
    "device_media": "Media di perangkat",
}
MAX_SOCIAL_REPORT_ITEMS = 500
MAX_SOCIAL_PREVIEW_CHARS = 2_000
MAX_WHATSAPP_REPORT_MESSAGES = 500


def canonical_report_digest(report: dict) -> str:
    """Stable SHA-256 over the decision payload (not timestamps)."""
    session = report.get("session") if isinstance(report.get("session"), dict) else {}
    body = {
        "session_id": session.get("id"),
        "recommendation": session.get("recommendation"),
        "participant": session.get("participant"),
        "findings": report.get("findings"),
        "breakdown": report.get("breakdown"),
        "metrics": {
            key: value
            for key, value in (report.get("metrics") or {}).items()
            if key not in {"timing", "progress"}
        },
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


REPORT_CATEGORY_LABELS = {
    "ketelanjangan": "Ketelanjangan / konten eksplisit",
    "konten_visual": "Konten visual berisiko",
    "konten visual": "Konten visual berisiko",
    "konten_teks": "Teks berisiko",
    "dokumen": "Dokumen",
    "pesan": "Pesan",
    "audio": "Audio / rekaman",
    "video": "Video",
    "politik": "Konten politik",
    "lgbt_content": "LGBT text/flag",
    "political_meme": "Meme politik",
    "political_campaign": "Kampanye politik",
    "demonstration": "Demonstrasi",
    "incitement": "Incitement / ajakan provokatif",
    "extremism": "Extremism",
    "hate_speech": "Ujaran kebencian",
    "political_insult": "Penghinaan negara/politikus",
    "makar": "Indikasi makar",
    "senjata": "Senjata / bom",
    "lainnya": "Lainnya",
}
REPORT_SOURCE_LABELS = {
    "image": "Foto / screenshot",
    "video": "Video",
    "audio": "Audio",
    "document": "Dokumen",
    "text": "Teks",
    "gallery": "Galeri HP",
    "dcim": "Kamera HP",
    "download": "Folder unduhan",
    "recovered_trash": "Sampah / media terhapus",
    "recovered_cache": "Pratinjau cache galeri",
    "ios_hidden": "Photos Tersembunyi (iOS)",
    "ios_recently_deleted": "Baru Dihapus (iOS)",
    "ios_recovered_cache": "Cache / preview Photos (iOS)",
    "ios_deleted_metadata": "Jejak hapus permanen Photos (iOS)",
    "whatsapp": "WhatsApp",
    "email": "Email",
    "gmail": "Email",
    "browser_history_full": "Riwayat Browser (lengkap)",
    "browser_history_partial": "Riwayat Browser (sebagian)",
    "notes": "Catatan Android",
}
REPORT_METHOD_LABELS = {
    "adb": "USB Android (ADB)",
    "adb_pull": "USB Android (ADB)",
    "android_agent_inventory_complete": "Inventaris Android selesai",
    "android_agent_inventory_partial": "Inventaris Android sebagian",
    "preprocessing_complete": "Pra-pemrosesan selesai",
    "preprocessing_partial": "Pra-pemrosesan sebagian",
    "selection_confirmed": "Seleksi terkonfirmasi",
    "android_agent_direct_manifest": "Transfer agen Android",
    "android_agent_direct_manifest_resumed": "Transfer agen Android dilanjutkan",
    "android_recovery_quick_complete": "Recovery sampah Android (Cepat)",
    "android_recovery_quick_partial": "Recovery sampah Android (Cepat, sebagian)",
    "android_recovery_full_complete": "Recovery sampah Android (Penuh)",
    "android_recovery_full_partial": "Recovery sampah Android (Penuh, sebagian)",
    "ios_afc_media": "Media dan recovery Photos iOS",
    "ios_photo_library_recovery": "Hidden/deleted/cache Photos iOS",
    "zip_upload": "Unggah ZIP",
    "simulated": "Simulasi lab",
    "chrome_cdp": "Riwayat browser Chrome (CDP)",
    "gmail_api": "Email Gmail (API resmi)",
    "android_notes_samsung_export": "Catatan Android (ekspor Samsung Notes)",
    "android_notes_ui_walk": "Catatan Android (navigasi UI)",
    "whatsapp_crypt15": "Backup WhatsApp terenkripsi (Crypt15)",
    "whatsapp_crypt15_parse_unavailable": (
        "Backup WhatsApp Crypt15 (format database belum terbaca)"
    ),
    "unknown": "Tidak diketahui",
}
RECOVERY_STATE_LABELS = {
    "scanning": "memindai",
    "complete": "selesai",
    "partial": "sebagian",
    "unavailable": "tidak tersedia",
}
REVIEW_STATUS_LABELS = {
    "pending": "Menunggu",
    "confirmed": "Dikonfirmasi",
    "rejected": "Ditolak",
}
PROFILE_USERNAME = re.compile(r"(?<![A-Za-z0-9._])@([A-Za-z0-9._]{2,30})")
PROFILE_LINK = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>{}\[\]\"']+|"
    r"(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.)"
    r"(?:com|net|org|id|co|me|io|app|link|bio|blog)(?:/[^\s<>{}\[\]\"']*)?"
)
ACCOUNT_MARKER = re.compile(r"^[A-Za-z0-9._]{2,30}$")
NUMERIC_ACCOUNT_MARKER = re.compile(r"^[0-9._]+$")
PROFILE_COUNT = re.compile(r"^(?:[0-9][0-9.,]*|[0-9:. ]+(?:am|pm)?)$")
PROFILE_METRIC_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])([0-9][0-9.,]*\s*(?:k|m|b|rb|jt)?)\s*"
    r"(friends?|teman|followers?|following|pengikut|mengikuti|posts?|postingan|kiriman)\b"
)
PROFILE_METRIC_SEPARATOR = re.compile(r"[\s·•|,;/]+")
IG_PROFILE_CHROME_PHRASES = (
    "add banners",
    "edit profile",
    "edit profil",
    "get verified",
    "share profile",
    "share profil",
)
IG_PROFILE_CHROME_RE = re.compile(
    r"(?i)\b("
    r"add banners|edit profile|edit profil|get verified|share profile|share profil|"
    r"following|followers|posts?|postingan|pengikut|mengikuti|diikuti"
    r")\b"
)
SPOTIFY_OCR_RE = re.compile(r"(?i)\bopen\.?\s*spotify(?:\s*com)?\S*")
PROFILE_NOISE = {
    "add",
    "add banners",
    "articles",
    "banners",
    "create",
    "curious",
    "curious_",
    "edit profile",
    "edit profil",
    "followers",
    "following",
    "for",
    "get verified",
    "highlights",
    "home",
    "inspo",
    "just",
    "likes",
    "media",
    "message",
    "messages",
    "more options",
    "navigate up",
    "needed",
    "needed..",
    "open",
    "posts",
    "postingan",
    "profile",
    "profile image",
    "profil",
    "ready",
    "ready for",
    "ready for.",
    "reels",
    "replies",
    "search and explore",
    "search button",
    "search facebook",
    "search",
    "share profile",
    "share profil",
    "spotify",
    "today",
    "todays",
    "vibe",
    "vibe_",
    "facebook",
    "facebook logo",
    "messaging",
    "notifications",
    "groups",
    "friends",
    "menu",
    "story tray",
    "on.your",
    "whats.on",
    "your.mind",
    "null",
    "undefined",
}
IG_AVATAR_PROMPT_FRAGMENTS = (
    "curious",
    "inspo",
    "needed",
    "ready for",
    "today",
    "vibe",
)
DOMAINISH_USERNAME_SUFFIXES = {
    "app",
    "blog",
    "com",
    "facebook",
    "id",
    "instagram",
    "io",
    "link",
    "me",
    "net",
    "org",
    "spotify",
    "twitter",
}
CONTENT_NOISE = PROFILE_NOISE | {
    "comment",
    "comments",
    "komentar",
    "like",
    "liked",
    "suka",
    "share",
    "bagikan",
    "options",
    "opsi",
    "back",
    "kembali",
    "stories archive",
    "arsip cerita",
    "memories",
    "select",
    "select multiple comments to delete",
}
ARCHIVE_PREVIEW_DROP = re.compile(
    r"(?i)^("
    r"options|opsi|back|kembali|stories archive|arsip cerita|memories|"
    r",?\s*\d+\s+of\s+\d+\.?|"
    r"\d{1,2}:\d{2}|"
    r"select|select multiple comments to delete|"
    r"your archived stories aren't visible.*|"
    r"stories you shared publicly.*|"
    r"learn more|"
    r"on this day|"
    r"\d+\s+years?\s+ago\s+today\.?"
    r")$"
)
ARCHIVE_MONTH_ONLY = re.compile(
    r"(?i)^(jan|january|feb|february|mar|march|apr|april|may|jun|june|"
    r"jul|july|aug|august|sep|sept|september|oct|october|nov|november|"
    r"dec|december)\s+\d{4}$"
)
# Host EasyOCR blobs keep chrome inline ("7.29 PM … Stories archive … content").
ARCHIVE_OCR_EMBEDDED_NOISE = re.compile(
    r"\b("
    r"stories?\s+archive|arsip\s+cerita|options|opsi|back|kembali|memories|"
    r"on this day|"
    r"\d+\s+of\s+\d+"
    r")\b|"
    r"(?<!\d)\d{1,2}[.:]\d{2}(\s*[ap]\.?m\.?)?",
    re.IGNORECASE,
)


def _is_chrome_ui_line(value: str) -> bool:
    key = value.casefold().strip()
    if not key:
        return True
    if "tab " in key and " of " in key:
        return True
    if key.startswith("create, double tap") or "story tray" in key:
        return True
    if "on your mind" in key or "di pikiranmu" in key:
        return True
    if "create note" in key or "buat catatan" in key:
        return True
    if "friend suggestion" in key or "saran teman" in key:
        return True
    if "profile picture" in key or "foto profil" in key:
        return True
    if key in {
        "facebook logo",
        "search facebook",
        "messaging",
        "menu",
        "home",
        "reels",
        "friends",
        "groups",
        "notifications",
        "profile",
        "profile picture",
        "on.your",
        "whats.on",
        "your.mind",
    }:
        return True
    return any(
        key.startswith(f"{prefix},")
        for prefix in (
            "home",
            "reels",
            "friends",
            "groups",
            "notifications",
            "profile",
            "menu",
        )
    )


def _ms(v: float) -> str:
    if not v:
        return "-"
    if v < 1000:
        return f"{v:.0f} ms"
    return f"{v / 1000:.2f} s"


def _fmt_bytes(value: object) -> str:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _social_account_heading(account: dict) -> str:
    platform = str(account.get("platform") or "Akun sosial")
    display_name = str(account.get("display_name") or "").strip()
    username = str(account.get("username") or "").strip().removeprefix("@")
    if username:
        identity = f"{display_name} · @{username}" if display_name else f"@{username}"
        return f"{platform} · {identity}"
    return f"{platform} · {display_name}" if display_name else platform


def _report_label(value: object, labels: dict[str, str]) -> str:
    key = "" if value is None else str(value).strip()
    return labels.get(key, key.replace("_", " "))


def _report_method(value: object) -> str:
    key = "unknown" if value is None else str(value).strip() or "unknown"
    parts: list[str] = []
    for raw_part in key.split("+"):
        label = _report_label(raw_part, REPORT_METHOD_LABELS)
        if label not in parts:
            parts.append(label)
    return " + ".join(parts)


def _record_metadata(record: dict) -> dict:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def _normalized_lines(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for raw in value.replace("\r", "\n").splitlines():
        line = re.sub(r"[\t ]+", " ", raw).strip()
        key = line.casefold()
        if line and key not in seen:
            seen.add(key)
            output.append(line)
    return output


def _profile_links(metadata: dict, text: object, record: dict | None = None) -> list[str]:
    values = metadata.get("profile_links")
    candidates = [value for value in values if isinstance(value, str)] if isinstance(values, list) else []
    blobs: list[str] = []
    if isinstance(text, str) and text.strip():
        blobs.append(text)
    if isinstance(record, dict):
        preprocessing = record.get("preprocessing")
        if isinstance(preprocessing, dict):
            ocr = preprocessing.get("ocr")
            if isinstance(ocr, dict) and isinstance(ocr.get("text"), str):
                blobs.append(ocr["text"])
    for blob in blobs:
        repaired = _repair_ocr_link_text(blob)
        candidates.extend(match.group(0) for match in PROFILE_LINK.finditer(repaired))
    return _dedupe_profile_links(candidates)


def _normalize_profile_link(value: str) -> str | None:
    text = value.strip().rstrip(".,;)]}")
    # OCR often turns UI ellipsis into a trailing underscore.
    text = re.sub(r"[_\u2026.]+$", "", text)
    text = text.strip()
    if len(text) < 4:
        return None
    # Keep real bio links (incl. OCR casing). Only drop obvious chrome UI labels.
    if text.casefold() in PROFILE_NOISE or _is_chrome_ui_line(text):
        return None
    return text[:2048]


def _dedupe_profile_links(candidates: list[str]) -> list[str]:
    """Keep unique links; when one is a prefix of another, keep the longer."""
    normalized = [
        value
        for value in (_normalize_profile_link(candidate) for candidate in candidates)
        if value is not None
    ]
    output: list[str] = []
    for value in sorted(normalized, key=len, reverse=True):
        key = value.casefold()
        if any(
            key == existing.casefold()
            or key.startswith(existing.casefold())
            or existing.casefold().startswith(key)
            for existing in output
        ):
            # Longer already kept first; skip shorter/equal prefix duplicates.
            continue
        output.append(value)
        if len(output) >= 16:
            break
    return output


def _repair_ocr_link_text(value: str) -> str:
    return repair_ocr_link_text(value)


def _valid_username(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if trimmed.endswith("..") or trimmed.endswith("…"):
        return None
    candidate = trimmed.removeprefix("@").strip()
    if candidate.endswith(".") or ".." in candidate:
        return None
    if PROFILE_LINK.fullmatch(candidate):
        return None
    key = candidate.casefold()
    key_stripped = key.rstrip("_")
    # org.json optString(JSON null) / bad structured markers
    if key in {"null", "undefined", "none", "nil"}:
        return None
    if not ACCOUNT_MARKER.fullmatch(candidate) or key in PROFILE_NOISE or key_stripped in PROFILE_NOISE:
        return None
    if any(fragment in key_stripped for fragment in IG_AVATAR_PROMPT_FRAGMENTS):
        return None
    if (
        "." in candidate
        and candidate.rsplit(".", 1)[-1].casefold() in DOMAINISH_USERNAME_SUFFIXES
    ):
        return None
    if PROFILE_COUNT.fullmatch(candidate) or NUMERIC_ACCOUNT_MARKER.fullmatch(candidate):
        return None
    if _is_chrome_ui_line(candidate) or " " in candidate:
        return None
    # Metric chrome: "471followers", "471.471followers"
    if re.search(r"(?i)followers?|following|pengikut|mengikuti|postingan|\bposts?\b", key):
        return None
    if re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", candidate):
        return None
    return candidate


def _pick_best_username(candidates: list[str]) -> str | None:
    valid = [value for value in candidates if _valid_username(value)]
    if not valid:
        return None
    # Prefer lowercase handles (lutfizp) over Title.Case OCR joins (War.Radiohead).
    lower = [value for value in valid if value == value.lower()]
    pool = lower or valid
    dotted = [value for value in pool if "." in value]
    ranked = dotted or pool
    return max(ranked, key=lambda value: (len(value), value.count("."), value))


def _username_from_ocr_blob(value: str) -> str | None:
    if not value:
        return None
    candidates: list[str] = []
    for match in re.finditer(
        r"(?i)(?<![A-Za-z0-9._])([A-Za-z0-9._]*\.[A-Za-z0-9._]+)(?![A-Za-z0-9._])",
        value,
    ):
        username = _valid_username(match.group(1))
        if username:
            candidates.append(username)
    for match in re.finditer(
        r"(?i)(?<![A-Za-z0-9._])([A-Za-z0-9_]{2,15})\s+([A-Za-z0-9_]{2,15})(?![A-Za-z0-9._])",
        value,
    ):
        left, right = match.group(1), match.group(2)
        # Only join lowercase fragments (intel negara → intel.negara). Skip Title Case music OCR.
        if left != left.lower() or right != right.lower():
            continue
        if left.casefold() in PROFILE_NOISE or right.casefold() in PROFILE_NOISE:
            continue
        username = _valid_username(f"{left}.{right}")
        if username:
            candidates.append(username)
    return _pick_best_username(candidates)


def _view_id_resource_leaf(view_id: str) -> str:
    leaf = view_id.rsplit("/", 1)[-1]
    return leaf.rsplit(":", 1)[-1].casefold()


def _profile_username_from_nodes(metadata: dict) -> str | None:
    nodes = metadata.get("nodes")
    if not isinstance(nodes, list):
        return None
    username_resources = (
        "action_bar_title",
        "profile_header_username",
        "profile_header_user_name",
        "action_bar_large_title_auto_size",
        "screen_name",
        "username",
        "user_name",
    )
    max_bottom = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        bounds = node.get("bounds")
        if isinstance(bounds, dict):
            max_bottom = max(max_bottom, int(bounds.get("bottom") or 0))
    profile_cutoff = (max_bottom * 2) // 5 if max_bottom > 0 else None
    for resource in username_resources:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            view_id = str(node.get("view_id") or "").casefold()
            if "notification" in view_id:
                continue
            # Exact leaf only — avoid user_name_container matching resource user_name.
            if _view_id_resource_leaf(view_id) != resource:
                continue
            for key in ("text", "content_description"):
                valid = _valid_username(node.get(key))
                if valid:
                    return valid
    for node in nodes:
        if not isinstance(node, dict):
            continue
        view_id = str(node.get("view_id") or "").casefold()
        if "notification" in view_id:
            continue
        bounds = node.get("bounds")
        if profile_cutoff is not None and isinstance(bounds, dict):
            if int(bounds.get("top") or 0) > profile_cutoff:
                continue
        text = node.get("text")
        if isinstance(text, str) and text.strip().startswith("@"):
            valid = _valid_username(text)
            if valid:
                return valid
    return None


def _profile_username(
    package_name: str,
    record: dict,
    metadata: dict,
    lines: list[str],
) -> str | None:
    # Prefer on-device structured username (already correct for IG Compose).
    structured = _valid_username(metadata.get("profile_username"))
    if structured:
        return structured
    from_nodes = _profile_username_from_nodes(metadata)
    if from_nodes:
        return from_nodes
    if package_name != "com.facebook.katana":
        from_regions = _profile_username_from_ocr_regions(record)
        if from_regions:
            return from_regions
    ocr_blob = "\n".join(lines)
    preprocessing = record.get("preprocessing")
    if isinstance(preprocessing, dict):
        ocr = preprocessing.get("ocr")
        if isinstance(ocr, dict) and isinstance(ocr.get("text"), str):
            ocr_blob = f"{ocr_blob}\n{ocr['text']}"
    if package_name != "com.facebook.katana":
        recovered = _username_from_ocr_blob(ocr_blob)
        if recovered:
            return recovered
    for line in lines:
        match = PROFILE_USERNAME.search(line)
        if match:
            candidate = _valid_username(match.group(1))
            if candidate:
                return candidate
    if package_name == "com.instagram.android":
        for index, line in enumerate(lines):
            if line.casefold() not in {"posts", "postingan"}:
                continue
            for candidate in reversed(lines[max(0, index - 5) : index]):
                valid = _valid_username(candidate)
                if valid:
                    return valid
        for index, line in enumerate(lines):
            if line.casefold() not in {"followers", "following", "pengikut", "diikuti"}:
                continue
            for candidate in reversed(lines[max(0, index - 4) : index]):
                valid = _valid_username(candidate)
                if valid:
                    return valid
    return None


def _profile_username_from_ocr_regions(record: dict) -> str | None:
    preprocessing = record.get("preprocessing")
    if not isinstance(preprocessing, dict):
        return None
    ocr = preprocessing.get("ocr")
    if not isinstance(ocr, dict):
        return None
    raw_regions = ocr.get("regions")
    if not isinstance(raw_regions, list):
        return None
    candidates: list[str] = []
    scored: list[tuple[int, int, str]] = []
    for region in raw_regions:
        if not isinstance(region, dict):
            continue
        username = _valid_username(region.get("text"))
        top = region.get("top")
        left = region.get("left")
        if username and isinstance(top, int) and isinstance(left, int):
            candidates.append(username)
            scored.append((top, left, username))
    # Prefer top-of-screen lowercase handles over Title.Case OCR joins.
    lower_scored = [row for row in scored if row[2] == row[2].lower()]
    pool = lower_scored or scored
    if not pool:
        return _pick_best_username(candidates)
    return min(pool, key=lambda value: (value[0], value[1]))[2]


def _profile_bio(
    record: dict,
    metadata: dict,
    lines: list[str],
    username: str | None,
    links: list[str],
) -> str | None:
    # Spatial OCR first (Compose profile a11y often empty), then structured metadata.
    spatial = _spatial_profile_bio(record, username, links)
    if spatial:
        return spatial
    structured = metadata.get("profile_bio")
    if isinstance(structured, str) and structured.strip():
        structured_lines = [
            line
            for line in _normalized_lines(structured)
            if line.casefold() not in PROFILE_NOISE
            and not _is_chrome_ui_line(line)
            and not _is_profile_metric_chrome(line)
            and not _is_ig_action_label(line)
            and not re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", line.strip())
        ]
        cleaned = "\n".join(structured_lines).strip()[:4096]
        if cleaned:
            return _scrub_profile_bio(cleaned, username, links)
    username_key = username.casefold() if username else None
    link_keys = [value.casefold() for value in links]
    output: list[str] = []
    for line in lines:
        key = line.casefold()
        if (
            key == username_key
            or key in PROFILE_NOISE
            or _is_chrome_ui_line(line)
            or _is_profile_metric_chrome(line)
            or _is_ig_action_label(line)
            or re.fullmatch(r"[0-9][0-9.,]*", key)
            or re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", line.strip())
            or any(link in key for link in link_keys)
        ):
            continue
        output.append(line)
        if len(output) >= 20:
            break
    value = "\n".join(output).strip()[:4096]
    return _scrub_profile_bio(value, username, links) if value else None


def _is_profile_metric_chrome(line: str) -> bool:
    if PROFILE_METRIC_TOKEN.search(line) is None:
        remainder = IG_PROFILE_CHROME_RE.sub(" ", line)
        remainder = re.sub(r"[0-9][0-9.,]*", " ", remainder)
        remainder = PROFILE_METRIC_SEPARATOR.sub("", remainder)
        return remainder == "" and bool(IG_PROFILE_CHROME_RE.search(line))
    remainder = PROFILE_METRIC_TOKEN.sub("", line)
    remainder = IG_PROFILE_CHROME_RE.sub(" ", remainder)
    remainder = re.sub(r"[0-9][0-9.,]*", " ", remainder)
    return PROFILE_METRIC_SEPARATOR.sub("", remainder) == ""


def _is_ig_action_label(text: str) -> bool:
    key = text.strip().casefold()
    return key in {
        "edit",
        "sunting",
        "share",
        "bagikan",
        *IG_PROFILE_CHROME_PHRASES,
    } or any(key.startswith(phrase) for phrase in IG_PROFILE_CHROME_PHRASES)


def _scrub_profile_bio(text: str, username: str | None, links: list[str]) -> str | None:
    blob = text
    if username:
        blob = re.sub(rf"(?i)@?{re.escape(username)}\b", " ", blob)
    for link in links:
        blob = re.sub(re.escape(link), " ", blob, flags=re.I)
    blob = re.sub(
        r"(?i)(?:\b[0-9][0-9.,]*\b\s*)+(?=(?:following|followers|posts?|postingan|pengikut|mengikuti|diikuti)\b)",
        " ",
        blob,
    )
    blob = re.sub(r"(?i)(?:\b\d+\s+)?\bopen\s+spotify\b[\s\S]*", " ", blob)
    blob = SPOTIFY_OCR_RE.sub(" ", blob)
    for phrase in IG_PROFILE_CHROME_PHRASES:
        blob = re.sub(re.escape(phrase), " ", blob, flags=re.I)
    blob = PROFILE_METRIC_TOKEN.sub(" ", blob)
    blob = IG_PROFILE_CHROME_RE.sub(" ", blob)
    blob = re.sub(r"[+=]+", " ", blob)
    blob = re.sub(r"\b\d+[.,]?\d*\b", " ", blob)
    blob = re.sub(r"\b[a-z0-9]{12,}\.?\b", " ", blob, flags=re.I)
    blob = re.sub(r"\s+", " ", blob).strip(" -·,.")
    if len(blob) < 2:
        return None
    if blob.casefold() in PROFILE_NOISE or _is_chrome_ui_line(blob) or _is_profile_metric_chrome(blob):
        return None
    return blob[:4096]


def _profile_metrics(metadata: dict, lines: list[str]) -> dict[str, int | None]:
    raw = metadata.get("profile_metrics")
    values = raw if isinstance(raw, dict) else {}
    output: dict[str, int | None] = {}
    for key in ("posts", "followers", "friends", "following"):
        value = values.get(key)
        output[key] = (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else _profile_metric_from_lines(lines, key)
        )
    blob = "\n".join(lines).casefold()
    if any(
        phrase in blob
        for phrase in (
            "create your first post",
            "buat postingan pertama",
            "share your point of view",
        )
    ):
        output["posts"] = 0
    return output


def _profile_metric_from_lines(lines: list[str], name: str) -> int | None:
    labels = {
        "posts": {"posts", "postingan", "kiriman", "tweets", "tweet"},
        "followers": {"followers", "pengikut"},
        "friends": {"friends", "friend", "teman"},
        "following": {"following", "mengikuti", "diikuti"},
    }[name]
    count_pattern = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)\s*(k|m|b|rb|jt)?$", re.I)

    def parse(value: str) -> int | None:
        match = count_pattern.fullmatch(value.strip())
        if not match:
            return None
        number, suffix = match.groups()
        if not suffix:
            return int(re.sub(r"[.,]", "", number))
        multiplier = {
            "k": 1_000,
            "rb": 1_000,
            "m": 1_000_000,
            "jt": 1_000_000,
            "b": 1_000_000_000,
        }[suffix.casefold()]
        return int(float(number.replace(",", ".")) * multiplier)

    for index, line in enumerate(lines):
        normalized = line.casefold().strip()
        for label in labels:
            inline = re.search(
                rf"(?i)([0-9][0-9.,]*\s*(?:k|m|b|rb|jt)?)\s*{re.escape(label)}\b",
                normalized,
            )
            if inline:
                count = parse(inline.group(1))
                if count is not None:
                    return count
        if normalized not in labels:
            continue
        for nearby in (index - 1, index + 1):
            if 0 <= nearby < len(lines):
                count = parse(lines[nearby])
                if count is not None:
                    return count
    return None


def _profile_display_name(metadata: dict) -> str | None:
    value = metadata.get("profile_display_name")
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\t\r\n ]+", " ", value).strip()
    if not normalized or _is_chrome_ui_line(normalized) or normalized.casefold() in PROFILE_NOISE:
        return None
    if re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", normalized):
        return None
    if re.search(r"(?i)followers?|following|pengikut", normalized):
        return None
    # FB person names are short; reject composer / prompt sentences.
    if any(ch in normalized for ch in ":?!"):
        return None
    if len(normalized) > 64:
        return None
    return normalized[:256] or None


def _spatial_profile_bio(
    record: dict,
    username: str | None,
    links: list[str],
) -> str | None:
    preprocessing = record.get("preprocessing")
    ocr = preprocessing.get("ocr") if isinstance(preprocessing, dict) else None
    raw_regions = ocr.get("regions") if isinstance(ocr, dict) else None
    if not username or not isinstance(raw_regions, list):
        return None
    regions = [
        value
        for value in raw_regions
        if isinstance(value, dict)
        and isinstance(value.get("text"), str)
        and all(isinstance(value.get(name), int) for name in ("left", "top", "bottom"))
    ]
    username_key = username.casefold()
    username_regions = [
        value
        for value in regions
        if value["text"].strip().removeprefix("@").casefold() == username_key
    ]
    if not username_regions:
        return None
    profile_start = min(int(value["bottom"]) for value in username_regions)
    action_regions = [
        value
        for value in regions
        if int(value["top"]) > profile_start and _is_ig_action_label(str(value["text"]))
    ]
    profile_end = min(
        (int(value["top"]) for value in action_regions),
        default=profile_start + 900,
    )
    link_keys = [value.casefold() for value in links]
    candidates = []
    for region in regions:
        top = int(region["top"])
        text = region["text"].strip()
        key = text.casefold()
        if not profile_start < top < profile_end:
            continue
        if (
            not text
            or key in PROFILE_NOISE
            or key in {"+", "@", "new", "baru"}
            or _is_chrome_ui_line(text)
            or _is_profile_metric_chrome(text)
            or _is_ig_action_label(text)
            or re.fullmatch(r"[0-9][0-9.,]*", key)
            or any(link in key or key in link for link in link_keys)
            or PROFILE_LINK.search(text)
        ):
            continue
        candidates.append(region)
    candidates.sort(key=lambda value: (int(value["top"]), int(value["left"])))
    rows: list[list[str]] = []
    row_top: int | None = None
    for region in candidates:
        top = int(region["top"])
        if row_top is None or abs(top - row_top) > 24:
            rows.append([])
            row_top = top
        rows[-1].append(region["text"].strip())
    value = "\n".join(" ".join(row) for row in rows if row).strip()[:4096]
    return _scrub_profile_bio(value, username, links) if value else None


def _social_preview(text: object) -> str | None:
    lines = []
    for line in _normalized_lines(text):
        key = line.casefold()
        if key in CONTENT_NOISE or ARCHIVE_PREVIEW_DROP.fullmatch(line.strip()):
            continue
        if _is_chrome_ui_line(line) or _is_profile_metric_chrome(line):
            continue
        lines.append(line)
    value = "\n".join(lines).strip()[:MAX_SOCIAL_PREVIEW_CHARS]
    return value or None


def _scrub_archive_ocr_line(line: str) -> str | None:
    cleaned = ARCHIVE_OCR_EMBEDDED_NOISE.sub(" ", line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|[](){}")
    if len(cleaned) < 8:
        return None
    if ARCHIVE_PREVIEW_DROP.fullmatch(cleaned) or ARCHIVE_MONTH_ONLY.fullmatch(cleaned):
        return None
    if cleaned.casefold() in CONTENT_NOISE or _is_chrome_ui_line(cleaned):
        return None
    if _is_profile_metric_chrome(cleaned):
        return None
    return cleaned


def _archive_social_preview(text: object) -> str | None:
    """Surface host-OCR story text; a11y alone is chrome/month-only on Compose grids."""
    content: list[str] = []
    months: list[str] = []
    for line in _normalized_lines(text):
        stripped = line.strip()
        if ARCHIVE_MONTH_ONLY.fullmatch(stripped):
            months.append(stripped)
            continue
        if ARCHIVE_PREVIEW_DROP.fullmatch(stripped):
            continue
        if stripped.casefold() in CONTENT_NOISE or _is_chrome_ui_line(stripped):
            continue
        scrubbed = _scrub_archive_ocr_line(stripped)
        if scrubbed:
            content.append(scrubbed)
    if content:
        return "\n".join(dict.fromkeys(content)).strip()[:MAX_SOCIAL_PREVIEW_CHARS]
    if months:
        return "\n".join(dict.fromkeys(months)).strip()[:MAX_SOCIAL_PREVIEW_CHARS]
    return None


def _record_source_app(record: dict) -> str | None:
    existing = record.get("source_app")
    if isinstance(existing, str) and existing in SOCIAL_PACKAGES:
        return existing
    metadata = _record_metadata(record)
    inferred = infer_source_app(
        directory_hint=(
            metadata.get("directory_hint")
            if isinstance(metadata.get("directory_hint"), str)
            else None
        ),
        display_name=(
            metadata.get("display_name")
            if isinstance(metadata.get("display_name"), str)
            else None
        ),
        path=str(record.get("source_locator") or "") or None,
    )
    return inferred if inferred in SOCIAL_PACKAGES else None


def _record_social_scope(record: dict, package_name: str) -> str | None:
    metadata = _record_metadata(record)
    scope = metadata.get("social_scope") or record.get("social_scope")
    if scope in SOCIAL_SCOPES and scope != "device_media":
        return str(scope)
    if record.get("source_kind") in {"media_image", "media_video"} and package_name:
        return "device_media"
    return None


def _social_item_preview(scope: str, record: dict) -> str | None:
    if scope == "device_media":
        metadata = _record_metadata(record)
        name = metadata.get("display_name")
        if isinstance(name, str) and name.strip():
            hint = metadata.get("directory_hint")
            if isinstance(hint, str) and hint.strip():
                return f"{name.strip()} · {hint.strip()}"
            return name.strip()
    if scope == "own_story_archive":
        return _archive_social_preview(record.get("normalized_text")) or _social_preview(
            record.get("normalized_text")
        )
    return _social_preview(record.get("normalized_text"))


def _apply_social_enrichment(record: dict, row) -> dict:
    raw_metadata = row["host_metadata_json"]
    try:
        enrichment = json.loads(raw_metadata) if raw_metadata else {}
    except (TypeError, json.JSONDecodeError):
        enrichment = {}
    metadata = dict(_record_metadata(record))
    if isinstance(enrichment, dict):
        username = _valid_username(enrichment.get("profile_username"))
        if username:
            metadata["profile_username"] = username
        links = enrichment.get("profile_links")
        if isinstance(links, list):
            existing_links = metadata.get("profile_links")
            merged_links = (
                [value for value in existing_links if isinstance(value, str)]
                if isinstance(existing_links, list)
                else []
            )
            merged_links.extend(value for value in links if isinstance(value, str))
            metadata["profile_links"] = list(dict.fromkeys(merged_links))[:16]
        derived_metrics = enrichment.get("profile_metrics")
        if isinstance(derived_metrics, dict):
            existing_metrics = metadata.get("profile_metrics")
            merged_metrics = dict(existing_metrics) if isinstance(existing_metrics, dict) else {}
            for name in ("posts", "followers", "friends", "following"):
                value = derived_metrics.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    merged_metrics[name] = value
            metadata["profile_metrics"] = merged_metrics
    record["metadata"] = metadata

    host_text = row["host_ocr_text"]
    if isinstance(host_text, str) and host_text.strip():
        source_text = record.get("normalized_text")
        values = [
            value.strip()
            for value in (source_text, host_text)
            if isinstance(value, str) and value.strip()
        ]
        record["normalized_text"] = "\n".join(dict.fromkeys(values))[:32768]
        record["host_social_ocr"] = {
            "backend": row["host_ocr_backend"],
            "confidence": row["host_ocr_confidence"],
        }
    return record


async def _load_social_records(session_id: str) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    transferred = await db.fetchall(
        "SELECT r.record_id, r.canonical_json, e.ocr_text AS host_ocr_text, "
        "e.ocr_backend AS host_ocr_backend, e.ocr_confidence AS host_ocr_confidence, "
        "e.metadata_json AS host_metadata_json FROM crawl_records r "
        "LEFT JOIN social_snapshot_enrichments e "
        "ON e.crawl_id = r.crawl_id AND e.record_id = r.record_id "
        "WHERE r.session_id = ? AND r.source_kind IN "
        "('visible_ui', 'media_image', 'media_video') "
        "ORDER BY r.ingested_at, r.record_id",
        (session_id,),
    )
    for row in transferred:
        try:
            value = json.loads(row["canonical_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        value = _apply_social_enrichment(value, row)
        record_id = str(value.get("record_id") or row["record_id"])
        if isinstance(value, dict) and record_id not in seen:
            seen.add(record_id)
            records.append(value)

    live_rows = await db.fetchall(
        "SELECT path FROM files WHERE session_id = ? "
        "AND source IN ('visible_ui', 'accessibility_visible_ui') ORDER BY path",
        (session_id,),
    )
    root = (settings.staging_dir / session_id).resolve()
    for row in live_rows:
        target = (root / str(row["path"])).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        record_id = str(value.get("record_id") or "")
        if isinstance(value, dict) and record_id and record_id not in seen:
            seen.add(record_id)
            records.append(value)
    return records


async def _session_inventory_counts(session_id: str) -> dict:
    contact_rows = await db.fetchall(
        "SELECT meta_json FROM files WHERE session_id = ? "
        "AND LOWER(source) IN ('contact', 'contacts')",
        (session_id,),
    )
    contact_records = len(contact_rows)
    contact_unique = 0
    contact_names: set[str] = set()
    for row in contact_rows:
        try:
            metadata = json.loads(row["meta_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        display_name = str(metadata.get("display_name") or "").strip().casefold()
        if display_name:
            contact_names.add(display_name)
        if not metadata.get("contact_duplicate"):
            contact_unique += 1
    if contact_records and not contact_unique:
        contact_unique = contact_records

    sms_rows = await db.fetchall(
        "SELECT json_extract(canonical_json, '$.metadata.direction') AS direction, "
        "COUNT(*) AS total FROM crawl_records "
        "WHERE session_id = ? AND source_kind = 'sms' GROUP BY 1",
        (session_id,),
    )
    sms_by_direction = {
        str(row["direction"] or "unknown"): int(row["total"]) for row in sms_rows
    }
    recovery_rows = await db.fetchall(
        "SELECT source, COUNT(*) AS total FROM files WHERE session_id = ? "
        "AND source IN ('recovered_trash', 'recovered_cache') GROUP BY source",
        (session_id,),
    )
    recovery = {str(row["source"]): int(row["total"]) for row in recovery_rows}
    notes_row = await db.fetchone(
        "SELECT COUNT(*) AS total FROM files WHERE session_id = ? AND source = 'notes'",
        (session_id,),
    )
    return {
        "contact_records": contact_records,
        "contact_unique": contact_unique,
        "contact_unique_names": len(contact_names),
        "sms_by_direction": sms_by_direction,
        "recovery_cache": recovery.get("recovered_cache", 0),
        "recovery_trash": recovery.get("recovered_trash", 0),
        "notes_records": int(notes_row["total"] or 0) if notes_row else 0,
    }


async def _session_device_identity(session_id: str) -> dict:
    rows = await db.fetchall(
        "SELECT f.meta_json, cr.normalized_text FROM files f "
        "LEFT JOIN crawl_records cr ON cr.session_id = f.session_id "
        "AND cr.record_id = json_extract(f.meta_json, '$.crawl_record_id') "
        "WHERE f.session_id = ? AND LOWER(f.source) IN ('document', 'documents')",
        (session_id,),
    )
    hints = []
    for row in rows:
        try:
            metadata = json.loads(row["meta_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        hint = hints_from_document(
            display_name=(
                metadata.get("display_name")
                if isinstance(metadata.get("display_name"), str)
                else None
            ),
            normalized_text=(
                row["normalized_text"]
                if isinstance(row["normalized_text"], str)
                else None
            ),
        )
        if hint:
            hints.append(hint)
    return merge_device_identity_hints(hints)


async def _social_report_data(session_id: str) -> tuple[list[dict], int, bool]:
    accounts: dict[str, dict] = {}
    all_items: list[dict] = []
    for record in await _load_social_records(session_id):
        package_name = _record_source_app(record)
        if package_name not in SOCIAL_PACKAGES:
            continue
        metadata = _record_metadata(record)
        scope = _record_social_scope(record, package_name)
        if scope not in SOCIAL_SCOPES:
            continue
        account = accounts.setdefault(
            package_name,
            {
                "platform": SOCIAL_PACKAGES[package_name],
                "source_app": package_name,
                "username": None,
                "display_name": None,
                "bio": None,
                "profile_links": [],
                "profile_metrics": {
                    "posts": None,
                    "followers": None,
                    "friends": None,
                    "following": None,
                },
                "scope_counts": {name: 0 for name in SOCIAL_SCOPES},
                "items": [],
            },
        )
        account["scope_counts"][scope] += 1
        lines = _normalized_lines(record.get("normalized_text"))
        if scope == "own_profile":
            username = _profile_username(package_name, record, metadata, lines)
            links = _profile_links(metadata, record.get("normalized_text"), record)
            display_name = _profile_display_name(metadata)
            bio = _profile_bio(record, metadata, lines, username, links)
            if (
                bio
                and display_name
                and bio.strip().casefold() == display_name.strip().casefold()
            ):
                bio = None
            account["username"] = username or account["username"]
            account["display_name"] = display_name or account["display_name"]
            account["bio"] = bio or account["bio"]
            metrics = _profile_metrics(metadata, lines)
            account["profile_metrics"] = {
                key: value if value is not None else account["profile_metrics"].get(key)
                for key, value in metrics.items()
            }
            account["profile_links"] = _dedupe_profile_links(
                [*account["profile_links"], *links]
            )
            continue
        item = {
            "record_id": str(record.get("record_id") or ""),
            "scope": scope,
            "scope_label": SOCIAL_SCOPES[scope],
            "observed_at": record.get("observed_at"),
            "preview_text": _social_item_preview(scope, record),
        }
        account["items"].append(item)
        all_items.append(item)

    total = len(all_items)
    remaining = MAX_SOCIAL_REPORT_ITEMS
    ordered_accounts = sorted(accounts.values(), key=lambda value: value["platform"])
    for account in ordered_accounts:
        account["items"].sort(key=lambda value: str(value.get("observed_at") or ""))
        account["items"] = account["items"][:remaining]
        remaining -= len(account["items"])
        if not account.get("display_name") and account["scope_counts"].get(
            "device_media"
        ):
            account["display_name"] = "Jejak di galeri HP"
    return ordered_accounts, total, total > MAX_SOCIAL_REPORT_ITEMS


async def _whatsapp_report_data(
    session_id: str,
    findings: list,
) -> tuple[list[dict], int, int, bool]:
    file_rows = await db.fetchall(
        "SELECT id, source, meta_json FROM files WHERE session_id = ? "
        "AND (lower(source) = 'whatsapp' "
        "OR instr(meta_json, '\"whatsapp_message_id\"') > 0)",
        (session_id,),
    )
    findings_by_file: dict[str, list] = {}
    for finding in findings:
        findings_by_file.setdefault(str(finding["file_id"]), []).append(finding)

    decoded_rows: list[tuple[Any, dict]] = []
    findings_by_whatsapp_message: dict[str, list] = {}
    for row in file_rows:
        try:
            metadata = json.loads(row["meta_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        decoded_rows.append((row, metadata))
        linked_message_ids = {
            str(metadata.get("whatsapp_message_id") or "").strip()
        } - {""}
        media_links = metadata.get("whatsapp_media_links")
        if isinstance(media_links, list):
            linked_message_ids.update(
                str(link.get("message_id") or "").strip()
                for link in media_links[:16]
                if isinstance(link, dict)
            )
            linked_message_ids.discard("")
        for linked_message_id in linked_message_ids:
            findings_by_whatsapp_message.setdefault(linked_message_id, []).extend(
                findings_by_file.get(str(row["id"]), [])
            )

    messages: list[dict] = []
    conversation_ids: set[str] = set()
    for row, metadata in decoded_rows:
        if str(row["source"] or "").casefold() != "whatsapp":
            continue
        conversation_id = str(metadata.get("conversation_id") or "").strip()
        message_id = str(metadata.get("message_id") or "").strip()
        message_type = str(metadata.get("message_type") or "text")[:64]
        direction = str(metadata.get("message_direction") or "UNKNOWN").strip()
        actor_kind = str(metadata.get("message_actor_kind") or "").strip()
        if message_type == "system":
            direction = "UNKNOWN"
            actor_kind = "system"
        if direction not in {"IN", "OUT", "UNKNOWN"}:
            direction = "UNKNOWN"
        if actor_kind not in {
            "self",
            "peer",
            "group_participant",
            "system",
            "unknown",
        }:
            actor_kind = (
                "self"
                if direction == "OUT"
                else "peer"
                if direction == "IN"
                else "unknown"
            )
        if not conversation_id or not message_id:
            continue
        conversation_ids.add(conversation_id)
        linked_findings = [
            *findings_by_file.get(str(row["id"]), []),
            *findings_by_whatsapp_message.get(message_id, []),
        ]
        conversation_name = str(
            metadata.get("conversation_name")
            or metadata.get("display_name")
            or "Percakapan WhatsApp"
        )[:512]
        if conversation_name == "(You)" and not bool(
            metadata.get("conversation_is_self_chat")
        ):
            conversation_name = f"Kontak WhatsApp · {conversation_id[-8:]}"
        messages.append(
            {
                "account_id": str(metadata.get("account_id") or "")[:128] or None,
                "account_slot": (
                    _nonnegative_int(metadata.get("account_slot"))
                    if metadata.get("account_slot") is not None
                    else None
                ),
                "conversation_id": conversation_id,
                "conversation_name": conversation_name,
                "conversation_address": str(
                    metadata.get("conversation_address") or ""
                )[:1024]
                or None,
                "conversation_type": (
                    "group" if metadata.get("conversation_type") == "group" else "chat"
                ),
                "message_id": message_id,
                "direction": direction,
                "direction_evidence": str(
                    metadata.get("message_direction_evidence") or "unavailable"
                )[:64],
                "actor_kind": actor_kind,
                "peer_jid": str(
                    metadata.get("message_peer_jid")
                    or metadata.get("conversation_peer_jid")
                    or ""
                )[:1024]
                or None,
                "participant_jid": str(
                    metadata.get("message_participant_jid") or ""
                )[:1024]
                or None,
                "sender": str(metadata.get("message_sender") or "")[:1024] or None,
                "message_type": message_type,
                "message_type_code": metadata.get("message_type_code"),
                "system_action_type": metadata.get("message_system_action_type"),
                "system_kind": str(metadata.get("message_system_kind") or "")[:128]
                or None,
                "analysis_eligible": bool(
                    metadata.get(
                        "message_analysis_eligible",
                        message_type not in {"system", "call"},
                    )
                ),
                "timestamp": metadata.get("message_timestamp")
                or metadata.get("captured_at"),
                "preview_text": str(
                    metadata.get("message_text") or metadata.get("preview_text") or ""
                )[:131_072]
                or "—",
                "quoted_text": str(metadata.get("quoted_text") or "")[:2_000]
                or None,
                "starred": bool(metadata.get("message_starred")),
                "revoked": bool(metadata.get("message_revoked")),
                "forwarded": _nonnegative_int(
                    metadata.get("message_forward_score")
                )
                > 0,
                "edited_at": metadata.get("message_edited_at"),
                "flagged": bool(linked_findings),
                "finding_labels": sorted(
                    {
                        str(finding["label"])
                        for finding in linked_findings
                        if str(finding["label"] or "").strip()
                    }
                ),
                "review_statuses": sorted(
                    {
                        str(finding["review_status"])
                        for finding in linked_findings
                        if str(finding["review_status"] or "").strip()
                    }
                ),
            }
        )

    messages.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    total = len(messages)
    selected = messages[:MAX_WHATSAPP_REPORT_MESSAGES]
    rooms_by_id: dict[str, dict] = {}
    for message in selected:
        room = rooms_by_id.setdefault(
            message["conversation_id"],
            {
                "conversation_id": message["conversation_id"],
                "name": message["conversation_name"],
                "address": message["conversation_address"],
                "type": message["conversation_type"],
                "messages": [],
            },
        )
        room["messages"].append(message)
    rooms = list(rooms_by_id.values())
    for room in rooms:
        room["messages"].sort(key=lambda item: str(item.get("timestamp") or ""))
        room["finding_count"] = sum(
            bool(message.get("flagged")) for message in room["messages"]
        )
    rooms.sort(
        key=lambda room: max(
            (str(message.get("timestamp") or "") for message in room["messages"]),
            default="",
        ),
        reverse=True,
    )
    return rooms, total, len(conversation_ids), total > MAX_WHATSAPP_REPORT_MESSAGES


async def build_session_report(session_id: str) -> dict:
    row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise KeyError("Session not found")

    findings = await db.fetchall(
        "SELECT * FROM findings WHERE session_id = ? ORDER BY confidence DESC",
        (session_id,),
    )
    files = await db.fetchone(
        "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes),0) AS bytes FROM files WHERE session_id = ?",
        (session_id,),
    )
    progress = json.loads(row["progress_json"])
    timing = json.loads(row["timing_json"])
    social_accounts, social_total, social_truncated = await _social_report_data(session_id)
    inventory = await _session_inventory_counts(session_id)
    device_identity = await _session_device_identity(session_id)
    whatsapp_rooms, whatsapp_total, whatsapp_conversations, whatsapp_truncated = (
        await _whatsapp_report_data(session_id, list(findings))
    )
    participant = None
    try:
        raw_participant = row["participant_json"]
    except (KeyError, IndexError):
        raw_participant = None
    if raw_participant:
        try:
            parsed = json.loads(raw_participant)
            if isinstance(parsed, dict) and (
                str(parsed.get("full_name") or "").strip()
                or str(parsed.get("registration_no") or "").strip()
            ):
                participant = {
                    "full_name": str(parsed.get("full_name") or "").strip(),
                    "registration_no": str(parsed.get("registration_no") or "").strip(),
                    "nik": str(parsed.get("nik") or "").strip() or None,
                    "organization": str(parsed.get("organization") or "").strip() or None,
                }
        except (TypeError, json.JSONDecodeError):
            participant = None

    by_cat: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for f in findings:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        by_layer[f["layer_origin"]] = by_layer.get(f["layer_origin"], 0) + 1
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": PRODUCT_NAME,
        "product_full_name": PRODUCT_FULL_NAME,
        "product_tagline": PRODUCT_TAGLINE,
        "session": {
            "id": row["id"],
            "label": row["label"],
            "device_id": row["device_id"],
            "device_type": row["device_type"],
            "mode": row["mode"],
            "scenario": row["scenario"],
            "status": row["status"],
            "recommendation": row["recommendation"],
            "acquisition_method": progress.get("acquisition_method", "unknown"),
            "authorized_by": progress.get("authorized_by"),
            "authorized_at": progress.get("authorized_at"),
            "authorize_note": progress.get("authorize_note"),
            "participant": participant,
        },
        "device_identity": device_identity,
        "metrics": {
            "files": files["c"] if files else 0,
            "bytes": files["bytes"] if files else 0,
            "findings": len(findings),
            "social_records": social_total,
            "contact_records": inventory["contact_records"],
            "contact_unique": inventory["contact_unique"],
            "contact_unique_names": inventory["contact_unique_names"],
            "sms_by_direction": inventory["sms_by_direction"],
            "recovery": {
                "cache": inventory["recovery_cache"],
                "trash": inventory["recovery_trash"],
            },
            "notes_records": inventory["notes_records"],
            "whatsapp_messages": whatsapp_total,
            "whatsapp_conversations": whatsapp_conversations,
            "timing": timing,
            "progress": progress,
        },
        "breakdown": {
            "by_category": by_cat,
            "by_layer": by_layer,
            "by_source": by_source,
        },
        "social_accounts": social_accounts,
        "social_data": {
            "total_items": social_total,
            "truncated": social_truncated,
            "maximum_items": MAX_SOCIAL_REPORT_ITEMS,
        },
        "whatsapp_rooms": whatsapp_rooms,
        "whatsapp_data": {
            "total_messages": whatsapp_total,
            "total_conversations": whatsapp_conversations,
            "truncated": whatsapp_truncated,
            "maximum_messages": MAX_WHATSAPP_REPORT_MESSAGES,
        },
        "findings": [
            {
                "label": f["label"],
                "category": f["category"],
                "source": f["source"],
                "path": f["path"],
                "confidence": f["confidence"],
                "layer": f["layer_origin"],
                "evidence": f["evidence"],
                "review_status": f["review_status"],
            }
            for f in findings
        ],
    }
    return report


def _rec_badge_class(recommendation: str | None) -> str:
    if recommendation == "TIDAK LULUS":
        return "pill bad"
    if recommendation == "MENUNGGU REVIEW":
        return "pill warn"
    if recommendation == "LULUS":
        return "pill ok"
    return "pill"


def _review_pill(status: str | None) -> str:
    label = REVIEW_STATUS_LABELS.get(status or "", status or "—")
    if status == "confirmed":
        css = "pill bad"
    elif status == "pending":
        css = "pill warn"
    else:
        css = "pill muted"
    return f'<span class="{css}">{_esc(label)}</span>'


def _mode_label(mode: object) -> str:
    key = "" if mode is None else str(mode).strip().lower()
    if key == "full":
        return "Penuh"
    if key == "quick":
        return "Cepat"
    return key or "—"


def _short_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "—"
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _is_opaque_file_id(name: str) -> bool:
    n = name.strip()
    if not n:
        return True
    lower = n.lower()
    if lower.startswith("record_"):
        return True
    stem = n.rsplit(".", 1)[0]
    return len(stem) >= 40 and all(c.isalnum() or c in "_-" for c in stem)


def _short_path(value: object, limit: int = 36) -> str:
    path = str(value or "").strip() or "—"
    if path == "—":
        return path
    base = path.rsplit("/", 1)[-1]
    if base.endswith((".imgmeta", ".vidmeta", ".json")) or len(path) > limit:
        return base if len(base) <= limit else base[: max(1, limit - 1)] + "…"
    if len(path) <= limit:
        return path
    return f"…/{base}" if len(base) < limit else base[: max(1, limit - 1)] + "…"


def _format_generated_at(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b %Y · %H:%M")
    except ValueError:
        return raw


def _executive_summary(
    recommendation: str | None,
    *,
    pending: int,
    confirmed: int,
    rejected: int,
    total: int,
) -> str:
    if recommendation == "LULUS":
        if total == 0:
            return "Tidak ada sinyal indikasi pada sesi ini. Rekomendasi sistem: LULUS."
        return (
            f"Dari {total} sinyal, {confirmed} dikonfirmasi dan {rejected} ditolak. "
            "Rekomendasi sistem: LULUS."
        )
    if recommendation == "MENUNGGU REVIEW":
        return (
            f"Masih ada {pending} temuan menunggu verifikasi analis "
            f"(total sinyal {total}). Pengesahan belum dapat dilakukan."
        )
    if recommendation == "TIDAK LULUS":
        return (
            f"{confirmed} temuan dikonfirmasi analis dari {total} sinyal "
            f"({rejected} ditolak, {pending} menunggu). Rekomendasi sistem: TIDAK LULUS."
        )
    return "Rekomendasi belum tersedia untuk sesi ini."


def _finding_evidence(finding: dict) -> str:
    evidence = finding.get("evidence")
    if isinstance(evidence, str):
        raw = evidence.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                evidence = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return _short_text(raw, 100)
        else:
            return _short_text(raw, 100)

    if isinstance(evidence, dict):
        name = str(evidence.get("name") or evidence.get("file") or "").strip()
        tags = evidence.get("tags")
        tag_bits: list[str] = []
        if isinstance(tags, list):
            tag_bits = [str(t).strip() for t in tags if str(t).strip()][:4]
        elif isinstance(tags, str) and tags.strip():
            tag_bits = [tags.strip()]

        parts: list[str] = []
        if name and not _is_opaque_file_id(name):
            parts.append(name)
        if tag_bits:
            parts.append("[" + "+".join(tag_bits) + "]" if len(tag_bits) > 1 else tag_bits[0])
        keyframes = evidence.get("keyframes")
        if keyframes not in (None, "", 0) and not tag_bits:
            parts.append(f"{keyframes} keyframe")
        if parts:
            return _short_text(" · ".join(parts), 100)
        for key in ("snippet", "text", "ocr", "summary"):
            if evidence.get(key):
                return _short_text(evidence.get(key), 100)
        return "—"

    return _short_text(evidence, 100)


def _social_account_html(account: dict) -> str:
    username = str(account.get("username") or "").strip().removeprefix("@")
    display_name = str(account.get("display_name") or "").strip()
    if display_name in {"", "—"}:
        identity = f"@{username}" if username else "—"
    elif username:
        identity = f"{display_name} · @{username}"
    else:
        identity = display_name
    metrics = account.get("profile_metrics") if isinstance(account.get("profile_metrics"), dict) else {}
    metric_bits = []
    for key, label in (("posts", "postingan"), ("followers", "pengikut"), ("following", "diikuti"), ("friends", "teman")):
        value = metrics.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metric_bits.append(f"{value} {label}")
    metric_html = (
        f"<p class=\"muted\">{_esc(' · '.join(metric_bits))}</p>" if metric_bits else ""
    )
    return (
        "<article class=\"account-card\">"
        f"<h3>{_esc(_social_account_heading(account))}</h3>"
        f"<p class=\"muted\">{_esc(identity)}</p>"
        f"<p>{_esc(_short_text(account.get('bio') or 'Bio tidak terbaca', 220))}</p>"
        f"{metric_html}"
        f"<p class=\"links\">{_esc(', '.join(account.get('profile_links', [])) or '—')}</p>"
        "</article>"
    )


def report_to_html(report: dict, *, print_mode: bool = False) -> str:
    s = report["session"]
    m = report["metrics"]
    b = report["breakdown"]
    progress = m.get("progress") if isinstance(m.get("progress"), dict) else {}
    timing = m.get("timing") if isinstance(m.get("timing"), dict) else {}
    findings = list(report.get("findings") or [])
    social_accounts = list(report.get("social_accounts") or [])
    whatsapp_rooms = list(report.get("whatsapp_rooms") or [])

    pending = sum(1 for f in findings if f.get("review_status") == "pending")
    confirmed = sum(1 for f in findings if f.get("review_status") == "confirmed")
    rejected = sum(1 for f in findings if f.get("review_status") == "rejected")
    total_findings = int(m.get("findings") or len(findings))
    recommendation = s.get("recommendation")
    summary = _executive_summary(
        recommendation,
        pending=pending,
        confirmed=confirmed,
        rejected=rejected,
        total=total_findings,
    )

    findings_rows = "".join(
        "<tr>"
        f"<td class=\"label-cell\">{_esc(f.get('label'))}</td>"
        f"<td>{_esc(_report_label(f.get('source'), REPORT_SOURCE_LABELS))}</td>"
        f"<td class=\"num-cell\">{float(f.get('confidence') or 0):.0%}</td>"
        f"<td class=\"status-cell\">{_review_pill(f.get('review_status'))}</td>"
        f"<td class=\"evidence-file\">"
        f"<span>{_esc(_finding_evidence(f))}</span>"
        f"<code title=\"{_esc(str(f.get('path') or ''))}\">{_esc(_short_path(f.get('path')))}</code>"
        f"</td>"
        "</tr>"
        for f in findings[:200]
    ) or '<tr><td colspan="5">Tidak ada temuan</td></tr>'

    cat_items = (
        "".join(
            f"<li><span>{_esc(_report_label(k, REPORT_CATEGORY_LABELS))}</span>"
            f"<strong>{_esc(v)}</strong></li>"
            for k, v in sorted(
                (b.get("by_category") or {}).items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )
        )
        or "<li><span>Tidak ada kategori</span><strong>0</strong></li>"
    )

    source_items = (
        "".join(
            f"<li><span>{_esc(_report_label(k, REPORT_SOURCE_LABELS))}</span>"
            f"<strong>{_esc(v)}</strong></li>"
            for k, v in sorted(
                (b.get("by_source") or {}).items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )
        )
        or "<li><span>Tidak ada sumber</span><strong>0</strong></li>"
    )

    files_count = int(m.get("files") or 0)
    bytes_label = _fmt_bytes(m.get("bytes", 0))
    analyzed = int(progress.get("files_analyzed") or 0)
    evidence_stats = f"""
    <li><span>Total temuan</span><strong>{_esc(total_findings)}</strong></li>
    <li><span>Dikonfirmasi</span><strong>{confirmed}</strong></li>
    <li><span>Ditolak</span><strong>{rejected}</strong></li>
    <li><span>Menunggu verifikasi</span><strong>{pending}</strong></li>
    <li><span>Berkas diperiksa</span><strong>{_esc(files_count)} · {_esc(bytes_label)}</strong></li>
"""
    if analyzed and analyzed != files_count:
        evidence_stats += (
            f"<li><span>Berkas dianalisis</span><strong>{_esc(analyzed)}</strong></li>"
        )
    contact_unique = int(m.get("contact_unique") or 0)
    contact_records = int(m.get("contact_records") or 0)
    if contact_records:
        evidence_stats += (
            "<li><span>Kontak</span>"
            f"<strong>{_esc(contact_unique)} unik · "
            f"{_esc(contact_records)} rekam</strong></li>"
        )
    sms_direction = (
        m.get("sms_by_direction")
        if isinstance(m.get("sms_by_direction"), dict)
        else {}
    )
    sms_total = sum(int(value or 0) for value in sms_direction.values())
    if sms_total:
        received = int(sms_direction.get("received") or 0)
        sent = int(sms_direction.get("sent") or 0)
        evidence_stats += (
            "<li><span>SMS</span>"
            f"<strong>{_esc(sms_total)} · {received} masuk · "
            f"{sent} terkirim</strong></li>"
        )
    notes_records = int(m.get("notes_records") or 0)
    if notes_records:
        evidence_stats += (
            f"<li><span>Catatan Android</span><strong>{_esc(notes_records)}</strong></li>"
        )

    recovery = m.get("recovery") if isinstance(m.get("recovery"), dict) else {}
    recovery_metric = ""
    cache_count = int(recovery.get("cache") or 0)
    trash_count = int(recovery.get("trash") or 0)
    if cache_count or trash_count or progress.get("recovery_state"):
        state_label = _report_label(
            progress.get("recovery_state"), RECOVERY_STATE_LABELS
        )
        recovered_parts = []
        if trash_count:
            recovered_parts.append(f"{trash_count} sampah")
        if cache_count:
            recovered_parts.append(f"{cache_count} pratinjau cache")
        if not recovered_parts:
            recovered_parts.append(f"{progress.get('recovery_captured', 0)} item")
        if progress.get("recovery_state"):
            recovered_parts.append(f"{progress.get('recovery_bytes', 0)} bytes")
            warning_count = int(progress.get("recovery_warning_count") or 0)
            cache_sources = int(progress.get("recovery_cache_sources") or 0)
            if warning_count:
                recovered_parts.append(f"{warning_count} peringatan")
            if cache_sources:
                recovered_parts.append(f"{cache_sources} sumber cache")
            recovered_parts.append(state_label)
        recovery_metric = (
            "<li>Recovery sampah Android: "
            f"{_esc(' · '.join(recovered_parts))}</li>"
        )

    ios_library_metric = ""
    if progress.get("ios_library_state"):
        ios_library_metric = (
            "<li>Recovery Photos iOS: "
            f"Hidden {_esc(progress.get('ios_hidden_captured', 0))} · "
            f"baru dihapus {_esc(progress.get('ios_recently_deleted_captured', 0))} · "
            f"cache {_esc(progress.get('ios_cache_captured', 0))} · "
            f"jejak purge {_esc(progress.get('ios_deleted_metadata_captured', 0))} · "
            f"{_esc(progress.get('ios_library_warning_count', 0))} peringatan</li>"
        )

    whatsapp_metric = ""
    if progress.get("whatsapp_state"):
        whatsapp_state_labels = {
            "not_installed": "tidak terpasang",
            "not_signed_in": "belum login nomor",
            "ui_automation": "otomasi UI",
            "parsing": "parsing",
            "parse_unavailable": "backup diperoleh, parser tidak tersedia",
            "complete": "selesai",
        }
        state = str(progress.get("whatsapp_state") or "")
        whatsapp_metric = (
            "<li>WhatsApp: "
            f"{_esc(progress.get('whatsapp_messages', 0))} pesan · "
            f"{_esc(progress.get('whatsapp_conversations', 0))} percakapan · "
            f"UI {_esc(progress.get('whatsapp_ui_attempt', 0))}/"
            f"{_esc(progress.get('whatsapp_ui_attempts', 4))} · "
            f"{_esc(whatsapp_state_labels.get(state, state))}</li>"
        )

    notes_metric = ""
    if progress.get("notes_state"):
        notes_state_labels = {
            "complete": "selesai",
            "partial": "sebagian",
            "unavailable": "tidak tersedia",
            "not_installed": "aplikasi tidak ditemukan",
        }
        state = str(progress.get("notes_state") or "")
        notes_metric = (
            "<li>Catatan Android: "
            f"{_esc(progress.get('notes_captured', 0))} diperoleh · "
            f"{_esc(progress.get('notes_skipped', 0))} dilewati · "
            f"{_esc(progress.get('notes_warning_count', 0))} peringatan · "
            f"{_esc(notes_state_labels.get(state, state))}</li>"
        )

    rec = recommendation or "—"
    rec_badge = f'<span class="{_rec_badge_class(recommendation)}">{_esc(rec)}</span>'

    participant = s.get("participant") if isinstance(s.get("participant"), dict) else None
    has_participant = bool(
        participant
        and (
            str(participant.get("full_name") or "").strip()
            or str(participant.get("registration_no") or "").strip()
        )
    )
    if has_participant:
        nik_val = str(participant.get("nik") or "").strip() or "—"
        org_val = str(participant.get("organization") or "").strip() or "—"
        device_identity = (
            report.get("device_identity")
            if isinstance(report.get("device_identity"), dict)
            else {}
        )
        operator_name = str(participant.get("full_name") or "").strip()
        device_names = [
            str(name)
            for name in (device_identity.get("names") or [])
            if str(name).strip()
            and str(name).casefold() != operator_name.casefold()
        ]
        device_name_row = ""
        if device_names:
            source_label = ""
            sources = device_identity.get("sources") or []
            if sources and isinstance(sources[0], dict):
                source_label = str(sources[0].get("label") or "")
            device_name_row = (
                "<div><span>Nama di perangkat</span>"
                f"<strong>{_esc(device_names[0])}"
                f"{(' · ' + _esc(source_label)) if source_label else ''}"
                "</strong></div>"
            )
        if nik_val == "—" and device_identity.get("nik_candidates"):
            nik_val = (
                f"{device_identity['nik_candidates'][0]} "
                "(dari dokumen, belum diverifikasi)"
            )
        if org_val == "—" and device_identity.get("organizations"):
            org_val = f"{device_identity['organizations'][0]} (dari dokumen)"
        identity_section = f"""
<section class="panel">
  <h2>Identitas peserta seleksi</h2>
  <div class="meta-grid">
    <div><span>Nama lengkap</span><strong>{_esc(participant.get('full_name') or '—')}</strong></div>
    {device_name_row}
    <div><span>No. peserta / registrasi</span><strong>{_esc(participant.get('registration_no') or '—')}</strong></div>
    <div><span>NIK</span><strong>{_esc(nik_val)}</strong></div>
    <div><span>Instansi / formasi</span><strong>{_esc(org_val)}</strong></div>
    <div><span>Rekomendasi</span><strong>{rec_badge}</strong></div>
  </div>
  <div class="kpi-row">
    <div class="kpi"><span>Menunggu</span><strong>{pending}</strong></div>
    <div class="kpi"><span>Dikonfirmasi</span><strong>{confirmed}</strong></div>
    <div class="kpi"><span>Ditolak</span><strong>{rejected}</strong></div>
    <div class="kpi"><span>Total temuan</span><strong>{_esc(total_findings)}</strong></div>
  </div>
</section>

<section class="panel">
  <h2>Detail teknis akuisisi</h2>
  <div class="meta-grid">
    <div><span>Sesi</span><strong><code title="{_esc(s.get('id'))}">{_esc(str(s.get('id', ''))[:8])}</code></strong></div>
    <div><span>Label sesi</span><strong>{_esc(s.get('label'))}</strong></div>
    <div><span>ID perangkat</span><strong>{_esc(s.get('device_id'))} ({_esc(s.get('device_type'))})</strong></div>
    <div><span>Mode</span><strong>{_esc(_mode_label(s.get('mode')))}</strong></div>
    <div><span>Cara ambil data</span><strong>{_esc(_report_method(s.get('acquisition_method')))}</strong></div>
  </div>
</section>
"""
    else:
        identity_section = f"""
<section class="panel">
  <h2>Identitas peserta seleksi</h2>
  <p class="muted" style="margin:0 0 12px;color:#5c6570;font-size:13px">
    Identitas peserta belum diisi saat akuisisi. Detail teknis sesi ditampilkan di bawah.
  </p>
  <div class="meta-grid">
    <div><span>Sesi</span><strong><code title="{_esc(s.get('id'))}">{_esc(str(s.get('id', ''))[:8])}</code></strong></div>
    <div><span>Label / perangkat</span><strong>{_esc(s.get('label'))}</strong></div>
    <div><span>ID perangkat</span><strong>{_esc(s.get('device_id'))} ({_esc(s.get('device_type'))})</strong></div>
    <div><span>Mode</span><strong>{_esc(_mode_label(s.get('mode')))}</strong></div>
    <div><span>Cara ambil data</span><strong>{_esc(_report_method(s.get('acquisition_method')))}</strong></div>
    <div><span>Rekomendasi</span><strong>{rec_badge}</strong></div>
  </div>
  <div class="kpi-row">
    <div class="kpi"><span>Menunggu</span><strong>{pending}</strong></div>
    <div class="kpi"><span>Dikonfirmasi</span><strong>{confirmed}</strong></div>
    <div class="kpi"><span>Ditolak</span><strong>{rejected}</strong></div>
    <div class="kpi"><span>Total temuan</span><strong>{_esc(total_findings)}</strong></div>
  </div>
</section>
"""

    letterhead_participant = ""
    if has_participant:
        letterhead_participant = (
            f"{_esc(participant.get('full_name'))}<br/>"
            f"{_esc(participant.get('registration_no') or '')}<br/>"
        )

    account_blocks = "".join(_social_account_html(account) for account in social_accounts)
    social_rows = "".join(
        "<tr>"
        f"<td>{_esc(account.get('platform'))}</td>"
        f"<td>{_esc(item.get('scope_label'))}</td>"
        f"<td>{_esc(item.get('observed_at') or '—')}</td>"
        f"<td class=\"preview\">{_esc(_short_text(item.get('preview_text'), 160))}</td>"
        "</tr>"
        for account in social_accounts
        for item in account.get("items", [])
    )
    has_social = bool(social_accounts) or bool(social_rows)

    if s.get("authorized_by"):
        when = f" · {_esc(_format_generated_at(s.get('authorized_at')))}" if s.get("authorized_at") else ""
        authorize_block = (
            "<section class=\"panel signature\">"
            "<h2>Pengesahan pimpinan</h2>"
            f"<p><span class=\"pill ok\">Disahkan</span> "
            f"<strong>{_esc(s['authorized_by'])}</strong>{when}</p>"
            f"<p class=\"note\">{_esc(s.get('authorize_note') or '—')}</p>"
            "</section>"
        )
    else:
        authorize_block = (
            "<section class=\"panel signature\">"
            "<h2>Pengesahan pimpinan</h2>"
            "<p class=\"muted\">Belum disahkan — ruang tanda tangan untuk pimpinan.</p>"
            "<div class=\"sign-grid\">"
            "<div><span>Nama / jabatan</span><div class=\"sign-line\"></div></div>"
            "<div><span>Tanggal</span><div class=\"sign-line\"></div></div>"
            "<div class=\"sign-full\"><span>Tanda tangan</span><div class=\"sign-box\"></div></div>"
            "</div>"
            "</section>"
        )

    social_section = ""
    if has_social:
        social_section = f"""
<section class="panel">
  <h2>Data akun &amp; sosial</h2>
  {account_blocks or '<p class="muted">Tidak ada profil sosial terverifikasi.</p>'}
  <table class="data">
    <thead><tr><th>Platform</th><th>Jenis</th><th>Waktu</th><th>Preview</th></tr></thead>
    <tbody>{social_rows or '<tr><td colspan="4">Tidak ada aktivitas sosial yang dikoleksi.</td></tr>'}</tbody>
  </table>
</section>
"""

    whatsapp_row_parts: list[str] = []
    for room in whatsapp_rooms:
        for message in room.get("messages", []):
            if message.get("actor_kind") == "system":
                direction = "Sistem"
                sender = "WhatsApp"
            elif message.get("direction") == "OUT":
                direction = "Keluar"
                sender = "Anda"
            elif message.get("direction") == "IN":
                direction = "Masuk"
                sender = message.get("sender") or "—"
            else:
                direction = "Tidak diketahui"
                sender = message.get("sender") or "—"
            finding_badge = (
                '<span class="pill bad">Temuan</span>'
                if message.get("flagged")
                else "—"
            )
            whatsapp_row_parts.append(
                "<tr>"
                f"<td>{_esc(room.get('name') or 'Percakapan WhatsApp')}</td>"
                f"<td>{_esc(direction)}</td>"
                f"<td>{_esc(sender)}</td>"
                f"<td>{_esc(_format_generated_at(message.get('timestamp')))}</td>"
                f"<td class=\"preview\">"
                f"{_esc(_short_text(message.get('preview_text'), 180))}</td>"
                f"<td>{finding_badge}</td>"
                "</tr>"
            )
    whatsapp_rows = "".join(whatsapp_row_parts)
    whatsapp_data = (
        report.get("whatsapp_data")
        if isinstance(report.get("whatsapp_data"), dict)
        else {}
    )
    whatsapp_section = ""
    if whatsapp_rooms:
        truncated_note = (
            "<p class=\"muted\">Tampilan dibatasi "
            f"{_esc(whatsapp_data.get('maximum_messages'))} dari "
            f"{_esc(whatsapp_data.get('total_messages'))} pesan.</p>"
            if whatsapp_data.get("truncated")
            else ""
        )
        whatsapp_section = f"""
<section class="panel">
  <h2>Percakapan WhatsApp</h2>
  <p class="muted">{_esc(whatsapp_data.get('total_messages', 0))} pesan dari {_esc(whatsapp_data.get('total_conversations', 0))} percakapan. Penanda Temuan mengikuti hasil analisis dan review pesan yang sama.</p>
  {truncated_note}
  <table class="data">
    <thead><tr><th>Ruang chat</th><th>Arah</th><th>Pengirim</th><th>Waktu</th><th>Pesan</th><th>Status</th></tr></thead>
    <tbody>{whatsapp_rows}</tbody>
  </table>
</section>
"""

    body_class = "doc print-doc" if print_mode else "doc screen-doc"
    toolbar = ""
    if print_mode:
        toolbar = (
            '<div class="toolbar no-print">'
            "<div><strong>Pratinjau cetak SATRIA</strong>"
            "<p class=\"muted\" style=\"margin:4px 0 0\">"
            "Di dialog cetak Chrome: More settings → uncheck "
            "<em>Headers and footers</em> agar tanggal/URL tidak ikut tercetak. "
            "Lalu Save as PDF."
            "</p></div>"
            '<button type="button" onclick="window.print()">Cetak / Simpan PDF</button>'
            "</div>"
        )
    auto_print = (
        "<script>window.addEventListener('load',function(){setTimeout(function(){window.print();},250);});</script>"
        if print_mode
        else ""
    )
    generated = _format_generated_at(report.get("generated_at"))

    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(PRODUCT_NAME)} · Laporan · {_esc(str(s.get('id', ''))[:8])}</title>
<style>
:root {{
  --ink: #1a1a1a;
  --muted: #5c6570;
  --line: #d7dbe0;
  --panel: #ffffff;
  --bg: #f4f5f7;
  --brand: #9b1c2e;
  --gold: #8a6a12;
  --ok: #1f6b45;
  --warn: #9a6700;
  --bad: #b42318;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  color: var(--ink);
  background: var(--bg);
  line-height: 1.45;
}}
.screen-doc {{
  --ink: #ece8df;
  --muted: #9aa3b2;
  --line: rgba(255,255,255,0.12);
  --panel: #12161f;
  --bg: #0a0c10;
  --brand: #e04555;
  --gold: #e8c547;
  --ok: #3d9b6c;
  --warn: #e6a23c;
  --bad: #e04555;
}}
.wrap {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
.toolbar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  background: var(--panel);
}}
.toolbar button {{
  border: 1px solid var(--gold);
  background: #fff8e1;
  color: #1a1208;
  padding: 8px 12px;
  font-weight: 700;
  cursor: pointer;
}}
header.cover {{
  border: 1px solid var(--line);
  border-top: 4px solid var(--brand);
  background: var(--panel);
  padding: 0;
  margin-bottom: 14px;
  overflow: hidden;
}}
.letterhead {{
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--line);
}}
.letterhead-brand .brand {{
  font-size: 1rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--brand);
  font-weight: 800;
  margin: 0;
}}
.letterhead-brand .org {{
  margin: 4px 0 0;
  font-size: 0.78rem;
  color: var(--muted);
  max-width: 42ch;
  line-height: 1.35;
}}
.letterhead-meta {{
  text-align: right;
  font-size: 0.72rem;
  color: var(--muted);
  line-height: 1.45;
  white-space: nowrap;
}}
.letterhead-meta strong {{
  display: block;
  color: var(--ink);
  font-size: 0.8rem;
}}
.cover-body {{
  padding: 14px 20px 16px;
}}
.cover-body h1 {{
  margin: 0 0 6px;
  font-size: 1.25rem;
}}
.cover-rec {{
  margin: 0 0 12px;
  font-size: 0.9rem;
}}
.summary {{
  margin-top: 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--brand);
  background: rgba(155, 28, 46, 0.04);
}}
.screen-doc .summary {{ background: rgba(224, 69, 85, 0.08); }}
.summary strong {{ display: block; margin-bottom: 4px; font-size: 0.72rem; letter-spacing: 0.06em; text-transform: uppercase; }}
.doc-footer {{
  margin-top: 18px;
  padding-top: 10px;
  border-top: 2px solid var(--ink);
  font-size: 0.7rem;
  color: var(--muted);
  display: grid;
  gap: 4px;
}}
.doc-footer .confidential {{
  color: var(--brand);
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}}
.doc-footer .meta-line {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
}}
.panel {{
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 6px;
  padding: 16px 18px;
  margin-bottom: 12px;
}}
h2 {{
  margin: 0 0 12px;
  font-size: 0.82rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--gold);
}}
.meta-grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 14px;
}}
.meta-grid > div {{
  min-width: 0;
}}
.meta-grid span {{
  display: block;
  font-size: 0.65rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}}
.meta-grid strong {{
  display: block;
  overflow-wrap: anywhere;
  word-break: break-word;
}}
.meta-grid code {{
  display: block;
  font-size: 0.72rem;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-all;
}}
.kpi-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}}
.kpi {{
  min-width: 92px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 4px;
}}
.kpi span {{ display: block; font-size: 0.62rem; color: var(--muted); text-transform: uppercase; }}
.kpi strong {{ font-size: 1.1rem; font-variant-numeric: tabular-nums; }}
.table-wrap {{
  width: 100%;
  overflow-x: auto;
}}
table.data {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}}
table.data th, table.data td {{
  border-bottom: 1px solid var(--line);
  padding: 7px 8px;
  text-align: left;
  vertical-align: top;
  overflow: hidden;
}}
table.data th {{
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--muted);
  white-space: normal;
  line-height: 1.25;
  overflow-wrap: break-word;
}}
table.data.findings-summary {{
  table-layout: fixed;
  width: 100%;
}}
table.data.findings-summary col.c-label {{ width: 28%; }}
table.data.findings-summary col.c-source {{ width: 6.2rem; }}
table.data.findings-summary col.c-pct {{ width: 5.2rem; }}
table.data.findings-summary col.c-status {{ width: 7.6rem; }}
table.data.findings-summary col.c-bukti {{ width: 26%; }}
.label-cell {{
  overflow-wrap: break-word;
  word-break: normal;
}}
.num-cell {{
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}}
.status-cell {{
  white-space: nowrap;
}}
.evidence-file span,
.evidence-file code {{
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
}}
.evidence-file code {{
  margin-top: 2px;
  font-size: 0.72rem;
  color: var(--muted);
}}
code {{ font-size: 0.74rem; }}
.preview {{ white-space: pre-wrap; max-width: 420px; }}
.pill {{
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 700;
  white-space: nowrap;
}}
.pill.bad {{ border-color: var(--bad); color: var(--bad); }}
.pill.warn {{ border-color: var(--warn); color: var(--warn); }}
.pill.ok {{ border-color: var(--ok); color: var(--ok); }}
.pill.muted {{ color: var(--muted); }}
.account-card {{ border-top: 1px solid var(--line); padding: 10px 0; }}
.account-card:first-child {{ border-top: 0; padding-top: 0; }}
.account-card h3 {{ margin: 0 0 4px; font-size: 0.95rem; }}
.muted {{ color: var(--muted); font-size: 0.85rem; }}
.links {{ font-family: ui-monospace, monospace; font-size: 0.75rem; word-break: break-all; }}
ul.stats {{ list-style: none; margin: 0; padding: 0; }}
ul.stats li {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}}
ul.stats li span {{ color: var(--muted); }}
.sign-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 12px;
}}
.sign-grid .sign-full {{
  grid-column: 1 / -1;
}}
.sign-grid span {{
  display: block;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 8px;
}}
.sign-line {{
  border-bottom: 1px solid var(--ink);
  height: 28px;
}}
.sign-box {{
  border: 1px solid var(--ink);
  height: 64px;
  box-sizing: border-box;
}}
footer {{
  margin-top: 16px;
  font-size: 0.72rem;
  color: var(--muted);
}}
.note {{ margin: 8px 0 0; color: var(--muted); }}
@media print {{
  body, .print-doc, .screen-doc {{
    background: #fff !important;
    color: #111 !important;
  }}
  .wrap {{ max-width: none; padding: 0; }}
  .no-print {{ display: none !important; }}
  .panel, header.cover {{
    background: #fff !important;
    border-color: #ccc !important;
    box-shadow: none !important;
  }}
  header.cover,
  .panel.signature,
  .sign-grid,
  .sign-box,
  table.data.findings-summary tr {{
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  h1, h2, .brand {{ color: #111 !important; }}
  .letterhead-brand .brand {{ color: var(--brand) !important; }}
  .summary {{ background: #f5f5f5 !important; }}
  .pill {{ border-color: #666 !important; color: #111 !important; }}
  table.data {{ font-size: 9.5pt; }}
  table.data th, table.data td {{
    border-color: #ccc !important;
    padding: 5px 6px;
  }}
  .meta-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)) !important; }}
  .meta-grid code {{ word-break: break-all !important; white-space: normal !important; }}
  .evidence-file span,
  .evidence-file code {{
    white-space: nowrap !important;
    word-break: normal !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
  }}
  .doc-footer {{
    border-top-color: #111 !important;
    color: #444 !important;
  }}
  .doc-footer .confidential {{ color: #9b1c2e !important; }}
  a {{ color: inherit; text-decoration: none; }}
}}
@page {{ size: A4; margin: 12mm 12mm 14mm 12mm; }}
</style></head>
<body class="{body_class}">
<div class="wrap">
{toolbar}
<header class="cover">
  <div class="letterhead">
    <div class="letterhead-brand">
      <p class="brand">{_esc(PRODUCT_NAME)}</p>
      <p class="org">{_esc(PRODUCT_FULL_NAME)}</p>
    </div>
    <div class="letterhead-meta">
      <strong>Dokumen internal panitia</strong>
      {letterhead_participant}Sesi {_esc(str(s.get('id', ''))[:8])}<br/>
      {_esc(generated)}
    </div>
  </div>
  <div class="cover-body">
    <h1>Laporan Hasil Analisis</h1>
    <p class="cover-rec">Rekomendasi sistem: {rec_badge}</p>
    <div class="summary">
      <strong>Ringkasan eksekutif</strong>
      {_esc(summary)}
    </div>
  </div>
</header>

{identity_section}

{authorize_block}

<section class="panel">
  <h2>Ringkasan temuan</h2>
  <div class="table-wrap">
  <table class="data findings-summary">
    <colgroup>
      <col class="c-label" />
      <col class="c-source" />
      <col class="c-pct" />
      <col class="c-status" />
      <col class="c-bukti" />
    </colgroup>
    <thead>
      <tr>
        <th>Label</th>
        <th>Sumber</th>
        <th>Yakin</th>
        <th>Status</th>
        <th>Bukti</th>
      </tr>
    </thead>
    <tbody>{findings_rows}</tbody>
  </table>
  </div>
</section>

{social_section}

{whatsapp_section}

<section class="panel">
  <h2>Ringkasan bukti</h2>
  <ul class="stats">
    {evidence_stats}
    {recovery_metric}
    {ios_library_metric}
    {whatsapp_metric}
    {notes_metric}
  </ul>
  <h2 style="margin-top:16px">Per kategori</h2>
  <ul class="stats">{cat_items}</ul>
  <h2 style="margin-top:16px">Per jenis sumber</h2>
  <ul class="stats">{source_items}</ul>
</section>

<footer class="doc-footer">
  <div class="confidential">Rahasia — hanya untuk keperluan panitia seleksi</div>
  <div class="meta-line">
    <span>{_esc(PRODUCT_NAME)}</span>
    <span>Sesi {_esc(str(s.get('id', ''))[:8])}</span>
    <span>Dibuat {_esc(generated)}</span>
  </div>
</footer>
</div>
{auto_print}
</body></html>"""


async def save_session_report(session_id: str) -> Path:
    report = await build_session_report(session_id)
    out_dir = settings.data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{session_id}.json"
    html_path = out_dir / f"{session_id}.html"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(report_to_html(report), encoding="utf-8")
    return html_path
