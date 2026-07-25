from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.acquisition.adb import (
    AdbDevice,
    AndroidDeviceCapabilities,
    DeviceReadiness,
    InstalledPackage,
    PermissionState,
    SpecialAccessKind,
    SpecialAccessState,
)
from app.acquisition.agent_artifact import AgentArtifact
from app.acquisition.agent_client import (
    AgentCapabilitiesV1,
    AgentCapabilityStatusV1,
    AgentHealthV1,
    AgentResponse,
    AgentSessionV1,
)
from app.acquisition.apk_metadata import ApkMetadata
from app.acquisition.bootstrap import (
    AgentBootstrapConfig,
    AndroidAgentBootstrapService,
    InstallAction,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.runtime import (
    AgentRuntimeRegistry,
    AgentRuntimeRepository,
    AgentRuntimeState,
)
from app.core.db import Database, utcnow

SESSION_ID = "session-bootstrap-001"
SERIAL = "device-bootstrap-001"
REQUEST_ID = "request-bootstrap-001"
ARTIFACT_INPUT_SHA = "d" * 64
ARTIFACT_APK_SHA = "a" * 64
SIGNER_SHA = "b" * 64


class FakeArtifacts:
    def __init__(self, artifact: AgentArtifact, error: AcquisitionError | None = None) -> None:
        self.artifact = artifact
        self.error = error
        self.calls = 0

    async def build_debug_apk(self, request_id: str | None = None) -> AgentArtifact:
        assert request_id == REQUEST_ID
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.artifact


class FakeInspector:
    def __init__(self, adb: FakeAdb, desired: ApkMetadata) -> None:
        self.adb = adb
        self.desired = desired
        self.calls: list[Path] = []

    async def inspect(self, apk_path: Path) -> ApkMetadata:
        self.calls.append(apk_path)
        if apk_path == self.desired.path:
            return self.desired
        if self.adb.installed_metadata is None:
            raise AssertionError("installed metadata is unavailable")
        return self.adb.installed_metadata


class FakeAdb:
    def __init__(
        self,
        *,
        installed_metadata: ApkMetadata | None = None,
        api_level: int = 33,
    ) -> None:
        self.installed_metadata = installed_metadata
        self.api_level = api_level
        self.desired_metadata: ApkMetadata | None = None
        self.operations: list[str] = []
        self.install_calls = 0
        self.started_extras: dict[str, str | int] | None = None
        self.created_port = 41001
        self.removed_ports: list[int] = []
        self.force_stop_calls = 0
        self.permission_states: dict[str, PermissionState] = {}
        self.permission_sequences: dict[str, list[PermissionState]] = {}
        self.runtime_permission_settings_opened = 0
        self.special_sequences: dict[SpecialAccessKind, list[SpecialAccessState]] = {}
        self.opened_access: list[SpecialAccessKind] = []
        self.failure_at: str | None = None
        self.cancel_at: str | None = None

    def _step(self, operation: str) -> None:
        self.operations.append(operation)
        if self.cancel_at == operation:
            raise asyncio.CancelledError
        if self.failure_at == operation:
            raise acquisition_error(
                ErrorCategory.ADB_NO_DEVICE,
                "Perangkat terputus.",
                retryable=True,
            )

    async def select_device(self, serial: str) -> AdbDevice:
        assert serial == SERIAL
        self._step("select_device")
        return AdbDevice(serial, "device")

    async def capabilities(
        self,
        serial: str,
        *,
        package_name: str,
        minimum_api: int,
    ) -> AndroidDeviceCapabilities:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert self.api_level >= minimum_api
        self._step("capabilities")
        return AndroidDeviceCapabilities(
            device=AdbDevice(serial, "device"),
            manufacturer="Fixture",
            model="Device",
            android_release="13",
            api_level=self.api_level,
            package_installed=self.installed_metadata is not None,
        )

    async def device_readiness(self, serial: str) -> DeviceReadiness:
        assert serial == SERIAL
        self._step("readiness")
        return DeviceReadiness(True, True, 1024 * 1024 * 1024)

    async def inspect_package(self, serial: str, package_name: str) -> InstalledPackage:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        self._step("inspect_package")
        if self.installed_metadata is None:
            return InstalledPackage(False)
        return InstalledPackage(
            installed=True,
            apk_path="/data/app/~~fixture==/com.siksik.agent-fixture==/base.apk",
            version_code=self.installed_metadata.version_code,
            version_name=self.installed_metadata.version_name,
        )

    async def pull_installed_apk(
        self,
        serial: str,
        remote_path: str,
        destination: Path,
        *,
        timeout: float = 120.0,
    ) -> None:
        assert serial == SERIAL
        assert remote_path.endswith("/base.apk")
        assert timeout > 0
        self._step("pull_installed_apk")
        destination.write_bytes(b"installed-apk")

    async def install_apk(
        self,
        serial: str,
        apk_path: Path,
        *,
        grant_runtime_permissions: bool = True,
        replace_package_on_uid_mismatch: str | None = None,
        timeout: float = 180.0,
        approval_poll_seconds: float = 2.0,
        on_user_restricted=None,
        **_kwargs,
    ) -> None:
        assert serial == SERIAL
        assert grant_runtime_permissions is True
        assert timeout > 0
        assert approval_poll_seconds > 0
        assert self.desired_metadata is not None
        assert apk_path == self.desired_metadata.path
        self._step("install_apk")
        self.install_calls += 1
        self.installed_metadata = self.desired_metadata
        if on_user_restricted is not None:
            assert callable(on_user_restricted)

    async def current_user_id(self, serial: str) -> int:
        assert serial == SERIAL
        self._step("current_user")
        return 0

    async def grant_runtime_permission(
        self,
        serial: str,
        package_name: str,
        permission: str,
        user_id: int,
    ) -> PermissionState:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert user_id == 0
        self._step(f"permission:{permission}")
        sequence = self.permission_sequences.get(permission)
        if sequence:
            if len(sequence) > 1:
                return sequence.pop(0)
            return sequence[0]
        return self.permission_states.get(permission, PermissionState.GRANTED)

    async def runtime_permission_state(
        self,
        serial: str,
        package_name: str,
        permission: str,
        user_id: int,
    ) -> PermissionState:
        return await self.grant_runtime_permission(
            serial,
            package_name,
            permission,
            user_id,
        )

    async def open_runtime_permission_settings(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> None:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert user_id == 0
        self._step("open:runtime_permissions")
        self.runtime_permission_settings_opened += 1

    async def special_access_state(
        self,
        serial: str,
        package_name: str,
        access: SpecialAccessKind,
        *,
        component: str | None = None,
        user_id: int | None = None,
    ) -> SpecialAccessState:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert component is not None or access == SpecialAccessKind.MANAGE_ALL_FILES
        assert user_id == 0
        self._step(f"special:{access.value}")
        sequence = self.special_sequences.get(access, [SpecialAccessState.GRANTED])
        if len(sequence) > 1:
            return sequence.pop(0)
        return sequence[0]

    async def grant_notification_listener(
        self,
        serial: str,
        package_name: str,
        component: str,
        *,
        user_id: int | None = None,
    ) -> SpecialAccessState:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert component
        assert user_id == 0
        self._step("grant:notification_listener")
        sequence = self.special_sequences.get(
            SpecialAccessKind.NOTIFICATION_LISTENER,
            [SpecialAccessState.GRANTED],
        )
        if len(sequence) > 1:
            return sequence.pop(0)
        return sequence[0]

    async def open_special_access_settings(
        self,
        serial: str,
        package_name: str,
        access: SpecialAccessKind,
        *,
        user_id: int | None = None,
    ) -> None:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        assert user_id == 0
        self._step(f"open:{access.value}")
        self.opened_access.append(access)

    async def force_stop(self, serial: str, package_name: str) -> None:
        assert serial == SERIAL
        assert package_name == "com.siksik.agent"
        self._step("force_stop")
        self.force_stop_calls += 1

    async def start_activity(
        self,
        serial: str,
        component: str,
        extras: dict[str, str | int],
        *,
        timeout: float = 30.0,
    ) -> None:
        assert serial == SERIAL
        assert component == "com.siksik.agent/.session.BootstrapActivity"
        assert timeout > 0
        self._step("start_activity")
        self.started_extras = extras

    async def create_forward(self, serial: str, device_port: int) -> int:
        assert serial == SERIAL
        assert device_port == 38471
        self._step("create_forward")
        return self.created_port

    async def remove_forward(self, serial: str, host_port: int) -> None:
        assert serial == SERIAL
        self._step("remove_forward")
        self.removed_ports.append(host_port)


@dataclass
class ClientBehavior:
    session_id: str = SESSION_ID
    api_level: int = 33
    health_failures: int = 0
    api_version: str = "1.0"
    build_sha256: str = ARTIFACT_INPUT_SHA
    stopped: int = 0


class FakeClient:
    def __init__(self, port: int, token: str, behavior: ClientBehavior) -> None:
        assert port == 41001
        assert len(token) >= 32
        self.behavior = behavior

    async def health(self, *, request_id: str | None = None):
        if self.behavior.health_failures > 0:
            self.behavior.health_failures -= 1
            raise acquisition_error(
                ErrorCategory.AGENT_UNREACHABLE,
                "Agent tidak dapat dihubungi.",
                retryable=True,
            )
        body = AgentHealthV1(
            schema_version=1,
            session_id=self.behavior.session_id,
            state="active",
            agent_version="0.2.0",
            agent_build_sha256=self.behavior.build_sha256,
            api_version=self.behavior.api_version,
            api_port=38471,
        )
        return AgentResponse(body, request_id or "request", 200)

    async def capabilities(self, *, request_id: str | None = None):
        granted = AgentCapabilityStatusV1(state="granted", required_for_full=False)
        unavailable = AgentCapabilityStatusV1(state="unavailable", required_for_full=True)
        body = AgentCapabilitiesV1(
            schema_version=1,
            agent_version="0.2.0",
            agent_build_sha256=self.behavior.build_sha256,
            api_version=self.behavior.api_version,
            api_port=38471,
            android_api_level=self.behavior.api_level,
            package_name="com.siksik.agent",
            source_capabilities={"media_image": granted, "visible_ui": unavailable},
            preprocessing_capabilities={"ocr": unavailable},
            feature_capabilities={"loopback_api": granted},
            permission_states={"read_media_images": granted},
            special_access_states={"accessibility": unavailable},
            available_storage_bytes=1024,
            active_session_id=self.behavior.session_id,
        )
        return AgentResponse(body, request_id or "request", 200)

    async def bootstrap(
        self,
        session_id: str,
        api_version: str,
        *,
        request_id: str | None = None,
    ):
        return AgentResponse(
            AgentSessionV1(session_id=session_id, api_version=api_version, state="active"),
            request_id or "request",
            201,
        )

    async def stop(self, session_id: str, *, request_id: str | None = None):
        self.behavior.stopped += 1
        return AgentResponse(
            AgentSessionV1(session_id=session_id, api_version="1.0", state="closed"),
            request_id or "request",
            200,
        )


async def create_session(database: Database, session_id: str = SESSION_ID) -> None:
    now = utcnow()
    await database.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            SERIAL,
            "android",
            "Fixture",
            "quick",
            "lulus",
            "pending",
            "{}",
            "{}",
            None,
            None,
            now,
            now,
        ),
    )


