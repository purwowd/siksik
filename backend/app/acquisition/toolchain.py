from __future__ import annotations

import logging

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.media_types import (
    IMG_EXT,
    TEXT_EXT,
    VID_EXT,
    _is_junk_media_path,
)
from app.acquisition.process import run_process
from app.core.config import settings
from app.models.schemas import DeviceInfo, DeviceType

logger = logging.getLogger("siksik.acquisition.toolchain")

async def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        result = await run_process(
            cmd,
            timeout=timeout,
            check=False,
            output_limit_bytes=1024 * 1024,
            operation="acquisition_dependency",
        )
        return result.returncode, result.stdout, result.stderr
    except AcquisitionError as exc:
        code = 124 if exc.category == ErrorCategory.ADB_TIMEOUT else 127
        return code, "", exc.public_message


async def toolchain_status() -> dict:
    try:
        adb_result = await AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=3,
        ).run(
            None,
            ["version"],
            operation="adb_version_probe",
            check=False,
        )
        adb_ready = adb_result.returncode == 0
    except AcquisitionError:
        adb_ready = False
    idevice_code, _, _ = await _run(["idevice_id", "-l"], timeout=3)
    backup_code, _, _ = await _run(["idevicebackup2", "-h"], timeout=3)
    return {
        "adb": adb_ready,
        "idevice_id": idevice_code == 0,
        "idevicebackup2": backup_code in (0, 1),  # help often exits 1
    }


async def detect_devices(*, include_simulators: bool = True) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []

    try:
        from app.acquisition.install_policy import oem_install_guidance

        transport = AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=min(settings.adb_command_timeout_s, 5.0),
        )
        android_devices = await transport.list_devices()
        for item in android_devices:
            if item.state != "device":
                continue
            model = (item.model or "Android").replace("_", " ")
            os_version = "unknown"
            manufacturer: str | None = None
            api_level: int | None = None
            unlocked: bool | None = None
            install_hint: str | None = None
            try:
                os_version = await transport.getprop(item.serial, "ro.build.version.release") or "unknown"
            except AcquisitionError:
                pass
            try:
                manufacturer = await transport.getprop(item.serial, "ro.product.manufacturer")
            except AcquisitionError:
                pass
            try:
                brand = await transport.getprop(item.serial, "ro.product.brand")
            except AcquisitionError:
                brand = None
            try:
                sdk = await transport.getprop(item.serial, "ro.build.version.sdk")
                if sdk and sdk.isdigit():
                    api_level = int(sdk)
            except AcquisitionError:
                pass
            try:
                readiness = await transport.device_readiness(item.serial)
                unlocked = readiness.unlocked
            except AcquisitionError:
                pass
            install_hint = oem_install_guidance(manufacturer=manufacturer, brand=brand)
            agent_state: str | None = None
            agent_version: str | None = None
            agent_error_category: str | None = None
            automation_state: str | None = None
            if settings.android_agent_enabled:
                try:
                    package = await transport.inspect_package(
                        item.serial,
                        settings.android_agent_package,
                    )
                    agent_state = "installed" if package.installed else "not_installed"
                    agent_version = package.version_name
                except AcquisitionError as exc:
                    agent_state = "unknown"
                    agent_error_category = exc.category.value
                try:
                    automation = await transport.inspect_package(
                        item.serial,
                        settings.android_agent_automation_package,
                    )
                    automation_state = (
                        "installed" if automation.installed else "not_installed"
                    )
                except AcquisitionError:
                    automation_state = "unknown"
            lock_note = ""
            if unlocked is False:
                lock_note = " · terkunci"
            devices.append(
                DeviceInfo(
                    device_id=item.serial,
                    device_type=DeviceType.ANDROID,
                    label=f"{model} · Android {os_version}{lock_note} ({item.serial[:8]})",
                    os_version=os_version or "unknown",
                    connected=True,
                    simulated=False,
                    agent_state=agent_state,
                    agent_version=agent_version,
                    agent_error_category=agent_error_category,
                    manufacturer=manufacturer,
                    api_level=api_level,
                    unlocked=unlocked,
                    install_hint=install_hint,
                    automation_state=automation_state,
                )
            )
    except AcquisitionError:
        pass

    code, out, _ = await _run(["idevice_id", "-l"], timeout=5)
    if code == 0:
        for udid in out.strip().splitlines():
            udid = udid.strip()
            if not udid:
                continue
            name_code, name_out, _ = await _run(["idevicename", "-u", udid], timeout=5)
            label = name_out.strip() if name_code == 0 and name_out.strip() else f"iPhone ({udid[:8]})"
            devices.append(
                DeviceInfo(
                    device_id=udid,
                    device_type=DeviceType.IOS,
                    label=label,
                    os_version="iOS",
                    connected=True,
                    simulated=False,
                )
            )

    if include_simulators:
        devices.extend(
            [
                DeviceInfo(
                    device_id="sim-android-01",
                    device_type=DeviceType.ANDROID,
                    label="Android Simulator (PoC)",
                    os_version="14",
                    connected=True,
                    simulated=True,
                ),
                DeviceInfo(
                    device_id="sim-iphone-01",
                    device_type=DeviceType.IOS,
                    label="iPhone Simulator (PoC)",
                    os_version="17",
                    connected=True,
                    simulated=True,
                ),
            ]
        )
    return devices
