from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.acquisition.adb import AsyncAdbTransport, SpecialAccessKind
from app.acquisition.agent_artifact import (
    AgentArtifactConfig,
    AgentArtifactService,
)
from app.acquisition.agent_client import AgentClient, AgentClientConfig
from app.acquisition.automation import AndroidUiAutomationOrchestrator, AutomationConfig
from app.acquisition.automation_package import (
    AutomationPackageConfig,
    AutomationPackageCoordinator,
)
from app.acquisition.apk_metadata import ApkMetadataConfig, ApkMetadataInspector
from app.acquisition.bootstrap_components import (
    AgentAccessCoordinator,
    AgentHandshakeCoordinator,
    AgentPackageCoordinator,
)
from app.acquisition.bootstrap_contracts import (
    STATE_MESSAGES,
    STATE_PERCENT,
    AgentBootstrapConfig,
    AgentClientFactory,
    ArtifactBuilder,
    BootstrapWorkingState,
    InstallAction,
    MetadataInspector,
    Progress,
    Sleep,
)
from app.acquisition.bootstrap_runner import Phase7AndroidAgentRunner
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.runtime import (
    AgentRuntimeRecord,
    AgentRuntimeRegistry,
    AgentRuntimeRepository,
    AgentRuntimeSecrets,
    AgentRuntimeState,
    agent_runtime_registry,
    agent_runtime_repository,
    device_ref,
)
from app.core.config import settings
from app.models.schemas import AgentBootstrapState, SessionStatus

logger = logging.getLogger("siksik.acquisition.bootstrap")
TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 300