def desired_metadata() -> ApkMetadata:
    return ApkMetadata(
        path=Path("/fixture/app-debug.apk"),
        package_name="com.siksik.agent",
        version_code=2,
        version_name="0.2.0",
        signer_sha256=SIGNER_SHA,
        apk_sha256=ARTIFACT_APK_SHA,
        size_bytes=1024,
        uses_shared_user_id=True,
    )


def installed_metadata(
    *,
    version_code: int = 2,
    signer_sha256: str = SIGNER_SHA,
    apk_sha256: str = ARTIFACT_APK_SHA,
    uses_shared_user_id: bool = True,
) -> ApkMetadata:
    return ApkMetadata(
        path=Path("/fixture/installed.apk"),
        package_name="com.siksik.agent",
        version_code=version_code,
        version_name=f"0.{version_code}.0",
        signer_sha256=signer_sha256,
        apk_sha256=apk_sha256,
        size_bytes=1024,
        uses_shared_user_id=uses_shared_user_id,
    )


async def make_service(
    tmp_path: Path,
    *,
    installed: ApkMetadata | None = None,
    special_access: tuple[SpecialAccessKind, ...] = (),
    artifact_error: AcquisitionError | None = None,
    behavior: ClientBehavior | None = None,
    force_reinstall: bool = False,
) -> tuple[AndroidAgentBootstrapService, FakeAdb, Database, FakeArtifacts, ClientBehavior]:
    database = Database(tmp_path / "bootstrap.db")
    await database.connect()
    await create_session(database)
    repository = AgentRuntimeRepository(database)
    registry = AgentRuntimeRegistry()
    desired = desired_metadata()
    adb = FakeAdb(installed_metadata=installed)
    adb.desired_metadata = desired
    artifact = AgentArtifact(
        path=desired.path,
        input_sha256=ARTIFACT_INPUT_SHA,
        apk_sha256=ARTIFACT_APK_SHA,
        size_bytes=desired.size_bytes,
        reused=False,
    )
    artifacts = FakeArtifacts(artifact, artifact_error)
    client_behavior = behavior or ClientBehavior()
    service = AndroidAgentBootstrapService(
        AgentBootstrapConfig(
            package_name="com.siksik.agent",
            component="com.siksik.agent/.session.BootstrapActivity",
            api_version="1.0",
            device_port=38471,
            minimum_api=26,
            token_ttl_seconds=600,
            install_timeout_seconds=30,
            minimum_device_storage_bytes=128 * 1024 * 1024,
            special_access_timeout_seconds=0.01,
            special_access_poll_seconds=0.001,
            required_special_access=special_access,
            accessibility_component="com.siksik.agent/.AccessibilityService",
            notification_component="com.siksik.agent/.NotificationService",
            inspection_root=tmp_path / "inspection",
            force_reinstall=force_reinstall,
        ),
        adb,  # type: ignore[arg-type]
        artifacts,
        FakeInspector(adb, desired),
        lambda port, token: FakeClient(port, token, client_behavior),
        repository=repository,
        registry=registry,
        clock=lambda: datetime(2026, 7, 16, tzinfo=timezone.utc),
        token_factory=lambda: "token_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    return service, adb, database, artifacts, client_behavior


async def run_bootstrap(service: AndroidAgentBootstrapService):
    progress: list[tuple[SessionStatus, float, str, dict[str, object]]] = []

    async def publish(phase, percent, message, **fields):
        progress.append((phase, percent, message, fields))

    record = await service.bootstrap(
        session_id=SESSION_ID,
        serial=SERIAL,
        request_id=REQUEST_ID,
        on_progress=publish,
    )
    return record, progress


@pytest.mark.unit
async def test_first_install_reaches_ready_with_complete_trace(tmp_path: Path) -> None:
    service, adb, database, artifacts, behavior = await make_service(tmp_path)
    record, progress = await run_bootstrap(service)

    assert record.state == AgentRuntimeState.READY
    assert record.details["install_action"] == InstallAction.INSTALL.value
    assert record.agent_build_sha256 == ARTIFACT_INPUT_SHA
    assert record.artifact_sha256 == ARTIFACT_APK_SHA
    assert adb.install_calls == 1
    assert adb.started_extras is not None
    assert "session_token" in adb.started_extras
    states = [item[3]["bootstrap_state"] for item in progress]
    assert states == [
        "detect_device",
        "validate_device",
        "resolve_or_build_agent",
        "inspect_installed_package",
        "install_or_update",
        "apply_runtime_permissions",
        "verify_special_access",
        "start_agent",
        "create_forward",
        "authenticate_and_negotiate",
        "ready",
    ]
    events = await database.fetchall(
        "SELECT state FROM agent_bootstrap_events WHERE session_id = ? ORDER BY id",
        (SESSION_ID,),
    )
    assert [row["state"] for row in events] == states
    await service.teardown(SESSION_ID, REQUEST_ID)
    assert adb.removed_ports == [41001]
    assert behavior.stopped == 1
    assert (await service.repository.get(SESSION_ID)).state == AgentRuntimeState.CLOSED
    assert artifacts.calls == 1
    await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("installed", "expected_action", "install_calls"),
    [
        (installed_metadata(), InstallAction.CURRENT, 0),
        (
            installed_metadata(version_code=1, apk_sha256="c" * 64),
            InstallAction.UPDATE,
            1,
        ),
        (
            installed_metadata(version_code=2, apk_sha256="c" * 64),
            InstallAction.UPDATE,
            1,
        ),
        # Signature berubah: reinstall via uninstall-otomatis, bukan gagal di sini.
        (
            installed_metadata(signer_sha256="e" * 64),
            InstallAction.UPDATE,
            1,
        ),
        # App lama tanpa sharedUserId vs artifact baru yang memakai sharedUserId.
        (
            installed_metadata(uses_shared_user_id=False),
            InstallAction.UPDATE,
            1,
        ),
    ],
)
async def test_install_decision_is_deterministic(
    tmp_path: Path,
    installed: ApkMetadata,
    expected_action: InstallAction,
    install_calls: int,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        installed=installed,
    )
    record, _progress = await run_bootstrap(service)

    assert record.details["install_action"] == expected_action.value
    assert adb.install_calls == install_calls
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
async def test_force_reinstall_always_updates_matching_apk(tmp_path: Path) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        installed=installed_metadata(),
        force_reinstall=True,
    )
    record, _progress = await run_bootstrap(service)

    assert record.details["install_action"] == InstallAction.UPDATE.value
    assert adb.install_calls == 1
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
async def test_signature_mismatch_reinstalls_via_uid_uninstall(
    tmp_path: Path,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        installed=installed_metadata(signer_sha256="e" * 64),
    )
    record, _progress = await run_bootstrap(service)

    assert record.state == AgentRuntimeState.READY
    assert record.details["install_action"] == InstallAction.UPDATE.value
    assert adb.install_calls == 1
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("installed", "category"),
    [
        (installed_metadata(version_code=3), ErrorCategory.AGENT_VERSION_MISMATCH),
    ],
)
async def test_incompatible_installed_package_fails_before_install(
    tmp_path: Path,
    installed: ApkMetadata,
    category: ErrorCategory,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        installed=installed,
    )

    with pytest.raises(AcquisitionError) as captured:
        await run_bootstrap(service)

    assert captured.value.category == category
    assert adb.install_calls == 0
    assert (await service.repository.get(SESSION_ID)).state == AgentRuntimeState.FAILED
    await database.close()


