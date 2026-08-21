from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.social_ocr import repair_ocr_link_text
from app.core.config import settings
from app.core.db import db

SOCIAL_PACKAGES = {
    "com.instagram.android": "Instagram",
    "com.twitter.android": "X / Twitter",
    "com.facebook.katana": "Facebook",
}
SOCIAL_SCOPES = {
    "own_profile": "Profil akun",
    "own_posts": "Postingan akun",
    "own_tweets": "Tweet akun",
    "own_tweets": "Tweet akun",
    "own_story_archive": "Arsip story",
    "own_story_archive": "Arsip story",
    "own_comments": "Komentar akun",
    "own_comments": "Komentar akun",
    "own_replies": "Balasan akun",
    "own_replies": "Balasan akun",
}
MAX_SOCIAL_REPORT_ITEMS = 500
MAX_SOCIAL_PREVIEW_CHARS = 2_000
REPORT_CATEGORY_LABELS = {
    "ketelanjangan": "Ketelanjangan / konten eksplisit",
}
REPORT_SOURCE_LABELS = {
    "recovered_trash": "Sampah / media terhapus",
    "ios_hidden": "Photos Tersembunyi (iOS)",
    "ios_recently_deleted": "Baru Dihapus (iOS)",
    "ios_recovered_cache": "Cache / preview Photos (iOS)",
    "ios_deleted_metadata": "Jejak hapus permanen Photos (iOS)",
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
    "unknown": "Tidak diketahui",
}
RECOVERY_STATE_LABELS = {
    "scanning": "memindai",
    "complete": "selesai",
    "partial": "sebagian",
    "unavailable": "tidak tersedia",
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
            and not re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", line.strip())
        ]
        cleaned = "\n".join(structured_lines).strip()[:4096]
        if cleaned:
            return cleaned
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
            or re.fullmatch(r"[0-9][0-9.,]*", key)
            or re.fullmatch(r"(?i)\d{1,2}:\d{2}(\s*[ap]m)?", line.strip())
            or any(link in key for link in link_keys)
        ):
            continue
        output.append(line)
        if len(output) >= 20:
            break
    value = "\n".join(output).strip()[:4096]
    return value or None


def _is_profile_metric_chrome(line: str) -> bool:
    if PROFILE_METRIC_TOKEN.search(line) is None:
        return False
    remainder = PROFILE_METRIC_TOKEN.sub("", line)
    return PROFILE_METRIC_SEPARATOR.sub("", remainder) == ""


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
        if int(value["top"]) > profile_start
        and value["text"].strip().casefold() in {"edit", "sunting", "share", "bagikan"}
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
    return value or None


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


def _social_item_preview(scope: str, text: object) -> str | None:
    if scope == "own_story_archive":
        return _archive_social_preview(text) or _social_preview(text)
    return _social_preview(text)


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
        "WHERE r.session_id = ? AND r.source_kind = 'visible_ui' "
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