class AndroidAgentBootstrapService:
    def __init__(
        self,
        config: AgentBootstrapConfig,
        adb: AsyncAdbTransport,
        artifacts: ArtifactBuilder,
        inspector: MetadataInspector,
        client_factory: AgentClientFactory,
        *,
        automation_packages: AutomationPackageCoordinator | None = None,
        repository: AgentRuntimeRepository = agent_runtime_repository,
        registry: AgentRuntimeRegistry = agent_runtime_registry,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    ) -> None:
        if (
            not 1 <= config.device_port <= 65535
            or config.minimum_api < 26
            or not 60 <= config.token_ttl_seconds <= 3600
            or config.minimum_device_storage_bytes <= 0
            or config.special_access_timeout_seconds <= 0
            or config.special_access_poll_seconds <= 0
        ):
            raise ValueError("Android bootstrap configuration is invalid")
        self._config = config
        self._adb = adb
        self._artifacts = artifacts
        self._inspector = inspector
        self._client_factory = client_factory
        self._repository = repository
        self._registry = registry
        self._sleep = sleep
        self._clock = clock
        self._token_factory = token_factory
        self._packages = AgentPackageCoordinator(config, adb, inspector)
        self._automation_packages = automation_packages
        self._access = AgentAccessCoordinator(config, adb, sleep=sleep)
        self._handshake = AgentHandshakeCoordinator(
            config,
            client_factory,
            on_identity_mismatch=self._invalidate_artifact_cache,
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._expiry_tasks: dict[str, asyncio.Task[None]] = {}

    def _invalidate_artifact_cache(self) -> None:
        if hasattr(self._artifacts, "invalidate_cache"):
            self._artifacts.invalidate_cache()

    @property
    def config(self) -> AgentBootstrapConfig:
        return self._config

    @property
    def inspector(self) -> MetadataInspector:
        return self._inspector

    @property
    def repository(self) -> AgentRuntimeRepository:
        return self._repository

    @staticmethod
    def public_status(record: AgentRuntimeRecord) -> dict[str, object]:
        state_map = {
            AgentRuntimeState.PREPARING: AgentBootstrapState.DETECT_DEVICE,
            AgentRuntimeState.ACTIVE: AgentBootstrapState.READY,
            AgentRuntimeState.DEGRADED: AgentBootstrapState.DEGRADED,
            AgentRuntimeState.CLOSED: AgentBootstrapState.CLOSED,
        }
        try:
            state = AgentBootstrapState(record.state.value)
        except ValueError:
            state = state_map.get(record.state, AgentBootstrapState.FAILED)
        permissions = record.details.get("runtime_permissions", {})
        special = record.details.get("special_access", {})
        capabilities = record.details.get("capabilities")
        return {
            "schema_version": 1,
            "session_id": record.session_id,
            "device_ref": record.device_ref,
            "state": state,
            "ready": state == AgentBootstrapState.READY,
            "api_version": record.api_version,
            "agent_version": record.agent_version,
            "agent_build_sha256": record.agent_build_sha256,
            "artifact_sha256": record.artifact_sha256,
            "install_action": record.details.get("install_action"),
            "runtime_permissions": permissions if isinstance(permissions, dict) else {},
            "special_access": special if isinstance(special, dict) else {},
            "capabilities": capabilities if isinstance(capabilities, dict) else None,
            "retryable": record.retryable,
            "error_category": record.error_category,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    async def bootstrap(
        self,
        *,
        session_id: str,
        serial: str,
        request_id: str | None,
        on_progress: Progress,
        required_special_access: tuple[SpecialAccessKind, ...] | None = None,
        optional_special_access: tuple[SpecialAccessKind, ...] = (),
    ) -> AgentRuntimeRecord:
        lock = await self._session_lock(session_id)
        async with lock:
            required_accesses = (
                required_special_access
                if required_special_access is not None
                else self._config.required_special_access
            )
            active = await self._active_runtime(session_id, request_id)
            if active is not None:
                current_access = active.details.get("special_access", {})
                required_ready = isinstance(current_access, dict) and all(
                    current_access.get(access.value) == "granted"
                    for access in required_accesses
                )
                optional_checked = isinstance(current_access, dict) and all(
                    access.value in current_access for access in optional_special_access
                )
                if required_ready and optional_checked:
                    return active
                await self.teardown(session_id, request_id)
            work = BootstrapWorkingState()
            started = time.monotonic()
            try:
                await self._clear_previous_runtime(session_id, serial, request_id)
                await self._teardown_stale_device_runtimes(session_id, serial, request_id)
                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.DETECT_DEVICE,
                    request_id,
                    on_progress,
                    work,
                )
                await self._adb.select_device(serial)

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.VALIDATE_DEVICE,
                    request_id,
                    on_progress,
                    work,
                )
                capabilities = await self._adb.capabilities(
                    serial,
                    package_name=self._config.package_name,
                    minimum_api=self._config.minimum_api,
                )
                work.device_manufacturer = capabilities.manufacturer
                work.device_model = capabilities.model
                work.device_api_level = capabilities.api_level
                await self._access.validate_readiness(serial)

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.RESOLVE_OR_BUILD_AGENT,
                    request_id,
                    on_progress,
                    work,
                )
                work.artifact = await self._artifacts.build_debug_apk(request_id)
                work.desired_apk = await self._inspector.inspect(work.artifact.path)
                self._packages.validate_desired_apk(work)

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.INSPECT_INSTALLED_PACKAGE,
                    request_id,
                    on_progress,
                    work,
                )
                work.installed_package = await self._adb.inspect_package(
                    serial,
                    self._config.package_name,
                )
                if work.installed_package.installed:
                    work.installed_apk = await self._packages.inspect_installed_apk(
                        serial,
                        work.installed_package,
                    )
                work.install_action = self._packages.install_action(work)

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.INSTALL_OR_UPDATE,
                    request_id,
                    on_progress,
                    work,
                )

                async def publish_install_awaiting() -> None:
                    await self._publish(
                        session_id,
                        serial,
                        AgentRuntimeState.AWAITING_INSTALL_APPROVAL,
                        request_id,
                        on_progress,
                        work,
                    )

                await self._packages.install_if_needed(
                    session_id,
                    serial,
                    request_id,
                    work,
                    on_user_restricted=publish_install_awaiting,
                )

                if self._automation_packages is not None:
                    await self._publish(
                        session_id,
                        serial,
                        AgentRuntimeState.INSTALL_AUTOMATION,
                        request_id,
                        on_progress,
                        work,
                    )

                    async def publish_automation_install_awaiting() -> None:
                        await self._publish(
                            session_id,
                            serial,
                            AgentRuntimeState.AWAITING_INSTALL_APPROVAL,
                            request_id,
                            on_progress,
                            work,
                        )

                    work.automation_install_action = (
                        await self._automation_packages.ensure_installed(
                            session_id=session_id,
                            serial=serial,
                            request_id=request_id,
                            on_user_restricted=publish_automation_install_awaiting,
                        )
                    )

                now = self._clock()
                work.token = self._token_factory()
                effective_token_ttl = max(
                    30,
                    self._config.token_ttl_seconds - TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS,
                )
                work.token_expires_at = now + timedelta(seconds=effective_token_ttl)
                if not 32 <= len(work.token) <= 512 or any(
                    ord(ch) < 32 or ord(ch) == 127 for ch in work.token
                ):
                    raise acquisition_error(
                        ErrorCategory.INTERNAL_ERROR,
                        "Generator token Android agent menghasilkan nilai tidak valid.",
                    )

                # Start agent BEFORE runtime-permission wait so:
                # 1) BootstrapActivity can show the system permission dialog
                # 2) handshake works (avoids "Android agent tidak dapat dihubungi"
                #    while stuck on App info with no AgentService)
                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.START_AGENT,
                    request_id,
                    on_progress,
                    work,
                )
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

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.CREATE_FORWARD,
                    request_id,
                    on_progress,
                    work,
                )
                work.forward_host_port = await self._adb.create_forward(
                    serial,
                    self._config.device_port,
                )

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.AUTHENTICATE_AND_NEGOTIATE,
                    request_id,
                    on_progress,
                    work,
                )
                await self._handshake.negotiate(session_id, capabilities, request_id, work)

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.APPLY_RUNTIME_PERMISSIONS,
                    request_id,
                    on_progress,
                    work,
                )

                async def publish_runtime_awaiting() -> None:
                    await self._publish(
                        session_id,
                        serial,
                        AgentRuntimeState.AWAITING_RUNTIME_PERMISSION,
                        request_id,
                        on_progress,
                        work,
                    )

                await self._access.apply_runtime_permissions(
                    session_id,
                    serial,
                    request_id,
                    capabilities,
                    work,
                    publish_runtime_awaiting,
                )

                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.VERIFY_SPECIAL_ACCESS,
                    request_id,
                    on_progress,
                    work,
                )

                async def publish_awaiting(message: str | None = None) -> None:
                    await self._publish(
                        session_id,
                        serial,
                        AgentRuntimeState.AWAITING_ACCESS,
                        request_id,
                        on_progress,
                        work,
                        message=message,
                    )

                await self._access.verify_special_access(
                    session_id,
                    serial,
                    request_id,
                    work,
                    publish_awaiting,
                    required_accesses,
                    optional_special_access,
                )

                google_token: str | None = None
                google_account: str | None = None
                if settings.gmail_acquisition_enabled:
                    try:
                        from app.acquisition.gmail_oauth import (
                            peek_gmail_oauth_token,
                            resolve_google_account_name,
                        )

                        client = self._client_factory(work.forward_host_port, work.token)
                        google_account = await resolve_google_account_name(
                            client,
                            session_id,
                            serial=serial,
                            adb=self._adb,
                            request_id=request_id,
                        )
                        if google_account:
                            google_token = await peek_gmail_oauth_token(
                                client,
                                session_id,
                                google_account,
                                request_id=request_id,
                            )
                    except Exception as exc:
                        logger.warning("gmail_bootstrap_auth_failed", extra={"error": str(exc)})

                runtime = AgentRuntimeSecrets(
                    session_id=session_id,
                    serial=serial,
                    token=work.token,
                    forward_host_port=work.forward_host_port,
                    token_expires_at=work.token_expires_at.isoformat(),
                    google_token=google_token,
                    google_account=google_account,
                )
                await self._registry.bind(runtime)
                record = await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.READY,
                    request_id,
                    on_progress,
                    work,
                )
                self._schedule_expiry(runtime)
                logger.info(
                    "agent_handshake_completed",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "agent_version": record.agent_version,
                        "api_version": record.api_version,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    },
                )
                return record
            except asyncio.CancelledError:
                await self._cleanup_failed_runtime(session_id, serial, work)
                await self._publish(
                    session_id,
                    serial,
                    AgentRuntimeState.CANCELLED,
                    request_id,
                    on_progress,
                    work,
                )
                raise
            except AcquisitionError as exc:
                await self._cleanup_failed_runtime(session_id, serial, work)
                await self._publish_failure(
                    session_id,
                    serial,
                    request_id,
                    on_progress,
                    work,
                    exc,
                )
                raise
            except Exception as exc:
                await self._cleanup_failed_runtime(session_id, serial, work)
                wrapped = acquisition_error(
                    ErrorCategory.INTERNAL_ERROR,
                    "Persiapan Android agent gagal.",
                )
                await self._publish_failure(
                    session_id,
                    serial,
                    request_id,
                    on_progress,
                    work,
                    wrapped,
                )
                logger.exception(
                    "agent_bootstrap_failed",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "error_category": ErrorCategory.INTERNAL_ERROR.value,
                        "retryable": False,
                    },
                )
                raise wrapped from exc

    async def status_for_device(self, serial: str, request_id: str | None) -> AgentRuntimeRecord:
        record = await self._repository.latest_for_device(serial)
        if record.state != AgentRuntimeState.READY:
            return record
        try:
            runtime = await self._registry.get(record.session_id)
            await self._client_factory(runtime.forward_host_port, runtime.token).health(
                request_id=request_id,
            )
            return record
        except AcquisitionError:
            runtime = await self._registry.remove(record.session_id)
            if runtime is not None:
                await self._remove_forward_best_effort(
                    runtime.serial,
                    runtime.forward_host_port,
                    record.session_id,
                    request_id,
                )
            return await self._repository.upsert(
                session_id=record.session_id,
                serial=serial,
                state=AgentRuntimeState.DEGRADED,
                api_version=record.api_version,
                agent_version=record.agent_version,
                agent_build_sha256=record.agent_build_sha256,
                artifact_sha256=record.artifact_sha256,
                request_id=request_id,
                error_category=ErrorCategory.AGENT_UNREACHABLE.value,
                retryable=True,
                details=record.details,
            )

    async def teardown(self, session_id: str, request_id: str | None = None) -> None:
        expiry = self._expiry_tasks.pop(session_id, None)
        current = asyncio.current_task()
        if expiry is not None and expiry is not current:
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        runtime = await self._registry.remove(session_id)
        if runtime is None:
            return
        client = self._client_factory(runtime.forward_host_port, runtime.token)
        try:
            await client.stop(session_id, request_id=request_id)
        except AcquisitionError as exc:
            logger.warning(
                "agent_stop_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(runtime.serial),
                    "error_category": exc.category.value,
                    "retryable": exc.retryable,
                },
            )
        await self._remove_forward_best_effort(
            runtime.serial,
            runtime.forward_host_port,
            session_id,
            request_id,
        )
        record = await self._repository.try_get(session_id)
        if record is not None:
            await self._repository.close(session_id)

    async def shutdown(self) -> None:
        tasks = list(self._expiry_tasks.values())
        self._expiry_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        runtimes = await self._registry.pop_all()
        for runtime in runtimes:
            try:
                await self._client_factory(runtime.forward_host_port, runtime.token).stop(
                    runtime.session_id,
                )
            except AcquisitionError as exc:
                logger.warning(
                    "agent_stop_failed",
                    extra={
                        "session_id": runtime.session_id,
                        "device_ref": device_ref(runtime.serial),
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    },
                )
            await self._remove_forward_best_effort(
                runtime.serial,
                runtime.forward_host_port,
                runtime.session_id,
                None,
            )
            record = await self._repository.try_get(runtime.session_id)
            if record is not None:
                await self._repository.close(runtime.session_id)

    async def _active_runtime(
        self,
        session_id: str,
        request_id: str | None,
    ) -> AgentRuntimeRecord | None:
        try:
            runtime = await self._registry.get(session_id)
            await self._client_factory(runtime.forward_host_port, runtime.token).health(
                request_id=request_id,
            )
            record = await self._repository.get(session_id)
            if record.state == AgentRuntimeState.READY:
                return record
        except AcquisitionError as exc:
            if exc.category not in {
                ErrorCategory.NOT_FOUND,
                ErrorCategory.AGENT_UNREACHABLE,
                ErrorCategory.AGENT_AUTH_INVALID,
                ErrorCategory.AGENT_SESSION_MISMATCH,
            }:
                raise
            return None
        return None

    async def _clear_previous_runtime(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
    ) -> None:
        existing = await self._registry.remove(session_id)
        if existing is not None:
            try:
                await self._client_factory(existing.forward_host_port, existing.token).stop(
                    session_id,
                    request_id=request_id,
                )
            except AcquisitionError as exc:
                logger.warning(
                    "agent_stop_failed",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "device_ref": device_ref(existing.serial),
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    },
                )
            await self._remove_forward_best_effort(
                existing.serial,
                existing.forward_host_port,
                session_id,
                request_id,
            )
        persisted = await self._repository.try_get(session_id)
        if persisted is not None and persisted.forward_host_port is not None:
            await self._remove_forward_best_effort(
                serial,
                persisted.forward_host_port,
                session_id,
                request_id,
            )

    async def _teardown_stale_device_runtimes(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
    ) -> None:
        stale = await self._registry.remove_for_serial(serial, except_session_id=session_id)
        for runtime in stale:
            try:
                await self._client_factory(runtime.forward_host_port, runtime.token).stop(
                    runtime.session_id,
                    request_id=request_id,
                )
            except AcquisitionError as exc:
                logger.warning(
                    "agent_stop_failed",
                    extra={
                        "request_id": request_id,
                        "session_id": runtime.session_id,
                        "device_ref": device_ref(runtime.serial),
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    },
                )
            await self._remove_forward_best_effort(
                runtime.serial,
                runtime.forward_host_port,
                runtime.session_id,
                request_id,
            )

    async def _cleanup_failed_runtime(
        self,
        session_id: str,
        serial: str,
        work: BootstrapWorkingState,
    ) -> None:
        await self._registry.remove(session_id)
        if work.forward_host_port is not None and work.token is not None:
            try:
                await self._client_factory(work.forward_host_port, work.token).stop(session_id)
            except AcquisitionError as exc:
                logger.warning(
                    "agent_stop_failed",
                    extra={
                        "session_id": session_id,
                        "device_ref": device_ref(serial),
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    },
                )
        if work.forward_host_port is not None:
            await self._remove_forward_best_effort(
                serial,
                work.forward_host_port,
                session_id,
                None,
            )
            work.forward_host_port = None

    async def _remove_forward_best_effort(
        self,
        serial: str,
        host_port: int,
        session_id: str,
        request_id: str | None,
    ) -> None:
        try:
            await self._adb.remove_forward(serial, host_port)
        except AcquisitionError as exc:
            logger.warning(
                "agent_forward_remove_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "error_category": exc.category.value,
                    "retryable": exc.retryable,
                },
            )

    async def _publish_failure(
        self,
        session_id: str,
        serial: str,
        request_id: str | None,
        on_progress: Progress,
        work: BootstrapWorkingState,
        error: AcquisitionError,
    ) -> None:
        await self._publish(
            session_id,
            serial,
            AgentRuntimeState.FAILED,
            request_id,
            on_progress,
            work,
            error=error,
        )
        logger.warning(
            "agent_bootstrap_failed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "error_category": error.category.value,
                "retryable": error.retryable,
            },
        )

    async def _publish(
        self,
        session_id: str,
        serial: str,
        state: AgentRuntimeState,
        request_id: str | None,
        on_progress: Progress,
        work: BootstrapWorkingState,
        *,
        error: AcquisitionError | None = None,
        message: str | None = None,
    ) -> AgentRuntimeRecord:
        details = work.safe_details()
        record = await self._repository.upsert(
            session_id=session_id,
            serial=serial,
            state=state,
            api_version=work.capabilities.api_version if work.capabilities else None,
            agent_version=work.capabilities.agent_version if work.capabilities else None,
            agent_build_sha256=work.capabilities.agent_build_sha256
            if work.capabilities
            else None,
            artifact_sha256=work.artifact.apk_sha256 if work.artifact else None,
            forward_host_port=work.forward_host_port,
            token=work.token if state == AgentRuntimeState.READY else None,
            token_expires_at=work.token_expires_at.isoformat()
            if state == AgentRuntimeState.READY and work.token_expires_at
            else None,
            request_id=request_id,
            error_category=error.category.value if error else None,
            retryable=error.retryable if error else False,
            details=details,
        )
        percent = STATE_PERCENT[state]
        await self._repository.add_event(
            session_id=session_id,
            serial=serial,
            state=state,
            percent=percent,
            message_code=state.value,
            details=details,
            request_id=request_id,
        )
        phase = (
            SessionStatus.AWAITING_ACCESS
            if state
            in {
                AgentRuntimeState.AWAITING_RUNTIME_PERMISSION,
                AgentRuntimeState.AWAITING_ACCESS,
                AgentRuntimeState.AWAITING_INSTALL_APPROVAL,
            }
            else SessionStatus.CANCELLED
            if state == AgentRuntimeState.CANCELLED
            else SessionStatus.PREPARING_AGENT
        )
        await on_progress(
            phase,
            percent,
            message or STATE_MESSAGES[state],
            bootstrap_state=state.value,
            agent_state=state.value,
            agent_version=record.agent_version,
            agent_api_version=record.api_version,
            agent_install_action=details.get("install_action"),
            agent_retryable=record.retryable,
            agent_error_category=record.error_category,
            runtime_permissions=dict(work.runtime_permissions),
            special_access=dict(work.special_access),
        )
        logger.info(
            "agent_bootstrap_progress",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "phase": state.value,
                "state": state.value,
            },
        )
        return record

    async def _session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(session_id, asyncio.Lock())

    def _schedule_expiry(self, runtime: AgentRuntimeSecrets) -> None:
        previous = self._expiry_tasks.pop(runtime.session_id, None)
        if previous is not None:
            previous.cancel()
        expires_at = datetime.fromisoformat(runtime.token_expires_at)
        delay = max(0.0, (expires_at - self._clock()).total_seconds())

        async def expire() -> None:
            await self._sleep(delay)
            await self.teardown(runtime.session_id)

        self._expiry_tasks[runtime.session_id] = asyncio.create_task(expire())