@pytest.mark.unit
async def test_build_failure_is_persisted_with_stable_category(tmp_path: Path) -> None:
    error = acquisition_error(ErrorCategory.AGENT_BUILD_FAILED, "Build agent gagal.")
    service, _adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        artifact_error=error,
    )

    with pytest.raises(AcquisitionError) as captured:
        await run_bootstrap(service)

    assert captured.value.category == ErrorCategory.AGENT_BUILD_FAILED
    record = await service.repository.get(SESSION_ID)
    assert record.error_category == ErrorCategory.AGENT_BUILD_FAILED.value
    await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("permission_state", "category"),
    [
        (PermissionState.DENIED, ErrorCategory.ACCESS_TIMEOUT),
        (PermissionState.UNSUPPORTED, ErrorCategory.RUNTIME_PERMISSION_UNSUPPORTED),
    ],
)
async def test_required_runtime_permission_failure_is_not_silent(
    tmp_path: Path,
    permission_state: PermissionState,
    category: ErrorCategory,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    adb.permission_states["android.permission.READ_MEDIA_IMAGES"] = permission_state

    with pytest.raises(AcquisitionError) as captured:
        await run_bootstrap(service)

    assert captured.value.category == category
    await database.close()


@pytest.mark.unit
async def test_required_runtime_permission_waits_for_storage_approval(
    tmp_path: Path,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    permission = "android.permission.READ_MEDIA_IMAGES"
    adb.permission_sequences[permission] = [
        PermissionState.DENIED,
        PermissionState.GRANTED,
    ]

    record, progress = await run_bootstrap(service)

    assert record.state == AgentRuntimeState.READY
    assert adb.runtime_permission_settings_opened == 1
    assert record.details["runtime_permissions"]["read_media_images"] == "granted"
    assert "awaiting_runtime_permission" in [
        item[3]["bootstrap_state"] for item in progress
    ]
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
async def test_optional_notification_permission_may_remain_denied(tmp_path: Path) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    adb.permission_states["android.permission.POST_NOTIFICATIONS"] = PermissionState.DENIED

    record, _progress = await run_bootstrap(service)

    assert record.state == AgentRuntimeState.READY
    assert record.details["runtime_permissions"]["post_notifications"] == "denied"
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
async def test_special_access_waits_and_continues_after_approval(tmp_path: Path) -> None:
    access = SpecialAccessKind.ACCESSIBILITY
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        special_access=(access,),
    )
    adb.special_sequences[access] = [
        SpecialAccessState.NOT_GRANTED,
        SpecialAccessState.GRANTED,
    ]

    record, progress = await run_bootstrap(service)

    assert record.state == AgentRuntimeState.READY
    assert adb.opened_access == [access]
    assert "awaiting_access" in [item[3]["bootstrap_state"] for item in progress]
    assert record.details["special_access"]["accessibility"] == "granted"
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sequence", "category"),
    [
        (
            [SpecialAccessState.NOT_GRANTED, SpecialAccessState.DENIED],
            ErrorCategory.ACCESS_DENIED,
        ),
        ([SpecialAccessState.NOT_GRANTED], ErrorCategory.ACCESS_TIMEOUT),
    ],
)
async def test_special_access_denial_and_timeout_are_distinct(
    tmp_path: Path,
    sequence: list[SpecialAccessState],
    category: ErrorCategory,
) -> None:
    access = SpecialAccessKind.ACCESSIBILITY
    service, adb, database, _artifacts, _behavior = await make_service(
        tmp_path,
        special_access=(access,),
    )
    adb.special_sequences[access] = sequence

    with pytest.raises(AcquisitionError) as captured:
        await run_bootstrap(service)

    assert captured.value.category == category
    await database.close()


