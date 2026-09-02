"""pymobiledevice3 usbmux address for WSL usbipd vs Windows iTunes mux.

Keep in sync with backend/app/acquisition/ios_usbmux.py.
"""

from __future__ import annotations

import os
from pathlib import Path

WSL_USBMUXD_PIPE = "/var/run/usbmuxd"
_DEFAULT_OWNER_PATH = "/tmp/siksik-iphone-usb.owner"


def _wsl_version_text() -> str:
    try:
        return Path("/proc/version").read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve_usbmux_address() -> str | None:
    explicit = (os.environ.get("USBMUXD_SOCKET_ADDRESS") or "").strip() or None
    if "microsoft" not in _wsl_version_text().casefold():
        return explicit
    owner = Path(os.environ.get("SIKSIK_IPHONE_USB_OWNER", _DEFAULT_OWNER_PATH))
    try:
        if owner.read_text(encoding="utf-8").strip() == "windows":
            return explicit
    except OSError:
        pass
    if Path(WSL_USBMUXD_PIPE).exists():
        return WSL_USBMUXD_PIPE
    return explicit


def lockdown_usbmux_kwargs() -> dict[str, str]:
    address = resolve_usbmux_address()
    if not address:
        return {}
    return {"usbmux_address": address}
