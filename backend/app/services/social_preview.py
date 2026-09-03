from __future__ import annotations

import re
from typing import Any

X_PACKAGE = "com.twitter.android"

_HANDLE = re.compile(r"^@?[A-Za-z0-9_]{1,32}$")
_COUNT = re.compile(r"^[0-9]+(?:[.,][0-9]+)?(?:\s*(?:k|m|b|rb|jt))?$", re.I)
_INLINE_COUNT = re.compile(
    r"^(?P<count>[0-9]+(?:[.,][0-9]+)?(?:\s*(?:k|m|b|rb|jt))?)\s+"
    r"(?P<label>.+)$",
    re.I,
)
_INLINE_LABEL = re.compile(
    r"^(?P<label>.+?)\s+(?P<count>[0-9]+(?:[.,][0-9]+)?(?:\s*(?:k|m|b|rb|jt))?)$",
    re.I,
)
_PUBLISHED = re.compile(
    r"^(?:[\u00b7\u2022]\s*)?(?:"
    r"\d+\s*(?:detik|menit|jam|hari|minggu|bulan|tahun|s|m|h|d|w)|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|Mei|May|Jun|Jul|Agu|Aug|Sep|Okt|Oct|Nov|Des|Dec)|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}"
    r")$",
    re.I,
)

_X_CHROME = {
    "banner profil",
    "profile banner",
    "foto profil",
    "profile photo",
    "sebarkan",
    "share",
    "edit profil",
    "edit profile",
    "jelaskan postingan ini dengan grok",
    "explain this post with grok",
    "opsi postingan",
    "post options",
    "more options",
}
_X_ACTIONS = {
    "balas",
    "reply",
    "posting ulang",
    "repost",
    "retweet",
    "tayangan",
    "views",
    "view",
    "markah",
    "bookmark",
    "bookmarks",
    "sebarkan",
    "share",
    "suka",
    "like",
    "likes",
}
_PROFILE_ONLY_PREFIXES = (
    "lahir ",
    "born ",
    "bergabung ",
    "joined ",
)
_MONTHS_ID = {
    "january": "Januari",
    "jan": "Januari",
    "januari": "Januari",
    "february": "Februari",
    "feb": "Februari",
    "februari": "Februari",
    "march": "Maret",
    "mar": "Maret",
    "maret": "Maret",
    "april": "April",
    "apr": "April",
    "may": "Mei",
    "mei": "Mei",
    "june": "Juni",
    "jun": "Juni",
    "juni": "Juni",
    "july": "Juli",
    "jul": "Juli",
    "juli": "Juli",
    "august": "Agustus",
    "aug": "Agustus",
    "agustus": "Agustus",
    "agu": "Agustus",
    "september": "September",
    "sep": "September",
    "october": "Oktober",
    "oct": "Oktober",
    "oktober": "Oktober",
    "okt": "Oktober",
    "november": "November",
    "nov": "November",
    "december": "Desember",
    "dec": "Desember",
    "desember": "Desember",
    "des": "Desember",
}


def _lines(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    output: list[str] = []
    for raw in value.replace("\x00", " ").splitlines():
        line = " ".join(raw.split()).strip(" |").strip()
        if line:
            output.append(line)
    return output


def _metadata(canonical: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(canonical, dict):
        return {}
    value = canonical.get("metadata")
    return value if isinstance(value, dict) else {}


def _username(lines: list[str], metadata: dict[str, Any]) -> str | None:
    structured = metadata.get("profile_username")
    # Bare content words (for example a one-word tweet) are valid handle
    # shapes too. Only trust the named canonical field or an explicit @ line.
    candidates = [structured, *(line for line in lines if line.startswith("@"))]
    for value in candidates:
        if not isinstance(value, str):
            continue
        candidate = value.strip().removeprefix("@").strip()
        if not candidate or not _HANDLE.fullmatch(candidate):
            continue
        if candidate.casefold() in _X_CHROME | _X_ACTIONS:
            continue
        return f"@{candidate}"
    return None


def _birth_date(lines: list[str]) -> str | None:
    for line in lines:
        match = re.match(r"^(?:Lahir|Born)\s+(.+)$", line, re.I)
        if not match:
            continue
        value = match.group(1).strip()
        month_first = re.fullmatch(
            r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
            value,
        )
        if month_first:
            month, day, year = month_first.groups()
            localized = _MONTHS_ID.get(month.casefold(), month)
            return f"Lahir {int(day)} {localized} {year}"
        day_first = re.fullmatch(
            r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})",
            value,
        )
        if day_first:
            day, month, year = day_first.groups()
            localized = _MONTHS_ID.get(month.casefold(), month)
            return f"Lahir {int(day)} {localized} {year}"
        return f"Lahir {value}"
    return None