@pytest.mark.unit
async def test_optional_all_files_denial_continues_with_explicit_capability_state(
    tmp_path: Path,
) -> None:
    access = SpecialAccessKind.MANAGE_ALL_FILES
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    adb.special_sequences[access] = [
        SpecialAccessState.NOT_GRANTED,
        SpecialAccessState.DENIED,
    ]

    async def publish(*_args, **_kwargs) -> None:
        return None

    record = await service.bootstrap(
        session_id=SESSION_ID,
        serial=SERIAL,
        request_id=REQUEST_ID,
        on_progress=publish,
        optional_special_access=(access,),
    )

    assert record.state == AgentRuntimeState.READY
    assert adb.opened_access == [access]
    assert record.details["special_access"][access.value] == SpecialAccessState.DENIED.value
    await service.teardown(SESSION_ID)
    await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure_at",
    [
        "select_device",
        "capabilities",
        "readiness",
        "inspect_package",
        "install_apk",
        "current_user",
        "start_activity",
        "create_forward",
    ],
)
async def test_disconnect_at_bootstrap_states_is_categorized(
    tmp_path: Path,
    failure_at: str,
) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    adb.failure_at = failure_at

    with pytest.raises(AcquisitionError) as captured:
        await run_bootstrap(service)

    assert captured.value.category == ErrorCategory.ADB_NO_DEVICE
    record = await service.repository.get(SESSION_ID)
    assert record.state == AgentRuntimeState.FAILED
    assert record.retryable is True
    await database.close()


