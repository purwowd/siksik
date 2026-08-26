"""Infer social/chat apps from gallery paths and Android screenshot names."""

from __future__ import annotations

import re
from pathlib import Path

SOCIAL_PACKAGE_LABELS = {
    "com.instagram.android": "Instagram",
    "com.instagram.barcelona": "Threads",
    "com.twitter.android": "X / Twitter",
    "com.facebook.katana": "Facebook",
    "com.whatsapp": "WhatsApp",
    "org.telegram.messenger": "Telegram",
}

# Android: Screenshot_2026-07-01-22-37-55-172_com.instagram.barcelona.jpg
_SCREENSHOT_PACKAGE = re.compile(
    r"(?i)screenshot_[^/]*_((?:com|org|net)\.[a-z0-9._]+)\.(?:jpe?g|png|webp|heic)$"
)
_WHATSAPP_MEDIA = re.compile(
    r"(?i)^(?:img|vid|aud|ptt|stk)-\d{8}-wa\d+"
)
_PACKAGE_ALIASES = {
    "com.instagram.android": "com.instagram.android",
    "com.instagram.barcelona": "com.instagram.barcelona",
    "com.facebook.katana": "com.facebook.katana",
    "com.facebook.lite": "com.facebook.katana",
    "com.twitter.android": "com.twitter.android",
    "com.whatsapp": "com.whatsapp",
    "com.whatsapp.w4b": "com.whatsapp",
    "org.telegram.messenger": "org.telegram.messenger",
}


def package_label(package_name: str | None) -> str | None:
    if not package_name:
        return None
    return SOCIAL_PACKAGE_LABELS.get(package_name)


def infer_source_app(
    *,
    directory_hint: str | None = None,
    display_name: str | None = None,
    path: str | None = None,
) -> str | None:
    name = Path(str(display_name or path or "").replace("\\", "/")).name
    match = _SCREENSHOT_PACKAGE.search(name)
    if match:
        package = match.group(1).casefold().rstrip(".")
        aliased = _PACKAGE_ALIASES.get(package)
        if aliased:
            return aliased
        for known, canonical in _PACKAGE_ALIASES.items():
            if package == known or package.startswith(f"{known}."):
                return canonical

    if _WHATSAPP_MEDIA.match(name):
        return "com.whatsapp"

    combined = "/".join(
        part.replace("\\", "/")
        for part in (directory_hint, display_name, path)
        if part
    ).casefold()
    if "whatsapp" in combined:
        return "com.whatsapp"
    if "telegram" in combined:
        return "org.telegram.messenger"
    return None


def inferred_album_label(
    *,
    directory_hint: str | None,
    display_name: str | None,
    path: str | None,
) -> str | None:
    package = infer_source_app(
        directory_hint=directory_hint,
        display_name=display_name,
        path=path,
    )
    label = package_label(package)
    if not label:
        return None
    haystack = " ".join(
        part for part in (directory_hint, display_name, path) if part
    ).casefold()
    if "screenshot" in haystack:
        return f"{label} (screenshot)"
    return label