def create_default_bootstrap_service() -> tuple[
    AndroidAgentBootstrapService,
    AutomationPackageCoordinator,
]:
    special_access: list[SpecialAccessKind] = []
    for value in settings.android_agent_required_special_access:
        try:
            special_access.append(SpecialAccessKind(value))
        except ValueError as exc:
            raise ValueError(f"Unsupported required special access: {value}") from exc
    adb = AsyncAdbTransport(
        settings.adb_path,
        timeout_seconds=settings.adb_command_timeout_s,
    )
    artifacts = AgentArtifactService(
        AgentArtifactConfig(
            project_path=settings.android_agent_project_path,
            apk_path=settings.android_agent_apk_path,
            build_timeout_seconds=settings.android_agent_build_timeout_s,
            java_home=settings.android_java_home,
            android_home=settings.android_sdk_home,
            required_output_paths=(settings.android_agent_automation_apk_path,),
        )
    )
    inspector = ApkMetadataInspector(
        ApkMetadataConfig(
            android_home=settings.android_sdk_home,
            java_home=settings.android_java_home,
            timeout_seconds=settings.android_agent_inspection_timeout_s,
        )
    )
    client_config = AgentClientConfig(
        timeout_seconds=settings.android_agent_request_timeout_s,
        max_attempts=settings.android_agent_request_attempts,
        max_response_bytes=settings.android_agent_max_response_mb * 1024 * 1024,
    )
    automation_packages = AutomationPackageCoordinator(
        AutomationPackageConfig(
            package_name=settings.android_agent_automation_package,
            apk_path=settings.android_agent_automation_apk_path,
            install_timeout_seconds=settings.android_agent_automation_install_timeout_s,
            inspection_root=settings.data_dir / "agent-inspection",
            force_reinstall=settings.android_agent_force_reinstall,
        ),
        adb,
        inspector,
    )
    service = AndroidAgentBootstrapService(
        AgentBootstrapConfig(
            package_name=settings.android_agent_package,
            component=settings.android_agent_component,
            api_version=settings.android_agent_api_version,
            device_port=settings.android_agent_device_port,
            minimum_api=settings.android_min_api,
            token_ttl_seconds=settings.android_agent_token_ttl_s,
            install_timeout_seconds=settings.android_agent_install_timeout_s,
            minimum_device_storage_bytes=settings.android_agent_min_device_storage_mb
            * 1024
            * 1024,
            special_access_timeout_seconds=settings.android_agent_access_timeout_s,
            special_access_poll_seconds=settings.android_agent_access_poll_s,
            required_special_access=tuple(special_access),
            accessibility_component=settings.android_agent_accessibility_component,
            notification_component=settings.android_agent_notification_component,
            inspection_root=settings.data_dir / "agent-inspection",
            force_reinstall=settings.android_agent_force_reinstall,
        ),
        adb,
        artifacts,
        inspector,
        lambda port, token: AgentClient(port, token, config=client_config),
        automation_packages=automation_packages,
    )
    return service, automation_packages


