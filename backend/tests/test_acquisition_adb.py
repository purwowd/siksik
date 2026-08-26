from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.acquisition.adb import (
    AdbDevice,
    AsyncAdbTransport,
    PermissionState,
    SpecialAccessKind,
    SpecialAccessState,
    parse_available_data_bytes,
    parse_device_unlocked,
    parse_devices,
    parse_package_dump,
    parse_runtime_permission_dump,
    select_device,
    validate_serial,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.process import ProcessResult, run_process


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "adb"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.mark.unit
def test_parse_devices_preserves_state_and_metadata() -> None:
    devices = parse_devices(
        "List of devices attached\n"
        "serial-1 device product:foo model:Pixel_8 transport_id:7\n"
        "serial-2 unauthorized usb:1-2\n"
    )
    assert devices == [
        AdbDevice(
            serial="serial-1",
            state="device",
            product="foo",
            model="Pixel_8",
            transport_id="7",
        ),
        AdbDevice(serial="serial-2", state="unauthorized", usb="1-2"),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("serial", ["", "-transport", "has space", "a" * 129, "a/b"])
def test_validate_serial_rejects_unsafe_values(serial: str) -> None:
    with pytest.raises(AcquisitionError) as captured:
        validate_serial(serial)
    assert captured.value.category == ErrorCategory.VALIDATION_ERROR


@pytest.mark.unit
@pytest.mark.parametrize(
    ("devices", "serial", "category", "retryable"),
    [
        ([], None, ErrorCategory.ADB_NO_DEVICE, True),
        ([AdbDevice("one", "unauthorized")], None, ErrorCategory.ADB_UNAUTHORIZED, True),
        ([AdbDevice("one", "offline")], None, ErrorCategory.ADB_OFFLINE, True),
        (
            [AdbDevice("one", "device"), AdbDevice("two", "device")],
            None,
            ErrorCategory.ADB_MULTIPLE_DEVICES,
            False,
        ),
        ([AdbDevice("one", "device")], "missing", ErrorCategory.ADB_NO_DEVICE, True),
    ],
)
def test_select_device_categorizes_failures(
    devices: list[AdbDevice],
    serial: str | None,
    category: ErrorCategory,
    retryable: bool,
) -> None:
    with pytest.raises(AcquisitionError) as captured:
        select_device(devices, serial)
    assert captured.value.category == category
    assert captured.value.retryable is retryable


@pytest.mark.unit
async def test_transport_pins_serial_and_uses_argv(tmp_path: Path) -> None:
    adb = executable(tmp_path)
    captured: list[tuple[str, ...]] = []

    async def runner(argv, **_kwargs):
        captured.append(tuple(argv))
        return ProcessResult(tuple(argv), 0, "value\n", "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    result = await transport.getprop("serial-1", "ro.product.model")

    assert result == "value"
    assert captured == [
        (str(adb.resolve()), "-s", "serial-1", "shell", "getprop", "ro.product.model")
    ]
    assert all(value not in {"sh", "bash", "zsh", "-c"} for value in captured[0])


@pytest.mark.unit
def test_transport_does_not_resolve_adb_until_used() -> None:
    transport = AsyncAdbTransport("/missing/android-sdk/platform-tools/adb")
    assert isinstance(transport, AsyncAdbTransport)


@pytest.mark.unit
async def test_transport_maps_nonzero_without_leaking_output(tmp_path: Path) -> None:
    adb = executable(tmp_path)

    async def runner(argv, **_kwargs):
        return ProcessResult(tuple(argv), 7, "secret-response", "failure")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    with pytest.raises(AcquisitionError) as captured:
        await transport.run("serial-1", ["shell", "id"], operation="probe")

    assert captured.value.category == ErrorCategory.ADB_COMMAND_FAILED
    assert captured.value.dependency_exit_code == 7
    assert "secret-response" not in str(captured.value)


@pytest.mark.unit
async def test_process_timeout_is_retryable() -> None:
    with pytest.raises(AcquisitionError) as captured:
        await run_process(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout=0.01,
            operation="timeout_fixture",
        )
    assert captured.value.category == ErrorCategory.ADB_TIMEOUT
    assert captured.value.retryable is True


@pytest.mark.unit
def test_package_storage_and_lock_parsers_are_bounded() -> None:
    assert parse_package_dump("  versionCode=42 minSdk=26\n  versionName=1.2.3\n") == (
        42,
        "1.2.3",
    )
    assert parse_package_dump("corrupt") == (None, None)
    assert parse_available_data_bytes(
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/block/data 100000 25000 75000 25% /data\n"
    ) == 75000 * 1024
    assert parse_available_data_bytes("unavailable") is None
    assert parse_device_unlocked("mShowingLockscreen=true") is False
    assert parse_device_unlocked("mShowingLockscreen=false") is True
    assert parse_device_unlocked("unknown OEM output") is None
    assert (
        parse_device_unlocked(
            "KeyguardServiceDelegate\n"
            "  showing=true\n"
            "  showingAndNotOccluded=true\n"
            "  occluded=false\n"
            "  dreaming=true\n"
            "  mIsShowing=true\n"
        )
        is False
    )
    assert parse_device_unlocked("mDreamingLockscreen=true") is False
    assert (
        parse_device_unlocked(
            "KeyguardServiceDelegate\n"
            "  showing=false\n"
            "  showingAndNotOccluded=true\n"
            "  occluded=false\n"
            "  dreaming=false\n"
            "  mIsShowing=false\n"
            "  mDreamingLockscreen=false\n"
        )
        is True
    )


@pytest.mark.unit
def test_runtime_permission_parser_is_scoped_to_android_user() -> None:
    output = (
        "requested permissions:\n"
        "  android.permission.READ_MEDIA_IMAGES\n"
        "User 0:\n"
        "  runtime permissions:\n"
        "    android.permission.READ_MEDIA_IMAGES: granted=true, flags=[ USER_SET ]\n"
        "User 10:\n"
        "  runtime permissions:\n"
        "    android.permission.READ_MEDIA_IMAGES: granted=false, flags=[ USER_SET ]\n"
    )
    permission = "android.permission.READ_MEDIA_IMAGES"
    assert parse_runtime_permission_dump(output, permission, 0) == PermissionState.GRANTED
    assert parse_runtime_permission_dump(output, permission, 10) == PermissionState.DENIED
    assert (
        parse_runtime_permission_dump(output, "android.permission.READ_MEDIA_VIDEO", 0)
        == PermissionState.UNSUPPORTED
    )


@pytest.mark.unit
async def test_package_inspection_is_serial_pinned(tmp_path: Path) -> None:
    adb = executable(tmp_path)
    captured: list[tuple[str, ...]] = []

    async def runner(argv, **_kwargs):
        command = tuple(argv)
        captured.append(command)
        if command[-2:] == ("devices", "-l"):
            stdout = "List of devices attached\nserial-1 device model:Fixture\n"
        elif "pm" in command and "path" in command:
            stdout = "package:/data/app/~~fixture==/com.siksik.agent-x==/base.apk\n"
        elif "dumpsys" in command:
            stdout = "  versionCode=2 minSdk=26 targetSdk=35\n  versionName=0.2.0\n"
        else:
            stdout = ""
        return ProcessResult(command, 0, stdout, "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    package = await transport.inspect_package("serial-1", "com.siksik.agent")

    assert package.installed is True
    assert package.version_code == 2
    assert package.version_name == "0.2.0"
    device_commands = [command for command in captured if "devices" not in command]
    assert device_commands
    assert all(command[1:3] == ("-s", "serial-1") for command in device_commands)


@pytest.mark.unit
async def test_runtime_permission_grant_is_verified(tmp_path: Path) -> None:
    adb = executable(tmp_path)
    checks = 0
    captured: list[tuple[str, ...]] = []

    async def runner(argv, **_kwargs):
        nonlocal checks
        command = tuple(argv)
        captured.append(command)
        if "dumpsys" in command:
            checks += 1
            granted = "false" if checks == 1 else "true"
            return ProcessResult(
                command,
                0,
                "User 0:\n"
                "  runtime permissions:\n"
                "    android.permission.READ_MEDIA_IMAGES: "
                f"granted={granted}, flags=[ USER_SET ]\n",
                "",
            )
        return ProcessResult(command, 0, "", "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    state = await transport.grant_runtime_permission(
        "serial-1",
        "com.siksik.agent",
        "android.permission.READ_MEDIA_IMAGES",
        0,
    )

    assert state == PermissionState.GRANTED
    grant = next(command for command in captured if "grant" in command)
    assert grant[1:3] == ("-s", "serial-1")
    assert grant[-4:] == (
        "--user",
        "0",
        "com.siksik.agent",
        "android.permission.READ_MEDIA_IMAGES",
    )


@pytest.mark.unit
async def test_accessibility_restore_preserves_foreign_parental_service(
    tmp_path: Path,
) -> None:
    """OEM/parental a11y classes often sit outside the declaring package name."""
    adb = executable(tmp_path)
    writes: list[str] = []
    phase = {"get": 0}

    async def runner(argv, **_kwargs):
        command = tuple(argv)
        if "get" in command and "enabled_accessibility_services" in command:
            phase["get"] += 1
            # Probe before restore + restore read: parental only.
            # Final verify after put: parental + SIKSIK.
            if phase["get"] <= 2:
                return ProcessResult(
                    command,
                    0,
                    "mobile.parental2025/com.app.service.AccessService\n",
                    "",
                )
            return ProcessResult(
                command,
                0,
                (
                    "mobile.parental2025/com.app.service.AccessService:"
                    "com.siksik.agent/com.siksik.agent.accessibility."
                    "CaptureAccessibilityService\n"
                ),
                "",
            )
        if "put" in command and "enabled_accessibility_services" in command:
            writes.append(command[-1])
            return ProcessResult(command, 0, "", "")
        if "put" in command and "accessibility_enabled" in command:
            return ProcessResult(command, 0, "", "")
        return ProcessResult(command, 0, "", "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    state = await transport.restore_accessibility_service(
        "serial-1",
        "com.siksik.agent",
        "com.siksik.agent/com.siksik.agent.accessibility.CaptureAccessibilityService",
        user_id=0,
    )
    assert state == SpecialAccessState.GRANTED
    assert writes
    assert "mobile.parental2025/com.app.service.AccessService" in writes[0]
    assert (
        "com.siksik.agent/com.siksik.agent.accessibility.CaptureAccessibilityService"
        in writes[0]
    )


@pytest.mark.unit
async def test_accessibility_restore_retries_after_write_secure_settings_grant(
    tmp_path: Path,
) -> None:
    adb = executable(tmp_path)
    puts = {"enabled": 0}

    async def runner(argv, **_kwargs):
        command = tuple(argv)
        if "get" in command and "enabled_accessibility_services" in command:
            if puts["enabled"] == 0:
                return ProcessResult(command, 0, "\n", "")
            return ProcessResult(
                command,
                0,
                "com.siksik.agent/com.siksik.agent.accessibility.CaptureAccessibilityService\n",
                "",
            )
        if "put" in command and "enabled_accessibility_services" in command:
            puts["enabled"] += 1
            if puts["enabled"] == 1:
                return ProcessResult(
                    command,
                    1,
                    "",
                    "java.lang.SecurityException: Permission denial: writing to settings requires:android.permission.WRITE_SECURE_SETTINGS\n",
                )
            return ProcessResult(command, 0, "", "")
        if command[-2:] == (
            "com.siksik.agent",
            "android.permission.WRITE_SECURE_SETTINGS",
        ) or (
            "pm" in command
            and "grant" in command
            and "WRITE_SECURE_SETTINGS" in command[-1]
        ):
            return ProcessResult(command, 0, "", "")
        if "put" in command and "accessibility_enabled" in command:
            return ProcessResult(command, 0, "", "")
        if "getprop" in command:
            return ProcessResult(command, 0, "11\n", "")
        return ProcessResult(command, 0, "", "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    state = await transport.restore_accessibility_service(
        "serial-1",
        "com.siksik.agent",
        "com.siksik.agent/com.siksik.agent.accessibility.CaptureAccessibilityService",
        user_id=0,
    )
    assert state == SpecialAccessState.GRANTED
    assert puts["enabled"] == 2


@pytest.mark.unit
async def test_special_access_probe_and_settings_open_do_not_modify_secure_settings(
    tmp_path: Path,
) -> None:
    adb = executable(tmp_path)
    captured: list[tuple[str, ...]] = []

    async def runner(argv, **_kwargs):
        command = tuple(argv)
        captured.append(command)
        stdout = "com.siksik.agent/com.siksik.agent.AccessibilityService\n"
        return ProcessResult(command, 0, stdout, "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    state = await transport.special_access_state(
        "serial-1",
        "com.siksik.agent",
        SpecialAccessKind.ACCESSIBILITY,
        component="com.siksik.agent/com.siksik.agent.AccessibilityService",
    )
    await transport.open_special_access_settings(
        "serial-1",
        "com.siksik.agent",
        SpecialAccessKind.ACCESSIBILITY,
    )

    assert state == SpecialAccessState.GRANTED
    assert any("android.settings.ACCESSIBILITY_SETTINGS" in command for command in captured)
    assert not any("settings" in command and "put" in command for command in captured)


@pytest.mark.unit
async def test_instrumentation_is_serial_pinned_and_argv_only(tmp_path: Path) -> None:
    adb = executable(tmp_path)
    captured: list[tuple[str, ...]] = []

    async def runner(argv, **_kwargs):
        command = tuple(argv)
        captured.append(command)
        if command[-2:] == ("devices", "-l"):
            return ProcessResult(
                command,
                0,
                "List of devices attached\nserial-1 device model:Fixture\n",
                "",
            )
        return ProcessResult(command, 0, "INSTRUMENTATION_CODE: -1\n", "")

    transport = AsyncAdbTransport(str(adb), runner=runner)
    await transport.run_instrumentation(
        "serial-1",
        runner_component=(
            "com.siksik.agent.automation/com.siksik.agent.automation.SiksikAndroidJUnitRunner"
        ),
        test_class="com.siksik.agent.automation.SocialCrawlInstrumentation",
        arguments={
            "session_id": "session-fixture",
            "crawl_id": "crawl-fixture",
            "target_package": "com.instagram.android",
        },
        timeout=30,
    )

    command = captured[-1]
    assert command[1:3] == ("-s", "serial-1")
    assert command[3:7] == ("shell", "am", "instrument", "-w")
    assert all(value not in {"sh", "bash", "zsh", "-c"} for value in command)