def _clean_count(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    candidate = " ".join(str(value).split()).strip()
    return candidate if _COUNT.fullmatch(candidate) else None


def _explicit_metric(
    lines: list[str],
    metadata: dict[str, Any],
    name: str,
    labels: set[str],
) -> str | None:
    # A number is only a profile metric when its label is present, or when the
    # acquisition adapter supplied a named structured metric. Standalone
    # numbers on X can belong to unrelated controls and must never be guessed.
    for index, line in enumerate(lines):
        key = line.casefold()
        for pattern in (_INLINE_COUNT, _INLINE_LABEL):
            match = pattern.fullmatch(line)
            if match and match.group("label").casefold() in labels:
                return _clean_count(match.group("count"))
        if key not in labels:
            continue
        for nearby in (index - 1, index + 1):
            if 0 <= nearby < len(lines):
                count = _clean_count(lines[nearby])
                if count is not None:
                    return count
    raw_metrics = metadata.get("profile_metrics")
    if isinstance(raw_metrics, dict):
        return _clean_count(raw_metrics.get(name))
    return None


def _published_label(lines: list[str]) -> str | None:
    for line in lines:
        candidate = re.sub(r"^[\u00b7\u2022]\s*", "", line).strip()
        if _PUBLISHED.fullmatch(line) or _PUBLISHED.fullmatch(candidate):
            return candidate
    return None


def _post_fields(
    lines: list[str],
    username: str | None,
    metadata: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    published = _published_label(lines)
    handle_index = next(
        (
            index
            for index, line in enumerate(lines)
            if username and line.removeprefix("@").casefold() == username[1:].casefold()
        ),
        None,
    )
    display_name: str | None = None
    if handle_index is not None and lines:
        first = lines[0]
        first_key = first.casefold()
        if (
            first_key not in _X_CHROME
            and first_key not in _X_ACTIONS
            and not first.startswith("@")
            and not _PUBLISHED.fullmatch(first)
        ):
            display_name = first[:128]
    structured_name = metadata.get("profile_display_name")
    if isinstance(structured_name, str) and structured_name.strip():
        display_name = " ".join(structured_name.split())[:128]

    body: list[str] = []
    action_zone = False
    for index, line in enumerate(lines):
        key = line.casefold()
        if key in _X_ACTIONS:
            action_zone = True
            continue
        if action_zone:
            # X exposes counts as detached accessibility nodes. Without a
            # semantic association they are chrome, not post engagement.
            continue
        if key in _X_CHROME:
            continue
        if index == 0 and display_name and line == display_name:
            continue
        if username and line.removeprefix("@").casefold() == username[1:].casefold():
            continue
        if published and re.sub(r"^[\u00b7\u2022]\s*", "", line).strip() == published:
            continue
        if key.startswith(_PROFILE_ONLY_PREFIXES):
            continue
        body.append(line)
    clean_body = "\n".join(dict.fromkeys(body)).strip()[:12_000] or None
    return display_name, clean_body, published


def build_social_preview(
    *,
    source_app: str | None,
    social_scope: str | None,
    normalized_text: Any,
    canonical: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if source_app != X_PACKAGE:
        return None
    lines = _lines(normalized_text)
    metadata = _metadata(canonical)
    username = _username(lines, metadata)
    if social_scope == "own_profile":
        birth_date = _birth_date(lines)
        following = _explicit_metric(
            lines,
            metadata,
            "following",
            {"following", "mengikuti", "diikuti"},
        )
        followers = _explicit_metric(
            lines,
            metadata,
            "followers",
            {"followers", "follower", "pengikut"},
        )
        return {
            "platform": "x",
            "kind": "profile",
            "display_name": None,
            "username": username,
            "body": None,
            "birth_date": birth_date,
            "published_label": None,
            "following": following,
            "followers": followers,
        }
    if social_scope not in {"own_tweets", "own_replies"}:
        return None
    display_name, body, published = _post_fields(lines, username, metadata)
    return {
        "platform": "x",
        "kind": "reply" if social_scope == "own_replies" else "post",
        "display_name": display_name,
        "username": username,
        "body": body,
        "birth_date": None,
        "published_label": published,
        "following": None,
        "followers": None,
    }


def social_preview_summary(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict) or value.get("platform") != "x":
        return None
    if value.get("kind") == "profile":
        parts = [value.get("username"), value.get("birth_date")]
        following = value.get("following")
        followers = value.get("followers")
        if following is not None:
            parts.append(f"{following} Mengikuti")
        if followers is not None:
            parts.append(f"{followers} Pengikut")
        summary = " · ".join(str(part) for part in parts if part)
        return summary[:2000] or None
    body = value.get("body")
    if isinstance(body, str) and body.strip():
        return " ".join(body.split())[:2000]
    identity = " ".join(
        str(part)
        for part in (value.get("display_name"), value.get("username"))
        if part
    )
    return identity[:2000] or None