agent_bootstrap, automation_packages = create_default_bootstrap_service()
automation_adb = AsyncAdbTransport(
    settings.adb_path,
    timeout_seconds=settings.adb_command_timeout_s,
)
automation_artifacts = AgentArtifactService(
    AgentArtifactConfig(
        project_path=settings.android_agent_project_path,
        apk_path=settings.android_agent_apk_path,
        build_timeout_seconds=settings.android_agent_build_timeout_s,
        java_home=settings.android_java_home,
        android_home=settings.android_sdk_home,
        required_output_paths=(settings.android_agent_automation_apk_path,),
    )
)
android_ui_automation = AndroidUiAutomationOrchestrator(
    AutomationConfig(
        apk_path=settings.android_agent_automation_apk_path,
        package_name=settings.android_agent_automation_package,
        runner_component=settings.android_agent_automation_runner,
        test_class=settings.android_agent_automation_test_class,
        agent_component=settings.android_agent_component,
        agent_package_name=settings.android_agent_package,
        accessibility_component=settings.android_agent_accessibility_component,
        install_timeout_seconds=settings.android_agent_automation_install_timeout_s,
        target_timeout_seconds=settings.android_agent_automation_target_timeout_s,
        quick_scrolls=settings.android_agent_social_quick_scrolls,
        full_scrolls=settings.android_agent_social_full_scrolls,
        quick_screenshots=settings.android_agent_social_quick_screenshots,
        full_screenshots=settings.android_agent_social_full_screenshots,
        debug_snapshots=settings.android_social_debug_snapshots,
        debug_dir=settings.android_social_debug_dir,
    ),
    automation_adb,
    automation_artifacts,
    package_coordinator=automation_packages,
)
android_agent_runner = Phase7AndroidAgentRunner(
    agent_bootstrap,
    runtime_registry=agent_runtime_registry,
    client_factory=lambda port, token: AgentClient(
        port,
        token,
        config=AgentClientConfig(
            timeout_seconds=settings.android_agent_request_timeout_s,
            max_attempts=settings.android_agent_request_attempts,
            max_response_bytes=settings.android_agent_max_response_mb * 1024 * 1024,
        ),
    ),
    automation=android_ui_automation,
    target_packages=tuple(settings.android_agent_social_targets),
    connection_repair=lambda serial, host_port: automation_adb.restore_forward(
        serial,
        host_port,
        settings.android_agent_device_port,
        timeout=settings.android_agent_reconnect_timeout_s,
        poll_interval=settings.android_agent_reconnect_poll_s,
    ),
)
