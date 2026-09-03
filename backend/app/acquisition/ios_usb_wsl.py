"""Move iPhone USB into WSL (usbipd attach). Kept out of services.acquisition to avoid import cycles."""

from __future__ import annotations

import os

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.ios_usbmux import (
    clear_iphone_usb_windows_hold,
    running_under_wsl,
    windows_holds_iphone_usb,
)
from app.acquisition.process import run_process
from app.core.config import settings


async def ensure_iphone_on_wsl(*, reattach: bool = False, force: bool = False) -> None:
    """Attach the iPhone into WSL.

    Idle polls attach an already-Shared iPhone (`ensure_shared_wsl_usb`) without
    bind --force. Windows AMDS handoff is iPhone-only. Explicit reclaim
    (`reattach` or `force`) is for Jalankan akuisisi iOS / WDA restore.
    """
    if not running_under_wsl():
        return
    if force or reattach:
        clear_iphone_usb_windows_hold()
    elif windows_holds_iphone_usb():
        return
    script = settings.ios_media_puller_path / "ios_automator" / "scripts" / "ensure_iphone_wsl.sh"
    if not script.is_file():
        return
    argv = ["bash", str(script)]
    if reattach:
        argv.append("--startup")
    try:
        await run_process(
            argv,
            timeout=90.0 if reattach else 30.0,
            check=False,
            output_limit_bytes=64 * 1024,
            operation="ios_usb_ensure_wsl",
        )
    except AcquisitionError as exc:
        if exc.category in {ErrorCategory.ADB_TIMEOUT, ErrorCategory.DEPENDENCY_NOT_FOUND}:
            return
        raise


def _lockdown_blob_ok(stdout: str, stderr: str, returncode: int) -> bool:
    if returncode != 0:
        return False
    blob = f"{stdout}\n{stderr}".casefold()
    if "mux error" in blob or "lockdownd" in blob or "no device found" in blob:
        return False
    return bool((stdout or "").strip())


async def iphone_lockdown_ok(udid: str | None = None) -> bool:
    argv = ["ideviceinfo", "-k", "DeviceName"]
    if udid:
        argv[1:1] = ["-u", udid]
    try:
        result = await run_process(
            argv,
            timeout=8.0,
            check=False,
            output_limit_bytes=16 * 1024,
            operation="ios_lockdown_probe",
        )
    except AcquisitionError:
        return False
    return _lockdown_blob_ok(result.stdout, result.stderr, result.returncode)


async def ensure_iphone_lockdown(*, udid: str | None = None) -> None:
    """Unwedge lockdownd after Trust/tunnel leftovers (usbipd recycle, no cable pull).

    No-op when ideviceinfo already works. Fail loud if Mux -8 remains so AFC
    does not hang at 18% for 300s.
    """
    if not running_under_wsl():
        return
    if await iphone_lockdown_ok(udid):
        return
    script = (
        settings.ios_media_puller_path
        / "ios_automator"
        / "scripts"
        / "recover_ios_lockdown.sh"
    )
    if script.is_file():
        env = dict(os.environ)
        if udid:
            env["UDID"] = udid
        try:
            await run_process(
                ["bash", str(script)],
                timeout=90.0,
                check=False,
                output_limit_bytes=64 * 1024,
                env=env,
                operation="ios_lockdown_recover",
            )
        except AcquisitionError as exc:
            if exc.category not in {
                ErrorCategory.ADB_TIMEOUT,
                ErrorCategory.DEPENDENCY_NOT_FOUND,
            }:
                raise
    if await iphone_lockdown_ok(udid):
        return
    raise acquisition_error(
        ErrorCategory.AGENT_UNREACHABLE,
        "iPhone USB di WSL tetapi lockdownd macet (Mux -8). Unlock HP, lalu ulangi akuisisi.",
        retryable=True,
    )
