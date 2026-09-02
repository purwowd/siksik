"""pymobiledevice3 usbmux address for WSL usbipd vs Windows iTunes mux.

pymobiledevice3 10's Wsl OS util defaults to 127.0.0.1:27015. Labs that attach
the iPhone into WSL (usbipd) talk through /var/run/usbmuxd instead — the same
socket libimobiledevice uses. Without this override, AFC/backup2 fail immediately
with ConnectionFailedToUsbmuxdError while idevice_id still lists the phone.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

WSL_USBMUXD_PIPE = "/var/run/usbmuxd"
_DEFAULT_OWNER_PATH = "/tmp/siksik-iphone-usb.owner"
_LOCKDOWN_ERROR_NAMES = (
    "ConnectionFailedToUsbmuxdError",
    "NoDeviceConnectedError",
    "PasswordRequiredError",
    "NotPairedError",
    "UserDeniedPairingError",
    "PairingDialogResponsePendingError",
    "ConnectionFailedError",
    "InvalidServiceError",
    "MuxException",
)


def _wsl_version_text() -> str:
    try:
        return Path("/proc/version").read_text(encoding="utf-8")
    except OSError:
        return ""


def running_under_wsl() -> bool:
    return "microsoft" in _wsl_version_text().casefold()


def iphone_usb_owner_path() -> Path:
    return Path(os.environ.get("SIKSIK_IPHONE_USB_OWNER", _DEFAULT_OWNER_PATH))


def windows_holds_iphone_usb() -> bool:
    try:
        return iphone_usb_owner_path().read_text(encoding="utf-8").strip() == "windows"
    except OSError:
        return False


def mark_iphone_usb_windows() -> None:
    path = iphone_usb_owner_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("windows\n", encoding="utf-8")
    except OSError:
        return


def clear_iphone_usb_windows_hold() -> None:
    path = iphone_usb_owner_path()
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == "windows":
            path.write_text("wsl\n", encoding="utf-8")
    except OSError:
        return


def _default_ipv4_gateway() -> str | None:
    try:
        text = Path("/proc/net/route").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or parts[1] != "00000000":
            continue
        raw = int(parts[2], 16)
        return socket.inet_ntoa(raw.to_bytes(4, "little"))
    return None


def _tcp_open(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def discover_windows_usbmux_tcp() -> str | None:
    """WSL → Windows AMDS via portproxy on the vEthernet gateway :27015."""
    gw = _default_ipv4_gateway()
    if gw and _tcp_open(gw, 27015):
        return f"{gw}:27015"
    return None


def resolve_usbmux_address() -> str | None:
    """Unix socket when WSL owns the cable; Windows AMDS TCP when USB is Shared."""
    explicit = (os.environ.get("USBMUXD_SOCKET_ADDRESS") or "").strip() or None
    if not running_under_wsl():
        return explicit
    if windows_holds_iphone_usb():
        return explicit or discover_windows_usbmux_tcp()
    if Path(WSL_USBMUXD_PIPE).exists():
        return WSL_USBMUXD_PIPE
    return explicit


def apply_usbmux_env(env: dict[str, str]) -> dict[str, str]:
    merged = dict(env)
    address = resolve_usbmux_address()
    if address:
        merged["USBMUXD_SOCKET_ADDRESS"] = address
    return merged


def lockdown_error_token(text: str) -> str | None:
    blob = text or ""
    for name in _LOCKDOWN_ERROR_NAMES:
        if name in blob:
            return name
    match = re.search(r"(pymobiledevice3\.\w+\.\w+Error)", blob)
    if match:
        return match.group(1).rsplit(".", 1)[-1]
    return None
