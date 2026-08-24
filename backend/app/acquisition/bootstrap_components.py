from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.acquisition.adb import (
    AccessibilityBindingState,
    AndroidDeviceCapabilities,
    AsyncAdbTransport,
    InstalledPackage,
    PermissionState,
    SpecialAccessKind,
    SpecialAccessState,
)
from app.acquisition.bootstrap_contracts import (
    SPECIAL_ACCESS_WAIT_MESSAGES,
    AgentBootstrapConfig,
    AgentClientFactory,
    BootstrapWorkingState,
    InstallAction,
    MetadataInspector,
    RuntimePermissionRequirement,
    Sleep,
    runtime_permissions_for_api,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.apk_metadata import ApkMetadata
from app.acquisition.runtime import device_ref

logger = logging.getLogger("siksik.acquisition.bootstrap")

NOTIFICATION_GRANT_SETTLE_ATTEMPTS = 3
MEDIA_RUNTIME_PERMISSIONS = frozenset({
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_MEDIA_AUDIO",
})
COMMUNICATION_RUNTIME_PERMISSIONS = frozenset({
    "android.permission.READ_SMS",
    "android.permission.READ_CONTACTS",
})


class AgentPackageCoordinator:
    def __init__(
        self,
        config: AgentBootstrapConfig,
        adb: AsyncAdbTransport,
        inspector: MetadataInspector,
    ) -> None:
        self._config = config
        self._adb = adb
        self._inspector = inspector

    def validate_desired_apk(self, work: BootstrapWorkingState) -> None:
        if work.artifact is None or work.desired_apk is None:
            raise acquisition_error(ErrorCategory.INTERNAL_ERROR, "Artifact agent belum tersedia.")
        if work.desired_apk.package_name != self._config.package_name:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Application ID artifact agent tidak sesuai konfigurasi SIKSIK.",
            )
        if work.desired_apk.apk_sha256 != work.artifact.apk_sha256:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Hash artifact agent berubah setelah build.",
            )

    async def inspect_installed_apk(
        self,
        serial: str,
        package: InstalledPackage,
    ) -> ApkMetadata:
        if package.apk_path is None:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Path APK agent terpasang tidak tersedia.",
            )
        root = self._config.inspection_root.expanduser().resolve()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        temporary = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="agent-apk-", dir=root))
        destination = temporary / "installed-base.apk"
        try:
            await self._adb.pull_installed_apk(serial, package.apk_path, destination)
            return await self._inspector.inspect(destination)
        finally:
            await asyncio.to_thread(shutil.rmtree, temporary, True)

    def install_action(self, work: BootstrapWorkingState) -> InstallAction:
        if work.desired_apk is None or work.installed_package is None:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Status install agent belum tersedia.",
            )
        if not work.installed_package.installed:
            return InstallAction.INSTALL
        if work.installed_apk is None:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "APK terpasang tidak valid.",
            )
        if work.installed_apk.version_code > work.desired_apk.version_code:
            raise acquisition_error(
                ErrorCategory.AGENT_VERSION_MISMATCH,
                "Versi Android agent terpasang lebih baru dari artifact SIKSIK.",
            )
        if (
            work.installed_apk.signer_sha256 != work.desired_apk.signer_sha256
            or not work.installed_apk.uses_shared_user_id
        ):
            return InstallAction.UPDATE
        if work.installed_apk.apk_sha256 == work.desired_apk.apk_sha256:
            return InstallAction.CURRENT
        if self._config.force_reinstall:
            return InstallAction.UPDATE
        return InstallAction.UPDATE

    async def install_if_needed(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
        work: BootstrapWorkingState,
        on_user_restricted: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if work.artifact is None or work.desired_apk is None or work.install_action is None:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Rencana install agent tidak valid.",
            )
        if work.install_action == InstallAction.CURRENT:
            work.install_strategy = "reuse_current"
            logger.info(
                "agent_install_completed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "state": InstallAction.CURRENT.value,
                },
            )
            return
        started = time.monotonic()
        logger.info(
            "agent_install_started",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "state": work.install_action.value,
            },
        )
        try:
            outcome = await self._adb.install_apk(
                serial,
                work.artifact.path,
                grant_runtime_permissions=True,
                replace_package_on_uid_mismatch=self._config.package_name,
                timeout=self._config.install_timeout_seconds,
                approval_poll_seconds=max(
                    1.0,
                    min(self._config.special_access_poll_seconds, 3.0),
                ),
                on_user_restricted=on_user_restricted,
            )
            if outcome is not None:
                work.install_strategy = outcome.strategy
                work.install_attempt_count = outcome.attempt_count
                work.runtime_granted_during_install = (
                    outcome.runtime_granted_during_install
                )
            installed = await self._adb.inspect_package(serial, self._config.package_name)
            inspected = await self.inspect_installed_apk(serial, installed)
            if (
                inspected.apk_sha256 != work.desired_apk.apk_sha256
                or inspected.signer_sha256 != work.desired_apk.signer_sha256
                or inspected.version_code != work.desired_apk.version_code
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INSTALL_FAILED,
                    "Hasil instalasi Android agent tidak sesuai artifact SIKSIK.",
                )
            work.installed_package = installed
            work.installed_apk = inspected
        except AcquisitionError as exc:
            logger.warning(
                "agent_install_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "error_category": exc.category.value,
                    "retryable": exc.retryable,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        logger.info(
            "agent_install_completed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "state": work.install_action.value,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "install_strategy": work.install_strategy,
                "retry_count": max(0, work.install_attempt_count - 1),
            },
        )


