"""Append-only operator log for iOS preflight (`logs/setup_ios.log`)."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

_WRITE_LOCK = threading.Lock()
_UDID_RE = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{10,}\b")
_SIX_DIGIT_LINE_RE = re.compile(r"^\s*\d{6}\s*$")
_PASSWORD_FLAG = frozenset({"-p", "--password", "-a", "--appleid", "--udid", "-u"})


def setup_ios_log_path() -> Path:
    return Path(settings.ios_setup_log_path)


def redact_text(text: str, *, udid: str | None = None) -> str:
    value = text.replace("\x00", "")
    if udid:
        value = value.replace(udid, "<udid>")
    value = _UDID_RE.sub("<udid>", value)
    lines: list[str] = []
    for line in value.splitlines():
        if _SIX_DIGIT_LINE_RE.match(line):
            lines.append("<code>")
            continue
        lines.append(line)
    return "\n".join(lines)


def safe_argv(argv: list[str], *, udid: str | None = None) -> str:
    parts: list[str] = []
    hide_next = False
    for item in argv:
        if hide_next:
            parts.append("<redacted>")
            hide_next = False
            continue
        if item in _PASSWORD_FLAG or item.startswith("--udid="):
            if item.startswith("--udid="):
                parts.append("--udid=<udid>")
                continue
            parts.append(item)
            hide_next = True
            continue
        parts.append(redact_text(item, udid=udid))
    return " ".join(parts)


def write_setup_ios_log(
    level: str,
    event: str,
    *,
    detail: str = "",
    udid: str | None = None,
) -> None:
    path = setup_ios_log_path()
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    body = redact_text(event if not detail else f"{event} | {detail}", udid=udid)
    line = f"{stamp} [{level.upper()}] {body}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        return