@pytest.mark.unit
async def test_cancellation_after_agent_start_preserves_special_access(tmp_path: Path) -> None:
    service, adb, database, _artifacts, _behavior = await make_service(tmp_path)
    adb.cancel_at = "create_forward"

    with pytest.raises(asyncio.CancelledError):
        await run_bootstrap(service)

    record = await service.repository.get(SESSION_ID)
    assert record.state == AgentRuntimeState.CANCELLED
    assert adb.force_stop_calls == 0
    await database.close()


@pytest.mark.unit
async def test_backend_restart_removes_persisted_forward_and_rotates_runtime(tmp_path: Path) -> None:
    service, adb, database, artifacts, behavior = await make_service(tmp_path)
    first, _progress = await run_bootstrap(service)
    assert first.forward_host_port == 41001

    restarted_registry = AgentRuntimeRegistry()
    restarted = AndroidAgentBootstrapService(
        service.config,
        adb,  # type: ignore[arg-type]
        artifacts,
        service.inspector,
        lambda port, token: FakeClient(port, token, behavior),
        repository=service.repository,
        registry=restarted_registry,
        clock=lambda: datetime(2026, 7, 16, tzinfo=timezone.utc),
        token_factory=lambda: "rotated_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    second, _progress = await run_bootstrap(restarted)

    assert second.state == AgentRuntimeState.READY
    assert 41001 in adb.removed_ports
    assert adb.install_calls == 1
    await restarted.teardown(SESSION_ID)
    await service.shutdown()
    await database.close()


@pytest.mark.unit
async def test_agent_process_death_rotates_forward_on_retry(tmp_path: Path) -> None:
    service, adb, database, _artifacts, behavior = await make_service(tmp_path)
    await run_bootstrap(service)
    behavior.health_failures = 1

    recovered, _progress = await run_bootstrap(service)

    assert recovered.state == AgentRuntimeState.READY
    assert 41001 in adb.removed_ports
    await service.teardown(SESSION_ID)
    await database.close()