class AgentAccessCoordinator:
    def __init__(
        self,
        config: AgentBootstrapConfig,
        adb: AsyncAdbTransport,
        *,
        sleep: Sleep,
    ) -> None:
        self._config = config
        self._adb = adb
        self._sleep = sleep

    async def validate_readiness(self, serial: str) -> None:
        readiness = await self._adb.device_readiness(serial)
        if not readiness.boot_completed:
            raise acquisition_error(
                ErrorCategory.ADB_OFFLINE,
                "Perangkat Android belum selesai melakukan boot.",
                retryable=True,
            )
        if readiness.unlocked is False:
            raise acquisition_error(
                ErrorCategory.DEVICE_LOCKED,
                "Buka kunci perangkat Android untuk melanjutkan.",
                retryable=True,
            )
        if (
            readiness.available_data_bytes is not None
            and readiness.available_data_bytes < self._config.minimum_device_storage_bytes
        ):
            raise acquisition_error(
                ErrorCategory.DEVICE_STORAGE_LOW,
                "Penyimpanan perangkat tidak cukup untuk Android agent.",
            )

    async def apply_runtime_permissions(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
        capabilities: AndroidDeviceCapabilities,
        work: BootstrapWorkingState,
        publish_awaiting: Callable[[], Awaitable[None]],
    ) -> None:
        user_id = await self._adb.current_user_id(serial)
        requirements = runtime_permissions_for_api(capabilities.api_level)
        required_pending: list[RuntimePermissionRequirement] = []
        for requirement in requirements:
            state = await self._adb.grant_runtime_permission(
                serial,
                self._config.package_name,
                requirement.permission,
                user_id,
            )
            key = requirement.permission.removeprefix("android.permission.").casefold()
            work.runtime_permissions[key] = state.value
            if state == PermissionState.GRANTED:
                continue
            if requirement.required and state == PermissionState.UNSUPPORTED:
                raise acquisition_error(
                    ErrorCategory.RUNTIME_PERMISSION_UNSUPPORTED,
                    "Izin runtime wajib Android agent tidak tersedia.",
                )
            if requirement.required:
                required_pending.append(requirement)

        media_pending = [
            item
            for item in required_pending
            if item.permission in MEDIA_RUNTIME_PERMISSIONS
        ]
        if media_pending:
            await self._await_runtime_permission_group(
                session_id=session_id,
                serial=serial,
                request_id=request_id,
                work=work,
                publish_awaiting=publish_awaiting,
                pending=media_pending,
                user_id=user_id,
                launch_dialog=self._relaunch_for_media_permission_dialog,
                log_state="runtime_storage_in_app",
                granted_log_state="runtime_storage",
                timeout_message="Konfirmasi izin penyimpanan Android melewati batas waktu.",
            )

        comms_pending = [
            item
            for item in requirements
            if item.required and item.permission in COMMUNICATION_RUNTIME_PERMISSIONS
        ]
        comms_pending = [
            item
            for item in comms_pending
            if work.runtime_permissions.get(
                item.permission.removeprefix("android.permission.").casefold(),
            )
            != PermissionState.GRANTED.value
        ]
        if comms_pending:
            await self._await_runtime_permission_group(
                session_id=session_id,
                serial=serial,
                request_id=request_id,
                work=work,
                publish_awaiting=publish_awaiting,
                pending=comms_pending,
                user_id=user_id,
                launch_dialog=self._relaunch_for_communication_permission_dialog,
                log_state="runtime_communication_in_app",
                granted_log_state="runtime_communication",
                timeout_message="Konfirmasi izin SMS/kontak Android melewati batas waktu.",
            )

    async def _await_runtime_permission_group(
        self,
        *,
        session_id: str,
        serial: str,
        request_id: str | None,
        work: BootstrapWorkingState,
        publish_awaiting: Callable[[], Awaitable[None]],
        pending: list[RuntimePermissionRequirement],
        user_id: int,
        launch_dialog: Callable[[str, str, BootstrapWorkingState], Awaitable[None]],
        log_state: str,
        granted_log_state: str,
        timeout_message: str,
    ) -> None:
        await launch_dialog(serial, session_id, work)
        await publish_awaiting()
        logger.info(
            "agent_access_waiting",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "state": log_state,
            },
        )
        deadline = (
            asyncio.get_running_loop().time()
            + self._config.special_access_timeout_seconds
        )
        opened_app_info = False
        mid_deadline = (
            asyncio.get_running_loop().time()
            + (self._config.special_access_timeout_seconds * 0.55)
        )
        while asyncio.get_running_loop().time() < deadline:
            await self._sleep(self._config.special_access_poll_seconds)
            all_granted = True
            for requirement in pending:
                state = await self._adb.runtime_permission_state(
                    serial,
                    self._config.package_name,
                    requirement.permission,
                    user_id,
                )
                key = requirement.permission.removeprefix(
                    "android.permission."
                ).casefold()
                work.runtime_permissions[key] = state.value
                if state == PermissionState.UNSUPPORTED:
                    raise acquisition_error(
                        ErrorCategory.RUNTIME_PERMISSION_UNSUPPORTED,
                        "Izin runtime wajib Android agent tidak tersedia.",
                    )
                all_granted = all_granted and state == PermissionState.GRANTED
            if all_granted:
                logger.info(
                    "agent_access_granted",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "state": granted_log_state,
                    },
                )
                return
            if (
                not opened_app_info
                and asyncio.get_running_loop().time() >= mid_deadline
            ):
                opened_app_info = True
                logger.info(
                    "agent_access_fallback_app_info",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                    },
                )
                await self._adb.open_runtime_permission_settings(
                    serial,
                    self._config.package_name,
                    user_id=user_id,
                )
        raise acquisition_error(
            ErrorCategory.ACCESS_TIMEOUT,
            timeout_message,
            retryable=True,
        )

    async def _relaunch_for_media_permission_dialog(
        self,
        serial: str,
        session_id: str,
        work: BootstrapWorkingState,
    ) -> None:
        if work.token is None or work.token_expires_at is None:
            return
        try:
            await self._adb.start_activity(
                serial,
                self._config.component,
                {
                    "session_id": session_id,
                    "session_token": work.token,
                    "token_expires_at_epoch_ms": int(
                        work.token_expires_at.timestamp() * 1000
                    ),
                    "request_media_permissions": "1",
                },
            )
        except AcquisitionError as exc:
            logger.warning(
                "agent_media_permission_relaunch_failed",
                extra={
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "error_category": exc.category.value,
                },
            )

    async def _relaunch_for_communication_permission_dialog(
        self,
        serial: str,
        session_id: str,
        work: BootstrapWorkingState,
    ) -> None:
        if work.token is None or work.token_expires_at is None:
            return
        try:
            await self._adb.start_activity(
                serial,
                self._config.component,
                {
                    "session_id": session_id,
                    "session_token": work.token,
                    "token_expires_at_epoch_ms": int(
                        work.token_expires_at.timestamp() * 1000
                    ),
                    "request_communication_permissions": "1",
                },
            )
        except AcquisitionError as exc:
            logger.warning(
                "agent_comm_permission_relaunch_failed",
                extra={
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "error_category": exc.category.value,
                },
            )

    async def verify_special_access(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
        work: BootstrapWorkingState,
        publish_awaiting: Callable[..., Awaitable[None]],
        required_access: tuple[SpecialAccessKind, ...] | None = None,
        optional_access: tuple[SpecialAccessKind, ...] = (),
    ) -> None:
        required = (
            required_access
            if required_access is not None
            else self._config.required_special_access
        )
        accesses = [(access, True) for access in required]
        accesses.extend(
            (access, False) for access in optional_access if access not in required
        )
        if not accesses:
            return
        user_id = await self._adb.current_user_id(serial)
        for access, is_required in accesses:
            component = self._special_component(access)
            state = await self._adb.special_access_state(
                serial,
                self._config.package_name,
                access,
                component=component,
                user_id=user_id,
            )
            work.special_access[access.value] = state.value
            if access == SpecialAccessKind.ACCESSIBILITY and component is not None:
                ready: bool | None = None
                if callable(
                    ready_probe := getattr(
                        self._adb,
                        "accessibility_ready_for_text_only",
                        None,
                    )
                ):
                    ready = await ready_probe(serial, component)
                elif callable(
                    service_bound := getattr(
                        self._adb,
                        "accessibility_service_bound",
                        None,
                    )
                ):
                    ready = await service_bound(serial, component)
                if state == SpecialAccessState.GRANTED and ready is False:
                    binding = AccessibilityBindingState.UNBOUND
                    if callable(
                        binding_state := getattr(
                            self._adb,
                            "accessibility_binding_state",
                            None,
                        )
                    ):
                        binding = await binding_state(serial, component)
                    master_enabled = True
                    if callable(
                        master_probe := getattr(
                            self._adb,
                            "accessibility_master_enabled",
                            None,
                        )
                    ):
                        master_enabled = await master_probe(serial, user_id=user_id)
                    logger.warning(
                        "agent_accessibility_not_ready",
                        extra={
                            "request_id": request_id,
                            "session_id": session_id,
                            "device_ref": device_ref(serial),
                            "binding_state": binding.value,
                            "master_enabled": master_enabled,
                        },
                    )
                    state = SpecialAccessState.NOT_GRANTED
                    work.special_access[access.value] = state.value
            if state == SpecialAccessState.GRANTED:
                continue
            if (
                access == SpecialAccessKind.ACCESSIBILITY
                and component is not None
                and state == SpecialAccessState.NOT_GRANTED
                and callable(
                    restore_accessibility := getattr(
                        self._adb,
                        "restore_accessibility_service",
                        None,
                    )
                )
            ):
                try:
                    state = await restore_accessibility(
                        serial,
                        self._config.package_name,
                        component,
                        user_id=user_id,
                    )
                except AcquisitionError as exc:
                    logger.warning(
                        "agent_accessibility_restore_failed",
                        extra={
                            "request_id": request_id,
                            "session_id": session_id,
                            "device_ref": device_ref(serial),
                            "error_category": exc.category.value,
                        },
                    )
                work.special_access[access.value] = state.value
                if state == SpecialAccessState.GRANTED:
                    ready: bool | None = None
                    if callable(
                        ready_probe := getattr(
                            self._adb,
                            "accessibility_ready_for_text_only",
                            None,
                        )
                    ):
                        ready = await ready_probe(serial, component)
                    elif callable(
                        service_bound := getattr(
                            self._adb,
                            "accessibility_service_bound",
                            None,
                        )
                    ):
                        ready = await service_bound(serial, component)
                    if ready is not False:
                        continue
                    state = SpecialAccessState.NOT_GRANTED
                    work.special_access[access.value] = state.value
                if state == SpecialAccessState.GRANTED:
                    continue
                # Restore can return UNAVAILABLE (foreign a11y entries) or DENIED
                # (MIUI blocks settings put secure). Neither is operator denial.
                if (
                    state in {SpecialAccessState.UNAVAILABLE, SpecialAccessState.DENIED}
                    and component is not None
                ):
                    logger.info(
                        "agent_accessibility_restore_deferred",
                        extra={
                            "request_id": request_id,
                            "session_id": session_id,
                            "device_ref": device_ref(serial),
                            "access_state": state.value,
                        },
                    )
                    state = SpecialAccessState.NOT_GRANTED
                    work.special_access[access.value] = state.value
            if access == SpecialAccessKind.NOTIFICATION_LISTENER:
                state = await self._adb.grant_notification_listener(
                    serial,
                    self._config.package_name,
                    require_not_none(component),
                    user_id=user_id,
                )
                state = await self._settle_notification_listener(
                    serial,
                    require_not_none(component),
                    user_id,
                    state,
                )
                work.special_access[access.value] = state.value
                if state == SpecialAccessState.GRANTED:
                    logger.info(
                        "agent_access_granted",
                        extra={
                            "request_id": request_id,
                            "session_id": session_id,
                            "device_ref": device_ref(serial),
                            "state": access.value,
                            "operation": "adb_notification_command",
                        },
                    )
                    continue
                logger.info(
                    "agent_notification_adb_grant_unavailable",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "state": access.value,
                        "access_state": state.value,
                    },
                )
            if state == SpecialAccessState.UNAVAILABLE:
                if is_required:
                    raise acquisition_error(
                        ErrorCategory.DEVICE_UNSUPPORTED,
                        "Special access wajib belum didukung Android agent.",
                    )
                continue
            if state == SpecialAccessState.DENIED:
                if is_required:
                    raise acquisition_error(
                        ErrorCategory.ACCESS_DENIED,
                        "Special access Android ditolak.",
                    )
                continue
            await self._adb.open_special_access_settings(
                serial,
                self._config.package_name,
                access,
                user_id=user_id,
                component=component,
            )
            await publish_awaiting(
                message=SPECIAL_ACCESS_WAIT_MESSAGES.get(access),
            )
            logger.info(
                "agent_access_waiting",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "state": access.value,
                },
            )
            deadline = (
                asyncio.get_running_loop().time()
                + self._config.special_access_timeout_seconds
            )
            while asyncio.get_running_loop().time() < deadline:
                await self._sleep(self._config.special_access_poll_seconds)
                state = await self._adb.special_access_state(
                    serial,
                    self._config.package_name,
                    access,
                    component=component,
                    user_id=user_id,
                )
                work.special_access[access.value] = state.value
                if state == SpecialAccessState.GRANTED:
                    logger.info(
                        "agent_access_granted",
                        extra={
                            "request_id": request_id,
                            "session_id": session_id,
                            "device_ref": device_ref(serial),
                            "state": access.value,
                        },
                    )
                    break
                if state == SpecialAccessState.DENIED:
                    if is_required:
                        raise acquisition_error(
                            ErrorCategory.ACCESS_DENIED,
                            "Special access Android ditolak.",
                        )
                    break
            else:
                if is_required:
                    raise acquisition_error(
                        ErrorCategory.ACCESS_TIMEOUT,
                        "Konfirmasi special access Android melewati batas waktu.",
                        retryable=True,
                    )
                logger.info(
                    "agent_optional_access_unavailable",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "state": access.value,
                    },
                )

    async def _settle_notification_listener(
        self,
        serial: str,
        component: str,
        user_id: int,
        state: SpecialAccessState,
    ) -> SpecialAccessState:
        delay = min(max(self._config.special_access_poll_seconds, 0.25), 1.0)
        for _ in range(NOTIFICATION_GRANT_SETTLE_ATTEMPTS):
            if state != SpecialAccessState.NOT_GRANTED:
                return state
            await self._sleep(delay)
            state = await self._adb.special_access_state(
                serial,
                self._config.package_name,
                SpecialAccessKind.NOTIFICATION_LISTENER,
                component=component,
                user_id=user_id,
            )
        return state

    def _special_component(self, access: SpecialAccessKind) -> str | None:
        if access == SpecialAccessKind.ACCESSIBILITY:
            return self._config.accessibility_component
        if access == SpecialAccessKind.NOTIFICATION_LISTENER:
            return self._config.notification_component
        return None


