"""Keep Android USB in WSL (usbipd attach). Never release Android to Windows.

Windows AMDS / usbipd detach is iPhone-only (WDA install). Android stays on
the Linux adb server for the whole operator session.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.ios_usbmux import running_under_wsl
from app.acquisition.process import run_process

logger = logging.getLogger("siksik.acquisition.android_usb_wsl")

_APPLE_VID = "05ac"
_PHONE_VIDS = frozenset(
    {
        "04e8",  # Samsung
        "18d1",  # Google
        "2717",  # Xiaomi
        "22d9",  # OPPO
        "2a70",  # OnePlus
        "0e8d",  # MediaTek / Transsion
        "0b05",  # ASUS
        "0fce",  # Sony
        "12d1",  # Huawei
        "19d2",  # ZTE
        "0bb4",  # HTC
        "1004",  # LG
        "2b4c",
        "05c6",  # Qualcomm
    }
)
_ANDROID_DESC = (
    "adb interface",
    "samsung_android",
    "android",
)
_NOT_PHONE_DESC = (
    "webcam",
    "bluetooth",
    "camera dfu",
    "root hub",
    "keyboard",
    "mouse",
)
_STATE_SUFFIXES = (
    ("Shared (forced)", "shared"),
    ("Not shared", "not_shared"),
    ("Not attached", "not_attached"),
    ("Attached", "attached"),
    ("Shared", "shared"),
)
_ROW_RE = re.compile(
    r"^(?P<busid>\d+-\d+(?:\.\d+)?)\s+(?P<vidpid>[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\s+(?P<rest>.+)$"
)


@dataclass(frozen=True, slots=True)
class UsbipdRow:
    busid: str
    vidpid: str
    description: str
    state: str

    @property
    def vid(self) -> str:
        return self.vidpid.split(":", 1)[0].casefold()

    @property
    def attached(self) -> bool:
        return self.state == "attached"

    @property
    def shared(self) -> bool:
        return self.state == "shared"


def is_apple_usb(row: UsbipdRow) -> bool:
    return row.vid == _APPLE_VID


def is_android_usb(row: UsbipdRow) -> bool:
    if is_apple_usb(row):
        return False
    desc = row.description.casefold()
    if any(token in desc for token in _NOT_PHONE_DESC):
        return False
    if any(token in desc for token in _ANDROID_DESC):
        return True
    if row.vid in _PHONE_VIDS and any(
        token in desc
        for token in (
            "galaxy",
            "redmi",
            "pixel",
            "realme",
            "xiaomi",
            "infinix",
            "oppo",
            "vivo",
            "oneplus",
            "poco",
            "tecno",
            "phone",
        )
    ):
        return True
    return False


def parse_usbipd_connected(blob: str) -> list[UsbipdRow]:
    rows: list[UsbipdRow] = []
    in_connected = False
    for raw in (blob or "").splitlines():
        line = raw.replace("\r", "").strip()
        if line.startswith("Connected:"):
            in_connected = True
            continue
        if line.startswith("Persisted:"):
            break
        if not in_connected or line.startswith("BUSID") or not line:
            continue
        match = _ROW_RE.match(line)
        if match is None:
            continue
        rest = match.group("rest").strip()
        state = ""
        description = rest
        for suffix, name in _STATE_SUFFIXES:
            if rest.endswith(suffix):
                state = name
                description = rest[: -len(suffix)].strip()
                break
        if not state:
            continue
        rows.append(
            UsbipdRow(
                busid=match.group("busid"),
                vidpid=match.group("vidpid").casefold(),
                description=description,
                state=state,
            )
        )
    return rows


def android_busids_needing_wsl_attach(blob: str) -> list[str]:
    """Shared Android buses that are not yet Attached. Never includes Apple."""
    return _phone_busids_for_wsl(blob, android=True, include_not_shared=False)


def apple_busids_needing_wsl_attach(blob: str) -> list[str]:
    """Shared iPhone buses that are not yet Attached. Never includes Android."""
    return _phone_busids_for_wsl(blob, android=False, include_not_shared=False)


def android_busids_reclaimable(blob: str) -> list[str]:
    """Shared or Not-shared Android buses that are not Attached."""
    return _phone_busids_for_wsl(blob, android=True, include_not_shared=True)


def apple_busids_reclaimable(blob: str) -> list[str]:
    """Shared or Not-shared iPhone buses that are not Attached."""
    return _phone_busids_for_wsl(blob, android=False, include_not_shared=True)


def _phone_busids_for_wsl(
    blob: str, *, android: bool, include_not_shared: bool
) -> list[str]:
    busids: list[str] = []
    for row in parse_usbipd_connected(blob):
        match = is_android_usb(row) if android else is_apple_usb(row)
        if not match or row.attached:
            continue
        if row.shared or (include_not_shared and row.state == "not_shared"):
            busids.append(row.busid)
    return busids


def _row_state(blob: str, busid: str) -> str | None:
    for row in parse_usbipd_connected(blob):
        if row.busid == busid:
            return row.state
    return None


async def _usbipd_list_blob() -> str:
    try:
        listed = await run_process(
            ["usbipd.exe", "list"],
            timeout=8.0,
            check=False,
            output_limit_bytes=64 * 1024,
            operation="usbipd_list",
        )
    except AcquisitionError as exc:
        if exc.category in {ErrorCategory.ADB_TIMEOUT, ErrorCategory.DEPENDENCY_NOT_FOUND}:
            return ""
        raise
    return f"{listed.stdout}\n{listed.stderr}"


async def _attach_shared_busids(busids: list[str], *, operation: str) -> bool:
    attached_any = False
    for busid in busids:
        try:
            result = await run_process(
                ["usbipd.exe", "attach", "--wsl", "--busid", busid],
                timeout=15.0,
                check=False,
                output_limit_bytes=16 * 1024,
                operation=operation,
            )
        except AcquisitionError as exc:
            if exc.category in {
                ErrorCategory.ADB_TIMEOUT,
                ErrorCategory.DEPENDENCY_NOT_FOUND,
            }:
                continue
            raise
        if result.returncode == 0:
            attached_any = True
            logger.info(operation, extra={"busid": busid})
    if attached_any:
        await asyncio.sleep(1.5)
    return attached_any


async def _bind_then_attach(busids: list[str], blob: str) -> bool:
    """Bind Not-shared phones then attach. No --force (may still need Admin)."""
    attached_any = False
    for busid in busids:
        state = _row_state(blob, busid)
        if state == "not_shared":
            try:
                await run_process(
                    ["usbipd.exe", "bind", "--busid", busid],
                    timeout=15.0,
                    check=False,
                    output_limit_bytes=16 * 1024,
                    operation="usbipd_bind",
                )
            except AcquisitionError as exc:
                if exc.category in {
                    ErrorCategory.ADB_TIMEOUT,
                    ErrorCategory.DEPENDENCY_NOT_FOUND,
                }:
                    continue
                raise
            await asyncio.sleep(0.8)
        if await _attach_shared_busids([busid], operation="usbipd_attach_wsl"):
            attached_any = True
    return attached_any


async def _wait_apple_in_lsusb() -> None:
    for spec in ("05ac:12a8", "05ac:12ab"):
        for _ in range(8):
            try:
                result = await run_process(
                    ["lsusb", "-d", spec],
                    timeout=2.0,
                    check=False,
                    output_limit_bytes=4096,
                    operation="lsusb_apple",
                )
            except AcquisitionError:
                return
            if result.returncode == 0 and (result.stdout or "").strip():
                return
            await asyncio.sleep(0.4)


async def _ensure_usbmuxd() -> None:
    if Path("/var/run/usbmuxd").exists():
        return
    try:
        await run_process(
            ["systemctl", "start", "usbmuxd"],
            timeout=5.0,
            check=False,
            output_limit_bytes=4096,
            operation="usbmuxd_start",
        )
    except AcquisitionError:
        return


async def ensure_android_on_wsl() -> None:
    """Attach already-bound Android USB into WSL. No detach, unbind, or bind --force."""
    await ensure_shared_wsl_usb(attach_android=True, attach_iphone=False)


async def ensure_shared_wsl_usb(
    *,
    attach_android: bool = True,
    attach_iphone: bool = True,
    include_iphone: bool | None = None,
    reclaim_not_shared: bool = False,
) -> None:
    """Attach phone USB into WSL when missing from adb/idevice.

    Default: Shared buses only (no UAC). With reclaim_not_shared, also try
    bind+attach for Not-shared phones (Pindai ulang). Windows AMDS handoff for
    WDA install still uses the elevated claim script when this soft path fails.
    """
    if not running_under_wsl():
        return
    if include_iphone is not None:
        attach_iphone = include_iphone
    if not attach_android and not attach_iphone:
        return
    blob = await _usbipd_list_blob()
    if not blob.strip():
        return
    if reclaim_not_shared:
        android_ids = android_busids_reclaimable(blob) if attach_android else []
        apple_ids = apple_busids_reclaimable(blob) if attach_iphone else []
        busids = android_ids + apple_ids
        if not busids:
            return
        attached = await _bind_then_attach(busids, blob)
    else:
        android_ids = android_busids_needing_wsl_attach(blob) if attach_android else []
        apple_ids: list[str] = []
        if attach_iphone:
            from app.acquisition.ios_usbmux import windows_holds_iphone_usb

            if not windows_holds_iphone_usb():
                apple_ids = apple_busids_needing_wsl_attach(blob)
        busids = android_ids + apple_ids
        if not busids:
            return
        attached = await _attach_shared_busids(busids, operation="usbipd_attach_wsl")
    if attached and apple_ids:
        await _wait_apple_in_lsusb()
        await _ensure_usbmuxd()
