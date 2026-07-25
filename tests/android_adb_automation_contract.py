#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.acquisition.adb import (
    AdbDevice,
    AndroidDeviceCapabilities,
    AsyncAdbTransport,
    PermissionState,
    SpecialAccessKind,
    SpecialAccessState,
    parse_runtime_permission_probe,
)
from app.acquisition.agent_client import AgentClientConfig, INVENTORY_SOURCES
from app.acquisition.bootstrap_components import AgentAccessCoordinator
from app.acquisition.bootstrap_contracts import AgentBootstrapConfig, BootstrapWorkingState
from app.acquisition.bootstrap_runner import (
    INVENTORY_PAGE_LIMITS,
    LIVE_ANALYSIS_BATCH_SIZE,
    LIVE_SELECTION_PAGE_LIMIT,
    SELECTION_CANDIDATE_PAGE_LIMIT,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.process import ProcessResult

SERIAL = "contract-device-001"
PACKAGE = "com.siksik.agent"
LISTENER = "com.siksik.agent/com.siksik.agent.notification.SessionNotificationListener"


def _readiness_result(command: tuple[str, ...]) -> ProcessResult | None:
    if len(command) >= 3 and command[-2] == "getprop":
        prop = command[-1]
        values = {
            "sys.boot_completed": "1",
            "ro.product.manufacturer": "Google",
            "ro.product.brand": "google",
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
        }
        return ProcessResult(command, 0, f"{values.get(prop, '')}\n", "")
    if "dumpsys" in command and "window" in command:
        return ProcessResult(
            command,
            0,
            "mShowingLockscreen=false mKeyguardShowing=false\n",
            "",
        )
    if "df" in command:
        return ProcessResult(
            command,
            0,
            "Filesystem 1K-blocks Used Available Use% Mounted on\n"
            "/data 1000000 1000 900000 1% /data\n",
            "",
        )
    return None


async def run_contract() -> None:
    commands: list[tuple[str, ...]] = []
    permission_checks = 0
    listener_allowed = False

    async def runner(argv, **_kwargs):
        nonlocal permission_checks, listener_allowed
        command = tuple(argv)
        commands.append(command)
        if command[-2:] == ("devices", "-l"):
            output = f"List of devices attached\n{SERIAL} device model:Contract\n"
        elif command[-2:] == ("am", "get-current-user"):
            output = "0\n"
        elif (readiness := _readiness_result(command)) is not None:
            return readiness
        elif "dumpsys" in command and "package" in command:
            permission_checks += 1
            granted = "true" if permission_checks > 1 else "false"
            output = (
                "User 0:\n  runtime permissions:\n"
                "    android.permission.READ_MEDIA_IMAGES: "
                f"granted={granted}, flags=[]\n"
            )
        elif "enabled_notification_listeners" in command:
            output = f"{LISTENER}\n" if listener_allowed else "null\n"
        elif "allow_listener" in command:
            listener_allowed = True
            output = ""
        elif "am" in command and "start" in command:
            action = command[command.index("-a") + 1]
            if action == "android.settings.ACCESSIBILITY_SETTINGS":
                output = (
                    "Starting: Intent { act=android.settings.ACCESSIBILITY_SETTINGS }\n"
                    "Warning: Activity not started, intent has been delivered to currently "
                    "running top-most instance.\n"
                    "Status: ok\n"
                    "LaunchState: UNKNOWN (0)\n"
                    "Activity: com.android.settings/.SubSettings\n"
                )
            else:
                output = "Status: ok\n"
        else:
            output = "Success\n"
        return ProcessResult(command, 0, output, "")

    with tempfile.TemporaryDirectory(prefix="siksik-adb-contract-") as temporary:
        root = Path(temporary)
        adb = root / "adb"
        adb.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        adb.chmod(0o700)
        apk = root / "agent.apk"
        apk.write_bytes(b"contract-apk")
        automation_apk = root / "automation.apk"
        automation_apk.write_bytes(b"contract-automation-apk")
        transport = AsyncAdbTransport(str(adb), runner=runner)

        await transport.install_apk(SERIAL, apk, grant_runtime_permissions=True)
        await transport.install_apk(
            SERIAL,
            automation_apk,
            grant_runtime_permissions=False,
            allow_test_packages=True,
        )
        await transport.grant_runtime_permission(
            SERIAL,
            PACKAGE,
            "android.permission.READ_MEDIA_IMAGES",
            0,
        )
        notification = await transport.grant_notification_listener(
            SERIAL,
            PACKAGE,
            LISTENER,
            user_id=0,
        )
        await transport.open_special_access_settings(
            SERIAL,
            PACKAGE,
            SpecialAccessKind.ACCESSIBILITY,
            user_id=0,
        )
        await transport.open_special_access_settings(
            SERIAL,
            PACKAGE,
            SpecialAccessKind.MANAGE_ALL_FILES,
            user_id=0,
        )
        try:
            await transport.open_special_access_settings(
                SERIAL,
                PACKAGE,
                SpecialAccessKind.NOTIFICATION_LISTENER,
                user_id=0,
            )
        except AcquisitionError as exc:
            assert exc.category == ErrorCategory.VALIDATION_ERROR
        else:
            raise AssertionError("notification settings must not be opened")

        migration_commands: list[tuple[str, ...]] = []
        migration_installs = 0

        async def migration_runner(argv, **_kwargs):
            nonlocal migration_installs
            command = tuple(argv)
            migration_commands.append(command)
            if command[-2:] == ("devices", "-l"):
                return ProcessResult(
                    command,
                    0,
                    f"List of devices attached\n{SERIAL} device model:Contract\n",
                    "",
                )
            readiness = _readiness_result(command)
            if readiness is not None:
                return readiness
            if "install" in command:
                migration_installs += 1
                if migration_installs == 1:
                    return ProcessResult(
                        command,
                        1,
                        "",
                        "Failure [INSTALL_FAILED_UID_CHANGED: package UID changed]",
                    )
            return ProcessResult(command, 0, "Success\n", "")

        migration_transport = AsyncAdbTransport(str(adb), runner=migration_runner)
        await migration_transport.install_apk(
            SERIAL,
            apk,
            grant_runtime_permissions=True,
            replace_package_on_uid_mismatch=PACKAGE,
        )
        assert migration_installs == 2
        assert any(command[-2:] == ("uninstall", PACKAGE) for command in migration_commands)

        fallback_commands: list[tuple[str, ...]] = []

        async def runtime_grant_fallback_runner(argv, **_kwargs):
            command = tuple(argv)
            fallback_commands.append(command)
            if command[-2:] == ("devices", "-l"):
                return ProcessResult(
                    command,
                    0,
                    f"List of devices attached\n{SERIAL} device model:Contract\n",
                    "",
                )
            readiness = _readiness_result(command)
            if readiness is not None:
                return readiness
            if "dumpsys" in command and "package" in command:
                return ProcessResult(
                    command,
                    0,
                    "requested permissions:\n"
                    "  android.permission.READ_EXTERNAL_STORAGE\n"
                    "User 0:\n"
                    "  runtime permissions:\n"
                    "    android.permission.READ_EXTERNAL_STORAGE: granted=false, flags=[]\n",
                    "",
                )
            if "install" in command and "-g" in command:
                return ProcessResult(
                    command,
                    1,
                    "",
                    "java.lang.SecurityException: You need the "
                    "android.permission.INSTALL_GRANT_RUNTIME_PERMISSIONS permission to use "
                    "the PackageManager.INSTALL_GRANT_RUNTIME_PERMISSIONS flag",
                )
            return ProcessResult(command, 0, "Success\n", "")

        fallback_transport = AsyncAdbTransport(
            str(adb),
            runner=runtime_grant_fallback_runner,
        )
        await fallback_transport.install_apk(
            SERIAL,
            apk,
            grant_runtime_permissions=True,
        )
        fallback_installs = [command for command in fallback_commands if "install" in command]
        assert len(fallback_installs) == 2
        assert "-g" in fallback_installs[0]
        assert "-g" not in fallback_installs[1]
        permission_state = await fallback_transport.grant_runtime_permission(
            SERIAL,
            PACKAGE,
            "android.permission.READ_EXTERNAL_STORAGE",
            0,
        )
        assert permission_state == PermissionState.DENIED
        assert not any(
            "pm" in command and "grant" in command for command in fallback_commands
        )
        await fallback_transport.open_runtime_permission_settings(
            SERIAL,
            PACKAGE,
            user_id=0,
        )
        permission_settings = next(
            command
            for command in fallback_commands
            if "android.settings.APPLICATION_DETAILS_SETTINGS" in command
        )
        assert permission_settings[1:3] == ("-s", SERIAL)
        assert f"package:{PACKAGE}" in permission_settings

        probe_payload = (
            "SIKSIK_PERMISSION_V1;"
            "android.permission.READ_EXTERNAL_STORAGE=granted;"
            "android.permission.READ_MEDIA_IMAGES=unsupported;"
            "android.permission.READ_MEDIA_VIDEO=unsupported;"
            "android.permission.READ_MEDIA_AUDIO=unsupported;"
            "android.permission.ACCESS_MEDIA_LOCATION=denied;"
            "android.permission.POST_NOTIFICATIONS=unsupported;"
            "android.permission.READ_SMS=denied;"
            "android.permission.READ_CONTACTS=denied"
        )
        parsed_probe = parse_runtime_permission_probe(
            f'Broadcast completed: result=-1, data="{probe_payload}"\n'
        )
        assert parsed_probe is not None
        assert (
            parsed_probe["android.permission.READ_EXTERNAL_STORAGE"]
            == PermissionState.GRANTED
        )
        assert (
            parsed_probe["android.permission.READ_MEDIA_IMAGES"]
            == PermissionState.UNSUPPORTED
        )

        probe_commands: list[tuple[str, ...]] = []

        async def permission_probe_runner(argv, **_kwargs):
            command = tuple(argv)
            probe_commands.append(command)
            if command[-2:] == ("devices", "-l"):
                return ProcessResult(
                    command,
                    0,
                    f"List of devices attached\n{SERIAL} device model:Contract\n",
                    "",
                )
            if "broadcast" in command:
                return ProcessResult(
                    command,
                    0,
                    f'Broadcast completed: result=-1, data="{probe_payload}"\n',
                    "",
                )
            return ProcessResult(command, 1, "", "unexpected command")

        probe_transport = AsyncAdbTransport(str(adb), runner=permission_probe_runner)
        probe_state = await probe_transport.runtime_permission_state(
            SERIAL,
            PACKAGE,
            "android.permission.READ_EXTERNAL_STORAGE",
            0,
        )
        assert probe_state == PermissionState.GRANTED
        assert any("broadcast" in command for command in probe_commands)
        assert not any("dumpsys" in command for command in probe_commands)
        assert all(command[1:3] == ("-s", SERIAL) for command in probe_commands)

        transport_commands: list[tuple[str, ...]] = []

        async def transport_fallback_runner(argv, **_kwargs):
            command = tuple(argv)
            transport_commands.append(command)
            if command[-2:] == ("devices", "-l"):
                return ProcessResult(
                    command,
                    0,
                    f"List of devices attached\n{SERIAL} device model:Contract\n",
                    "",
                )
            readiness = _readiness_result(command)
            if readiness is not None:
                return readiness
            if "install" in command and "--no-streaming" not in command:
                return ProcessResult(
                    command,
                    1,
                    "Performing Streamed Install\n",
                    "adb: error: connection reset by peer",
                )
            return ProcessResult(command, 0, "Success\n", "")

        transport = AsyncAdbTransport(str(adb), runner=transport_fallback_runner)
        transport_outcome = await transport.install_apk(
            SERIAL,
            automation_apk,
            grant_runtime_permissions=False,
            allow_test_packages=True,
        )
        transport_installs = [
            command for command in transport_commands if "install" in command
        ]
        assert len(transport_installs) == 2
        assert "--no-streaming" not in transport_installs[0]
        assert "--no-streaming" in transport_installs[1]
        assert "-t" in transport_installs[1]
        assert transport_outcome.strategy == "push_post_grant_test"

        async def restricted_runner(argv, **_kwargs):
            command = tuple(argv)
            if command[-2:] == ("devices", "-l"):
                return ProcessResult(
                    command,
                    0,
                    f"List of devices attached\n{SERIAL} device model:Contract\n",
                    "",
                )
            readiness = _readiness_result(command)
            if readiness is not None:
                # Override manufacturer for OEM guidance assertion.
                if len(command) >= 3 and command[-2] == "getprop" and command[-1] == "ro.product.manufacturer":
                    return ProcessResult(command, 0, "Xiaomi\n", "")
                if len(command) >= 3 and command[-2] == "getprop" and command[-1] == "ro.product.brand":
                    return ProcessResult(command, 0, "Redmi\n", "")
                return readiness
            return ProcessResult(
                command,
                1,
                "Performing Streamed Install\n",
                "Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]",
            )

        restricted = AsyncAdbTransport(str(adb), runner=restricted_runner)
        try:
            await restricted.install_apk(
                SERIAL,
                apk,
                grant_runtime_permissions=True,
                timeout=3.0,
                approval_poll_seconds=1.0,
            )
        except AcquisitionError as exc:
            assert exc.category == ErrorCategory.ACCESS_DENIED
            assert exc.retryable is True
            assert "Xiaomi" in exc.public_message or "MIUI" in exc.public_message
        else:
            raise AssertionError("OEM install restriction must remain explicit")

        class RuntimePermissionAdb:
            def __init__(self) -> None:
                self.settings_opened = 0
                self.image_checks = 0

            async def current_user_id(self, serial: str) -> int:
                assert serial == SERIAL
                return 0

            async def grant_runtime_permission(
                self,
                serial: str,
                package_name: str,
                permission: str,
                user_id: int,
            ) -> PermissionState:
                assert serial == SERIAL
                assert package_name == PACKAGE
                assert user_id == 0
                if permission == "android.permission.READ_MEDIA_IMAGES":
                    return PermissionState.DENIED
                return PermissionState.GRANTED

            async def open_runtime_permission_settings(
                self,
                serial: str,
                package_name: str,
                *,
                user_id: int | None = None,
            ) -> None:
                assert serial == SERIAL
                assert package_name == PACKAGE
                assert user_id == 0
                self.settings_opened += 1

            async def runtime_permission_state(
                self,
                serial: str,
                package_name: str,
                permission: str,
                user_id: int,
            ) -> PermissionState:
                assert serial == SERIAL
                assert package_name == PACKAGE
                assert permission == "android.permission.READ_MEDIA_IMAGES"
                assert user_id == 0
                self.image_checks += 1
                return PermissionState.GRANTED

        access_adb = RuntimePermissionAdb()
        access = AgentAccessCoordinator(
            AgentBootstrapConfig(
                package_name=PACKAGE,
                component=f"{PACKAGE}/.session.BootstrapActivity",
                api_version="1.0",
                device_port=38471,
                minimum_api=26,
                token_ttl_seconds=600,
                install_timeout_seconds=30,
                minimum_device_storage_bytes=1,
                special_access_timeout_seconds=1,
                special_access_poll_seconds=0.001,
                required_special_access=(),
                accessibility_component=f"{PACKAGE}/.accessibility.Service",
                notification_component=f"{PACKAGE}/.notification.Listener",
                inspection_root=root,
            ),
            access_adb,  # type: ignore[arg-type]
            sleep=asyncio.sleep,
        )
        work = BootstrapWorkingState()
        awaiting_updates = 0

        async def publish_runtime_awaiting() -> None:
            nonlocal awaiting_updates
            awaiting_updates += 1

        await access.apply_runtime_permissions(
            "session-contract-001",
            SERIAL,
            "request-contract-001",
            AndroidDeviceCapabilities(
                device=AdbDevice(SERIAL, "device"),
                manufacturer="Contract",
                model="Fixture",
                android_release="13",
                api_level=33,
                package_installed=True,
            ),
            work,
            publish_runtime_awaiting,
        )
        assert access_adb.settings_opened == 1
        assert access_adb.image_checks == 1
        assert awaiting_updates == 1
        assert work.runtime_permissions["read_media_images"] == "granted"

    assert notification == SpecialAccessState.GRANTED
    assert AgentClientConfig().max_response_bytes == 4 * 1024 * 1024
    assert set(INVENTORY_PAGE_LIMITS) == INVENTORY_SOURCES
    assert max(INVENTORY_PAGE_LIMITS.values()) == 100
    assert INVENTORY_PAGE_LIMITS["accessibility_visible_ui"] == 50
    assert LIVE_SELECTION_PAGE_LIMIT == 16
    assert LIVE_ANALYSIS_BATCH_SIZE == 64
    assert SELECTION_CANDIDATE_PAGE_LIMIT == 50
    unresolved = ProcessResult(
        ("adb",),
        0,
        "Error: Activity not started, unable to resolve Intent.\n",
        "",
    )
    assert not AsyncAdbTransport._activity_started(unresolved)
    assert any(command[-4:-1] == ("install", "-r", "-g") for command in commands)
    assert any(command[-3:-1] == ("-r", "-t") for command in commands)
    assert any("pm" in command and "grant" in command for command in commands)
    assert any("cmd" in command and "allow_listener" in command for command in commands)
    opened_actions = {
        command[command.index("-a") + 1]
        for command in commands
        if "-a" in command and "am" in command and "start" in command
    }
    assert opened_actions == {
        "android.settings.ACCESSIBILITY_SETTINGS",
        "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION",
    }
    device_commands = [command for command in commands if command[-2:] != ("devices", "-l")]
    assert device_commands
    assert all(command[1:3] == ("-s", SERIAL) for command in device_commands)


if __name__ == "__main__":
    asyncio.run(run_contract())
    print("android_adb_automation_contract=ok")