def require_not_none(value: str | None) -> str:
    if value is None:
        raise acquisition_error(
            ErrorCategory.INTERNAL_ERROR,
            "Komponen special access Android belum dikonfigurasi.",
        )
    return value


class AgentHandshakeCoordinator:
    def __init__(
        self,
        config: AgentBootstrapConfig,
        client_factory: AgentClientFactory,
        on_identity_mismatch: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._on_identity_mismatch = on_identity_mismatch

    async def negotiate(
        self,
        session_id: str,
        device: AndroidDeviceCapabilities,
        request_id: str | None,
        work: BootstrapWorkingState,
    ) -> None:
        if (
            work.forward_host_port is None
            or work.token is None
            or work.artifact is None
            or work.desired_apk is None
        ):
            raise acquisition_error(ErrorCategory.INTERNAL_ERROR, "Runtime agent belum lengkap.")
        client = self._client_factory(work.forward_host_port, work.token)
        health_run = None
        last_exc = None
        for attempt in range(20):
            try:
                health_run = (await client.health(request_id=request_id)).body
                break
            except AcquisitionError as exc:
                if exc.category == ErrorCategory.AGENT_UNREACHABLE:
                    last_exc = exc
                    await asyncio.sleep(0.5)
                    continue
                raise
        if health_run is None:
            if last_exc is not None:
                raise last_exc
            raise acquisition_error(
                ErrorCategory.AGENT_UNREACHABLE,
                "Android agent tidak dapat dihubungi.",
            )
        health = health_run
        if (
            health.session_id != session_id
            or health.api_version != self._config.api_version
            or health.api_port != self._config.device_port
            or health.agent_build_sha256 != work.artifact.input_sha256
        ):
            if self._on_identity_mismatch is not None:
                self._on_identity_mismatch()
            logger.warning(
                "agent_identity_mismatch",
                extra={
                    "expected_sha256": work.artifact.input_sha256[:12],
                    "reported_sha256": (health.agent_build_sha256 or "")[:12],
                },
            )
            raise acquisition_error(
                ErrorCategory.AGENT_API_MISMATCH,
                "Identitas runtime Android agent tidak sesuai artifact SIKSIK.",
                retryable=True,
            )
        capabilities = (await client.capabilities(request_id=request_id)).body
        if (
            capabilities.active_session_id != session_id
            or capabilities.api_version != self._config.api_version
            or capabilities.api_port != self._config.device_port
            or capabilities.package_name != self._config.package_name
            or capabilities.agent_build_sha256 != work.artifact.input_sha256
            or capabilities.android_api_level != device.api_level
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_API_MISMATCH,
                "Capability Android agent tidak sesuai sesi aktif.",
            )
        active_session = (
            await client.bootstrap(session_id, self._config.api_version, request_id=request_id)
        ).body
        if active_session.session_id != session_id or active_session.state != "active":
            raise acquisition_error(
                ErrorCategory.AGENT_SESSION_MISMATCH,
                "Handshake Android agent tidak mengaktifkan sesi SIKSIK.",
            )
        work.capabilities = capabilities