async def _social_report_data(session_id: str) -> tuple[list[dict], int, bool]:
    accounts: dict[str, dict] = {}
    all_items: list[dict] = []
    for record in await _load_social_records(session_id):
        package_name = record.get("source_app")
        if package_name not in SOCIAL_PACKAGES:
            continue
        metadata = _record_metadata(record)
        scope = metadata.get("social_scope")
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
            "preview_text": _social_item_preview(scope, record.get("normalized_text")),
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
    return ordered_accounts, total, total > MAX_SOCIAL_REPORT_ITEMS


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

    by_cat: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for f in findings:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        by_layer[f["layer_origin"]] = by_layer.get(f["layer_origin"], 0) + 1
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        },
        "metrics": {
            "files": files["c"] if files else 0,
            "bytes": files["bytes"] if files else 0,
            "findings": len(findings),
            "social_records": social_total,
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


def report_to_html(report: dict) -> str:
    s = report["session"]
    m = report["metrics"]
    b = report["breakdown"]
    rows = "".join(
        "<tr>"
        f"<td>{_esc(f['label'])}</td>"
        f"<td>{_esc(_report_label(f['category'], REPORT_CATEGORY_LABELS))}</td>"
        f"<td>{_esc(_report_label(f['source'], REPORT_SOURCE_LABELS))}</td>"
        f"<td>{_esc(f['layer'])}</td>"
        f"<td>{f['confidence']:.0%}</td>"
        f"<td><code>{_esc(f['path'])}</code></td>"
        "</tr>"
        for f in report["findings"][:200]
    )
    cat = (
        "".join(
            f"<li>{_esc(_report_label(k, REPORT_CATEGORY_LABELS))}: "
            f"<b>{_esc(v)}</b></li>"
            for k, v in b["by_category"].items()
        )
        or "<li>-</li>"
    )
    progress = m.get("progress") if isinstance(m.get("progress"), dict) else {}
    recovery_state = progress.get("recovery_state")
    recovery_metric = ""
    if recovery_state:
        state_label = _report_label(recovery_state, RECOVERY_STATE_LABELS)
        recovery_metric = (
            "<li>Recovery sampah Android: "
            f"{_esc(progress.get('recovery_captured', 0))} item · "
            f"{_esc(progress.get('recovery_bytes', 0))} bytes · "
            f"{_esc(state_label)} · "
            f"{_esc(progress.get('recovery_warning_count', 0))} peringatan · "
            f"cache { _esc(progress.get('recovery_cache_captured', 0))} preview/"
            f"{ _esc(progress.get('recovery_cache_sources', 0))} sumber</li>"
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
    rec = s["recommendation"] or "-"
    if s["recommendation"] == "TIDAK LULUS":
        rec_class = "bad"
    elif s["recommendation"] == "MENUNGGU REVIEW":
        rec_class = "warn"
    else:
        rec_class = ""
    rec_badge = f'<span class="badge {rec_class}">{_esc(rec)}</span>'
    social_accounts = report.get("social_accounts", [])
    account_rows = "".join(
        "<div class=\"account\">"
        f"<h3>{_esc(_social_account_heading(account))}</h3>"
        f"<div><b>Nama tampilan:</b> {_esc(account.get('display_name') or '-')}</div>"
        f"<div><b>Bio / profil terlihat:</b><br>{_esc(account.get('bio') or '-')}</div>"
        f"<div><b>Link profil:</b> {_esc(', '.join(account.get('profile_links', [])) or '-')}</div>"
        f"<div><b>Metrik profil:</b> {_esc(json.dumps(account.get('profile_metrics', {}), ensure_ascii=False))}</div>"
        "</div>"
        for account in social_accounts
    )
    social_rows = "".join(
        "<tr>"
        f"<td>{_esc(account['platform'])}</td>"
        f"<td>{_esc(item['scope_label'])}</td>"
        f"<td>{_esc(item.get('observed_at') or '-')}</td>"
        f"<td class=\"preview\">{_esc(item.get('preview_text') or '-')}</td>"
        "</tr>"
        for account in social_accounts
        for item in account.get("items", [])
    )
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"/>
<title>SADT Report — {_esc(s['id'][:8])}</title>
<style>
body{{font-family:ui-monospace,Menlo,monospace;background:#061018;color:#d7ece8;padding:24px}}
h1,h2{{color:#00e5c8;letter-spacing:.06em;text-transform:uppercase}}
.box{{border:1px solid rgba(0,229,200,.25);padding:14px;margin:12px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border-bottom:1px solid rgba(0,229,200,.15);padding:8px;text-align:left;vertical-align:top}}
.badge{{display:inline-block;padding:4px 8px;border:1px solid #00e5c8;color:#00e5c8}}
.bad{{border-color:#ff4d5a;color:#ff4d5a}}
.warn{{border-color:#e6a23c;color:#e6a23c}}
.account{{border-top:1px solid rgba(0,229,200,.15);padding:10px 0}}
.preview{{white-space:pre-wrap;max-width:760px}}
</style></head><body>
<h1>SADT // OPS REPORT</h1>
<div class="box">
  <div>Session: <code>{_esc(s['id'])}</code></div>
  <div>Device: {_esc(s['label'])} / {_esc(s['device_id'])} ({_esc(s['device_type'])})</div>
  <div>Mode: {_esc(s['mode'])} · Method: {_esc(_report_method(s['acquisition_method']))}</div>
  <div>Recommendation: {rec_badge}</div>
</div>
<div class="box">
  <h2>Data akun & sosial yang dikoleksi</h2>
  {account_rows or '<div>Tidak ada data sosial terverifikasi.</div>'}
  <table><thead><tr><th>Platform</th><th>Jenis data</th><th>Waktu</th><th>Preview</th></tr></thead>
  <tbody>{social_rows or '<tr><td colspan="4">Tidak ada postingan / tweet / arsip / komentar yang dikoleksi.</td></tr>'}</tbody></table>
</div>
<div class="box">
  <h2>Metrics</h2>
  <ul>
    <li>Files: {_esc(m['files'])} ({_esc(m['bytes'])} bytes)</li>
    <li>Findings: {_esc(m['findings'])}</li>
    <li>Acquire: {_esc(_ms(m['timing'].get('t_acquire_ms',0)))}</li>
    <li>Analyze: {_esc(_ms(m['timing'].get('t_analyze_ms',0)))}</li>
    <li>Total: {_esc(_ms(m['timing'].get('t_total_ms',0)))}</li>
    {recovery_metric}
    {ios_library_metric}
  </ul>
  <h2>By category</h2>
  <ul>{cat}</ul>
</div>
<div class="box">
  <h2>Findings</h2>
  <table><thead><tr><th>Label</th><th>Category</th><th>Source</th><th>Layer</th><th>Conf</th><th>Path</th></tr></thead>
  <tbody>{rows or '<tr><td colspan="6">No findings</td></tr>'}</tbody></table>
</div>
<p>Generated {_esc(report['generated_at'])} · {_esc(settings.app_name)}</p>
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
