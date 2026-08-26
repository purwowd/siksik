from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.acquisition.adb import (
    AccessibilityBindingState,
    SpecialAccessKind,
    SpecialAccessState,
)
from app.acquisition.agent_client import AutomationResultV1
from app.acquisition.bootstrap_contracts import InstallAction
from app.acquisition.automation_package import AutomationPackageCoordinator
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import ProcessResult
from app.acquisition.time_scope import MIN_TIME_SCOPE_EPOCH_MS, build_time_scope
from app.core.config import settings
from app.models.schemas import AcquisitionMode

logger = logging.getLogger("siksik.acquisition.automation")
AutomationProgressCallback = Callable[[str, str], Awaitable[None]]
AutomationResultCallback = Callable[[AutomationResultV1], Awaitable[None]]
SYSTEM_PROGRESS_TARGET = "__system__"
TEXT_ONLY_SOCIAL_TARGETS = frozenset({
    "com.twitter.android",
    "com.facebook.katana",
})
TEXT_ONLY_COVER_PREFLIGHT_ATTEMPTS = 5
TEXT_ONLY_COVER_PREFLIGHT_SETTLE_SECONDS = 0.4
TEXT_ONLY_ACCESSIBILITY_SETTLE_SECONDS = 8.0
TEXT_ONLY_ACCESSIBILITY_POLL_SECONDS = 0.3


def allowed_social_targets() -> frozenset[str]:
    return frozenset(settings.android_agent_social_targets)
RESULT_PREFIXES = (
    "INSTRUMENTATION_STATUS: siksik_result=",
    "INSTRUMENTATION_RESULT: siksik_result=",
)
RESULT_PREFIX = RESULT_PREFIXES[0]
SCOPE_PROGRESS_PREFIXES = (
    "INSTRUMENTATION_STATUS: siksik_scope_progress=",
    "INSTRUMENTATION_RESULT: siksik_scope_progress=",
)
TARGET_REQUIRED_SCOPES = {
    "com.instagram.android": frozenset(
        {"own_profile", "own_posts", "own_story_archive", "own_comments"}
    ),
    "com.twitter.android": frozenset({"own_profile", "own_tweets", "own_replies"}),
    "com.facebook.katana": frozenset({"own_profile", "own_posts", "own_comments"}),
}
SCOPE_PROGRESS_STATES = frozenset(
    {"running", "retrying", "complete", "failed", "cancelled"}
)
SCOPE_FAILURE_CLASSES = frozenset(
    {"observation", "action", "postcondition", "empty_content"}
)
SAFE_SCOPE_TOKEN = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True, slots=True)
class AutomationScopeProgressV1:
    target_package: str
    scope: str
    stage: str
    state: str
    attempt: int
    failure_class: str | None
    reason: str | None
    scroll_count: int
    screenshot_count: int


AutomationScopeProgressCallback = Callable[
    [AutomationScopeProgressV1], Awaitable[None]
]


class ArtifactBuilder(Protocol):
    async def build_debug_apk(self, request_id: str | None = None): ...


class AutomationAdb(Protocol):
    async def install_apk(self, serial: str, apk_path: Path, **kwargs) -> None: ...
    async def package_exists(self, serial: str, package_name: str) -> bool: ...
    async def run_instrumentation(self, serial: str, **kwargs) -> ProcessResult: ...
    async def start_activity(
        self,
        serial: str,
        component: str,
        extras: dict[str, str | int],
        **kwargs,
    ) -> None: ...
    async def force_stop(self, serial: str, package_name: str) -> None: ...
    async def current_user_id(self, serial: str) -> int: ...
    async def special_access_state(self, serial: str, package_name: str, access, **kwargs): ...
    async def restore_accessibility_service(
        self,
        serial: str,
        package_name: str,
        component: str,
        **kwargs,
    ): ...
    async def suspend_accessibility_service(
        self,
        serial: str,
        package_name: str,
        component: str,
        **kwargs,
    ) -> bool: ...
    async def accessibility_service_bound(self, serial: str, component: str) -> bool: ...
    async def accessibility_binding_state(
        self,
        serial: str,
        component: str,
    ) -> AccessibilityBindingState: ...
    async def wait_accessibility_service_bound(
        self,
        serial: str,
        component: str,
        *,
        timeout_seconds: float = ...,
        poll_seconds: float = ...,
    ) -> bool: ...
    async def set_text_only_cover_visible(
        self,
        serial: str,
        package_name: str,
        *,
        visible: bool,
        user_id: int | None = None,
    ) -> None: ...
    async def probe_text_only_cover_status(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> str | None: ...
    async def open_special_access_settings(
        self,
        serial: str,
        package_name: str,
        access: SpecialAccessKind,
        **kwargs,
    ) -> None: ...
    async def pull_social_debug_mapping(self, serial: str, **kwargs) -> int: ...


@dataclass(frozen=True, slots=True)
class AutomationConfig:
    apk_path: Path
    package_name: str
    runner_component: str
    test_class: str
    agent_component: str
    agent_package_name: str
    accessibility_component: str
    install_timeout_seconds: float
    target_timeout_seconds: float
    quick_scrolls: int
    full_scrolls: int
    quick_screenshots: int
    full_screenshots: int
    debug_snapshots: bool = False
    debug_dir: Path | None = None

    def __post_init__(self) -> None:
        if (
            self.install_timeout_seconds <= 0
            or self.target_timeout_seconds <= 0
            or "/" not in self.agent_component
            or "/" not in self.accessibility_component
            or "\x00" in self.agent_component
            or self.quick_scrolls not in range(0, 401)
            or self.full_scrolls not in range(0, 401)
            or self.quick_screenshots not in range(0, 49)
            or self.full_screenshots not in range(0, 49)
            or (self.debug_snapshots and self.debug_dir is None)
        ):
            raise ValueError("automation configuration is invalid")


class AndroidUiAutomationOrchestrator:
    def __init__(
        self,
        config: AutomationConfig,
        adb: AutomationAdb,
        artifact_builder: ArtifactBuilder,
        *,
        package_coordinator: AutomationPackageCoordinator | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._config = config
        self._adb = adb
        self._artifact_builder = artifact_builder
        self._packages = package_coordinator
        self._clock = clock

    async def run(
        self,
        *,
        serial: str,
        session_id: str,
        session_token: str,
        token_expires_at_epoch_ms: int,
        crawl_id: str,
        mode: str,
        not_before_epoch_ms: int | None = None,
        target_packages: Sequence[str],
        request_id: str | None,
        on_progress: AutomationProgressCallback | None = None,
        on_result: AutomationResultCallback | None = None,
        on_scope_progress: AutomationScopeProgressCallback | None = None,
    ) -> list[AutomationResultV1]:
        targets = tuple(dict.fromkeys(target_packages))
        if (
            not 32 <= len(session_token) <= 512
            or any(ord(char) < 32 or ord(char) == 127 for char in session_token)
            or isinstance(token_expires_at_epoch_ms, bool)
            or token_expires_at_epoch_ms <= 0
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Runtime automation Android tidak valid.",
            )
        if not targets or len(targets) > len(allowed_social_targets()):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Daftar target social Android tidak valid.",
            )
        if not set(targets) <= allowed_social_targets() or mode not in {"quick", "full"}:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Konfigurasi automation Android tidak valid.",
            )
        cutoff_epoch_ms = not_before_epoch_ms
        if cutoff_epoch_ms is None:
            cutoff_epoch_ms = build_time_scope(
                AcquisitionMode(mode),
                reference=self._clock(),
            ).not_before_epoch_ms
        if (
            isinstance(cutoff_epoch_ms, bool)
            or not isinstance(cutoff_epoch_ms, int)
            or cutoff_epoch_ms < MIN_TIME_SCOPE_EPOCH_MS
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas waktu automation Android tidak valid.",
            )
        await self._artifact_builder.build_debug_apk(request_id)
        if on_progress is not None:
            await on_progress(SYSTEM_PROGRESS_TARGET, "build")
        if self._packages is None:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Koordinator paket UiAutomator belum dikonfigurasi.",
            )
        install_action = await self._packages.ensure_installed(
            session_id=session_id,
            serial=serial,
            request_id=request_id,
        )
        if on_progress is not None:
            await on_progress(
                SYSTEM_PROGRESS_TARGET,
                "install_skip" if install_action == InstallAction.CURRENT else "install",
            )
        logger.info(
            "automation_runtime_identity",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "runner_component": self._config.runner_component,
                "test_class": self._config.test_class,
                "install_state": install_action.value,
                "targets": list(targets),
            },
        )
        user_id: int | None = None
        restore_accessibility = False
        try:
            user_id = await self._adb.current_user_id(serial)
            restore_accessibility = (
                await self._adb.special_access_state(
                    serial,
                    self._config.agent_package_name,
                    SpecialAccessKind.ACCESSIBILITY,
                    component=self._config.accessibility_component,
                    user_id=user_id,
                )
                == SpecialAccessState.GRANTED
            )
        except AcquisitionError as exc:
            logger.warning(
                "automation_accessibility_snapshot_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "error_category": exc.category.value,
                },
            )
        results: list[AutomationResultV1] = []
        try:
            for target in targets:
                if target in TEXT_ONLY_SOCIAL_TARGETS:
                    pass
                else:
                    if on_progress is not None:
                        await on_progress(target, "preflight_visual_suspend")
                    await self._suspend_accessibility_for_visual(
                        serial=serial,
                        request_id=request_id,
                        session_id=session_id,
                        crawl_id=crawl_id,
                        target_package=target,
                    )
                try:
                    result, instrumented, requires_force_stop = await self._run_target(
                        serial=serial,
                        session_id=session_id,
                        crawl_id=crawl_id,
                        mode=mode,
                        not_before_epoch_ms=cutoff_epoch_ms,
                        target_package=target,
                        request_id=request_id,
                        on_progress=on_progress,
                        on_scope_progress=on_scope_progress,
                    )
                    await self._pull_debug_mapping(
                        serial=serial,
                        session_id=session_id,
                        crawl_id=crawl_id,
                        target_package=target,
                        request_id=request_id,
                    )
                    if instrumented:
                        if requires_force_stop:
                            await self._stop_instrumentation(serial, request_id)
                        if on_progress is not None:
                            await on_progress(target, "restore_agent")
                        try:
                            await self._restart_agent(
                                serial,
                                session_id,
                                session_token,
                                token_expires_at_epoch_ms,
                            )
                        except AcquisitionError as exc:
                            logger.error(
                                "automation_agent_restart_failed",
                                extra={
                                    "request_id": request_id,
                                    "session_id": session_id,
                                    "crawl_id": crawl_id,
                                    "target_package": target,
                                    "error_category": exc.category.value,
                                },
                            )
                            if on_progress is not None:
                                await on_progress(target, "restore_agent_failed")
                    if target not in TEXT_ONLY_SOCIAL_TARGETS and restore_accessibility:
                        if on_progress is not None:
                            await on_progress(target, "restore_accessibility")
                        await self._restore_accessibility_best_effort(
                            serial=serial,
                            user_id=user_id,
                            request_id=request_id,
                            session_id=session_id,
                            crawl_id=crawl_id,
                            target_package=target,
                        )
                    results.append(result)
                    if on_result is not None:
                        await on_result(result)
                    if on_progress is not None:
                        detail = result.reason or result.state
                        await on_progress(target, f"{result.state}:{detail}")
                finally:
                    if target in TEXT_ONLY_SOCIAL_TARGETS:
                        # Instrumentation intentionally leaves the opaque cover
                        # pinned. Unpin only after the host has left X/Facebook
                        # and restored the agent foreground.
                        await self._hide_text_only_cover_best_effort(
                            serial=serial,
                            user_id=user_id,
                            request_id=request_id,
                            session_id=session_id,
                            crawl_id=crawl_id,
                            target_package=target,
                        )
        except asyncio.CancelledError:
            await self._hide_text_only_cover_best_effort(
                serial=serial,
                user_id=user_id,
                request_id=request_id,
                session_id=session_id,
                crawl_id=crawl_id,
                target_package="cancelled",
            )
            try:
                await self._adb.force_stop(serial, self._config.package_name)
            except AcquisitionError:
                pass
            await self._restart_agent_best_effort(
                serial,
                session_id,
                session_token,
                token_expires_at_epoch_ms,
                request_id,
            )
            if restore_accessibility:
                await self._restore_accessibility_best_effort(
                    serial=serial,
                    user_id=user_id,
                    request_id=request_id,
                    session_id=session_id,
                    crawl_id=crawl_id,
                    target_package="cancelled",
                )
            raise
        except Exception:
            await self._hide_text_only_cover_best_effort(
                serial=serial,
                user_id=user_id,
                request_id=request_id,
                session_id=session_id,
                crawl_id=crawl_id,
                target_package="failed",
            )
            try:
                await self._adb.force_stop(serial, self._config.package_name)
            except AcquisitionError:
                pass
            await self._restart_agent_best_effort(
                serial,
                session_id,
                session_token,
                token_expires_at_epoch_ms,
                request_id,
            )
            if restore_accessibility:
                await self._restore_accessibility_best_effort(
                    serial=serial,
                    user_id=user_id,
                    request_id=request_id,
                    session_id=session_id,
                    crawl_id=crawl_id,
                    target_package="failed",
                )
            raise
        return results

    async def _restore_accessibility_best_effort(
        self,
        *,
        serial: str,
        user_id: int | None,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
    ) -> bool:
        try:
            restored = await self._adb.restore_accessibility_service(
                serial,
                self._config.agent_package_name,
                self._config.accessibility_component,
                user_id=user_id,
            )
        except AcquisitionError as exc:
            logger.error(
                "automation_accessibility_restore_command_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "error_category": exc.category.value,
                },
            )
            return False
        if restored == SpecialAccessState.GRANTED and await self._adb.wait_accessibility_service_bound(
            serial,
            self._config.accessibility_component,
            timeout_seconds=TEXT_ONLY_ACCESSIBILITY_SETTLE_SECONDS,
            poll_seconds=TEXT_ONLY_ACCESSIBILITY_POLL_SECONDS,
        ):
            return True
        logger.error(
            "automation_accessibility_restore_failed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "access_state": restored.value,
                "binding_state": (
                    await self._adb.accessibility_binding_state(
                        serial,
                        self._config.accessibility_component,
                    )
                ).value,
            },
        )
        return False

    async def _hide_text_only_cover_best_effort(
        self,
        *,
        serial: str,
        user_id: int | None,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
    ) -> None:
        try:
            await self._adb.set_text_only_cover_visible(
                serial,
                self._config.agent_package_name,
                visible=False,
                user_id=user_id,
            )
        except AcquisitionError as exc:
            logger.warning(
                "automation_text_only_cover_cleanup_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "error_category": exc.category.value,
                },
            )

    async def _pull_debug_mapping(
        self,
        *,
        serial: str,
        session_id: str,
        crawl_id: str,
        target_package: str,
        request_id: str | None,
    ) -> None:
        if not self._config.debug_snapshots or self._config.debug_dir is None:
            return
        try:
            file_count = await self._adb.pull_social_debug_mapping(
                serial,
                agent_package=self._config.agent_package_name,
                session_id=session_id,
                crawl_id=crawl_id,
                target_package=target_package,
                destination_root=self._config.debug_dir,
                timeout=min(45.0, self._config.target_timeout_seconds),
            )
        except AcquisitionError as exc:
            logger.warning(
                "automation_debug_mapping_pull_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "error_category": exc.category.value,
                },
            )
            return
        logger.info(
            "automation_debug_mapping_pulled",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "file_count": file_count,
            },
        )

    async def _stop_instrumentation(
        self,
        serial: str,
        request_id: str | None,
    ) -> None:
        try:
            await self._adb.force_stop(serial, self._config.package_name)
        except AcquisitionError as exc:
            logger.warning(
                "automation_force_stop_failed",
                extra={
                    "request_id": request_id,
                    "error_category": exc.category.value,
                },
            )

    async def _restart_agent(
        self,
        serial: str,
        session_id: str,
        session_token: str,
        token_expires_at_epoch_ms: int,
    ) -> None:
        await self._adb.start_activity(
            serial,
            self._config.agent_component,
            {
                "session_id": session_id,
                "session_token": session_token,
                "token_expires_at_epoch_ms": token_expires_at_epoch_ms,
            },
            timeout=30.0,
        )

    async def _restart_agent_best_effort(
        self,
        serial: str,
        session_id: str,
        session_token: str,
        token_expires_at_epoch_ms: int,
        request_id: str | None,
    ) -> None:
        try:
            await self._restart_agent(
                serial,
                session_id,
                session_token,
                token_expires_at_epoch_ms,
            )
        except AcquisitionError as exc:
            logger.error(
                "automation_agent_restart_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "error_category": exc.category.value,
                },
            )

    async def _accessibility_failure_reason(self, serial: str) -> str:
        binding = await self._adb.accessibility_binding_state(
            serial,
            self._config.accessibility_component,
        )
        if binding == AccessibilityBindingState.CRASHED:
            return "accessibility_crashed"
        return "accessibility_required"

    async def _wait_for_accessibility_service(
        self,
        *,
        serial: str,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
    ) -> bool:
        component = self._config.accessibility_component
        if await self._adb.accessibility_service_bound(serial, component):
            # A bound service can still retain a failed/stale overlay attempt.
            # Clear the pin before the next functional show+probe retry.
            try:
                user_id = await self._adb.current_user_id(serial)
                await self._adb.set_text_only_cover_visible(
                    serial,
                    self._config.agent_package_name,
                    visible=False,
                    user_id=user_id,
                )
                await asyncio.sleep(TEXT_ONLY_COVER_PREFLIGHT_SETTLE_SECONDS)
            except AcquisitionError:
                pass
            return True
        try:
            user_id = await self._adb.current_user_id(serial)
            restored = await self._adb.restore_accessibility_service(
                serial,
                self._config.agent_package_name,
                component,
                user_id=user_id,
            )
        except AcquisitionError:
            restored = SpecialAccessState.UNAVAILABLE
        if restored == SpecialAccessState.GRANTED and await self._adb.wait_accessibility_service_bound(
            serial,
            component,
            timeout_seconds=TEXT_ONLY_ACCESSIBILITY_SETTLE_SECONDS,
            poll_seconds=TEXT_ONLY_ACCESSIBILITY_POLL_SECONDS,
        ):
            return True
        logger.error(
            "automation_text_only_accessibility_settle_timeout",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "binding_state": (
                    await self._adb.accessibility_binding_state(serial, component)
                ).value,
            },
        )
        return False

    async def _recover_accessibility_for_cover(
        self,
        *,
        serial: str,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
    ) -> bool:
        component = self._config.accessibility_component
        if await self._adb.accessibility_service_bound(serial, component):
            return True
        try:
            user_id = await self._adb.current_user_id(serial)
            restored = await self._adb.restore_accessibility_service(
                serial,
                self._config.agent_package_name,
                component,
                user_id=user_id,
            )
        except AcquisitionError as exc:
            logger.error(
                "automation_text_only_cover_accessibility_recovery_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "error_category": exc.category.value,
                },
            )
            return False
        if restored != SpecialAccessState.GRANTED:
            return False
        return await self._adb.wait_accessibility_service_bound(
            serial,
            component,
            timeout_seconds=TEXT_ONLY_ACCESSIBILITY_SETTLE_SECONDS,
            poll_seconds=TEXT_ONLY_ACCESSIBILITY_POLL_SECONDS,
        )

    async def _ensure_text_only_cover_preflight(
        self,
        *,
        serial: str,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
        on_progress: AutomationProgressCallback | None = None,
    ) -> tuple[bool, str]:
        user_id = await self._adb.current_user_id(serial)
        last_reason = "cover_probe_timeout"
        recovered = False
        for attempt in range(1, TEXT_ONLY_COVER_PREFLIGHT_ATTEMPTS + 1):
            try:
                await self._adb.set_text_only_cover_visible(
                    serial,
                    self._config.agent_package_name,
                    visible=True,
                    user_id=user_id,
                )
            except AcquisitionError as exc:
                logger.error(
                    "automation_text_only_cover_preflight_failed",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "crawl_id": crawl_id,
                        "target_package": target_package,
                        "attempt": attempt,
                        "error_category": exc.category.value,
                    },
                )
                return False, "cover_broadcast_denied"
            await asyncio.sleep(TEXT_ONLY_COVER_PREFLIGHT_SETTLE_SECONDS)
            status = await self._adb.probe_text_only_cover_status(
                serial,
                self._config.agent_package_name,
                user_id=user_id,
            )
            if on_progress is not None:
                await on_progress(
                    target_package,
                    f"preflight_cover_attempt_{attempt}:{status or 'no_response'}",
                )
            logger.info(
                "automation_text_only_cover_preflight_attempt",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "attempt": attempt,
                    "cover_status": status,
                },
            )
            if status == "shown":
                try:
                    await self._adb.set_text_only_cover_visible(
                        serial,
                        self._config.agent_package_name,
                        visible=False,
                        user_id=user_id,
                    )
                except AcquisitionError:
                    pass
                return True, "shown"
            if status == "hidden":
                last_reason = "cover_attach_failed"
            else:
                last_reason = "cover_probe_timeout"
            if (
                not recovered
                and attempt >= 2
                and last_reason == "cover_attach_failed"
            ):
                recovered = await self._recover_accessibility_for_cover(
                    serial=serial,
                    request_id=request_id,
                    session_id=session_id,
                    crawl_id=crawl_id,
                    target_package=target_package,
                )
        try:
            await self._adb.set_text_only_cover_visible(
                serial,
                self._config.agent_package_name,
                visible=False,
                user_id=user_id,
            )
        except AcquisitionError:
            pass
        return False, last_reason

    async def _suspend_accessibility_for_visual(
        self,
        *,
        serial: str,
        request_id: str | None,
        session_id: str,
        crawl_id: str,
        target_package: str,
    ) -> None:
        component = self._config.accessibility_component
        try:
            user_id = await self._adb.current_user_id(serial)
            suspended = await self._adb.suspend_accessibility_service(
                serial,
                self._config.agent_package_name,
                component,
                user_id=user_id,
            )
        except AcquisitionError as exc:
            logger.warning(
                "automation_visual_accessibility_suspend_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "error_category": exc.category.value,
                },
            )
            return
        if suspended:
            logger.info(
                "automation_visual_accessibility_suspended",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                },
            )
            return
        logger.warning(
            "automation_visual_accessibility_suspend_incomplete",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "binding_state": (
                    await self._adb.accessibility_binding_state(serial, component)
                ).value,
            },
        )

    async def _run_target(
        self,
        *,
        serial: str,
        session_id: str,
        crawl_id: str,
        mode: str,
        not_before_epoch_ms: int,
        target_package: str,
        request_id: str | None,
        on_progress: AutomationProgressCallback | None = None,
        on_scope_progress: AutomationScopeProgressCallback | None = None,
    ) -> tuple[AutomationResultV1, bool, bool]:
        if on_progress is not None:
            await on_progress(target_package, "target_probe")
        try:
            installed = await self._adb.package_exists(serial, target_package)
        except AcquisitionError:
            return failure_result(target_package, "failed", "target_probe_failed"), False, False
        if not installed:
            return (
                failure_result(target_package, "target_missing", "target_not_installed"),
                False,
                False,
            )
        if target_package in TEXT_ONLY_SOCIAL_TARGETS:
            if on_progress is not None:
                await on_progress(target_package, "preflight_accessibility")
            if not await self._wait_for_accessibility_service(
                serial=serial,
                request_id=request_id,
                session_id=session_id,
                crawl_id=crawl_id,
                target_package=target_package,
            ):
                return (
                    failure_result(
                        target_package,
                        "failed",
                        await self._accessibility_failure_reason(serial),
                    ),
                    False,
                    False,
                )
            if on_progress is not None:
                await on_progress(target_package, "preflight_cover")
            cover_ready, cover_reason = await self._ensure_text_only_cover_preflight(
                serial=serial,
                request_id=request_id,
                session_id=session_id,
                crawl_id=crawl_id,
                target_package=target_package,
                on_progress=on_progress,
            )
            if not cover_ready:
                logger.error(
                    "automation_text_only_cover_unavailable",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "crawl_id": crawl_id,
                        "target_package": target_package,
                        "cover_reason": cover_reason,
                    },
                )
                return (
                    failure_result(
                        target_package,
                        "failed",
                        "text_only_cover_required",
                    ),
                    False,
                    False,
                )
        if on_progress is not None:
            await on_progress(target_package, "instrument")
        scrolls = self._config.quick_scrolls if mode == "quick" else self._config.full_scrolls
        screenshots = 0
        if target_package not in TEXT_ONLY_SOCIAL_TARGETS:
            screenshots = (
                self._config.quick_screenshots
                if mode == "quick"
                else self._config.full_screenshots
            )
        logger.info(
            "automation_target_started",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "capture_mode": (
                    "text_only"
                    if target_package in TEXT_ONLY_SOCIAL_TARGETS
                    else "visual"
                ),
            },
        )
        navigation_deadline_ms = int(
            max(15.0, self._config.target_timeout_seconds - 10.0) * 1000,
        )
        observed_scope_progress: list[AutomationScopeProgressV1] = []

        async def on_instrumentation_line(line: str) -> None:
            try:
                progress = parse_instrumentation_scope_progress(line, target_package)
            except AcquisitionError as exc:
                logger.warning(
                    "automation_scope_progress_invalid",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "crawl_id": crawl_id,
                        "target_package": target_package,
                        "error_category": exc.category.value,
                    },
                )
                return
            if progress is None:
                return
            observed_scope_progress.append(progress)
            if on_scope_progress is not None:
                await on_scope_progress(progress)

        try:
            result = await self._adb.run_instrumentation(
                serial,
                runner_component=self._config.runner_component,
                test_class=self._config.test_class,
                arguments={
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "max_scrolls": scrolls,
                    "max_screenshots": screenshots,
                    "launch_timeout_ms": 12_000,
                    "stable_wait_ms": (
                        350 if target_package in TEXT_ONLY_SOCIAL_TARGETS else 300
                    ),
                    "navigation_deadline_ms": navigation_deadline_ms,
                    "not_before_epoch_ms": not_before_epoch_ms,
                    "debug_snapshots": str(self._config.debug_snapshots).lower(),
                },
                timeout=self._config.target_timeout_seconds,
                on_stdout_line=on_instrumentation_line,
            )
        except AcquisitionError as exc:
            state = "timeout" if exc.category == ErrorCategory.ADB_TIMEOUT else "failed"
            reason = "automation_timeout" if state == "timeout" else "automation_adb_failure"
            return failure_result(target_package, state, reason), True, True
        if result.returncode != 0 or result.output_truncated:
            logger.warning(
                "automation_instrumentation_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": target_package,
                    "returncode": result.returncode,
                    "truncated": result.output_truncated,
                    "failure_token": instrumentation_failure_token(
                        result.stdout,
                        result.stderr,
                    ),
                },
            )
            return (
                failure_result(target_package, "failed", "instrumentation_failed"),
                True,
                True,
            )
        try:
            for raw_line in result.stdout.splitlines():
                try:
                    progress = parse_instrumentation_scope_progress(
                        raw_line,
                        target_package,
                    )
                except AcquisitionError:
                    continue
                if progress is not None and progress not in observed_scope_progress:
                    observed_scope_progress.append(progress)
            parsed = enforce_required_scope_evidence(
                normalize_automation_result(
                parse_instrumentation_result(
                    f"{result.stdout}\n{result.stderr}",
                    target_package,
                ),
                target_package,
                ),
                target_package,
                observed_scope_progress,
            )
        except AcquisitionError:
            return (
                failure_result(target_package, "failed", "instrumentation_result_invalid"),
                True,
                True,
            )
        logger.info(
            "automation_target_completed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crawl_id": crawl_id,
                "target_package": target_package,
                "state": parsed.state,
                "reason": parsed.reason,
                "scroll_count": parsed.scroll_count,
                "screenshot_count": len(parsed.screenshot_ids),
                "duration_ms": parsed.duration_ms,
            },
        )
        return parsed, True, False


def instrumentation_failure_token(stdout: str, stderr: str) -> str:
    blob = f"{stdout}\n{stderr}"
    if "Unable to find instrumentation" in blob:
        return "runner_not_registered"
    if "Unable to instantiate instrumentation" in blob:
        return "runner_instantiate_failed"
    if "PROCESS_CRASHED" in blob:
        return "process_crashed"
    return "instrument_nonzero_exit"


def parse_instrumentation_scope_progress(
    raw_line: str,
    expected_target: str,
) -> AutomationScopeProgressV1 | None:
    line = raw_line.strip()
    prefix = next(
        (item for item in SCOPE_PROGRESS_PREFIXES if line.startswith(item)),
        None,
    )
    if prefix is None:
        return None
    encoded = line.removeprefix(prefix).strip()
    if not encoded or len(encoded) > 32 * 1024:
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Progress scope automation Android tidak valid.",
        )
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Progress scope automation Android tidak valid.",
        ) from exc
    if not isinstance(payload, dict):
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Progress scope automation Android tidak valid.",
        )
    target = payload.get("target_package")
    scope = payload.get("scope")
    stage = payload.get("stage")
    state = payload.get("state")
    attempt = payload.get("attempt")
    failure_class = payload.get("failure_class")
    reason = payload.get("reason")
    scroll_count = payload.get("scroll_count")
    screenshot_count = payload.get("screenshot_count")
    valid_integer_fields = (
        isinstance(attempt, int)
        and not isinstance(attempt, bool)
        and 0 <= attempt <= 32
        and isinstance(scroll_count, int)
        and not isinstance(scroll_count, bool)
        and 0 <= scroll_count <= 2_000
        and isinstance(screenshot_count, int)
        and not isinstance(screenshot_count, bool)
        and 0 <= screenshot_count <= 48
    )
    if (
        payload.get("schema_version") != 1
        or target != expected_target
        or expected_target not in TARGET_REQUIRED_SCOPES
        or not isinstance(scope, str)
        or scope not in TARGET_REQUIRED_SCOPES[expected_target]
        or not isinstance(stage, str)
        or SAFE_SCOPE_TOKEN.fullmatch(stage) is None
        or state not in SCOPE_PROGRESS_STATES
        or not valid_integer_fields
        or (failure_class is not None and failure_class not in SCOPE_FAILURE_CLASSES)
        or (
            reason is not None
            and (
                not isinstance(reason, str)
                or re.fullmatch(r"[a-z0-9_]{1,128}", reason) is None
            )
        )
    ):
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Progress scope automation Android tidak konsisten.",
        )
    return AutomationScopeProgressV1(
        target_package=target,
        scope=scope,
        stage=stage,
        state=state,
        attempt=attempt,
        failure_class=failure_class,
        reason=reason,
        scroll_count=scroll_count,
        screenshot_count=screenshot_count,
    )


def enforce_required_scope_evidence(
    result: AutomationResultV1,
    target_package: str,
    progress: Sequence[AutomationScopeProgressV1],
) -> AutomationResultV1:
    """Downgrade a claimed complete only when scope progress was observed.

    Empty progress must not fail a target: OEM/ADB buffers often drop
    INSTRUMENTATION_STATUS lines while the final siksik_result remains valid.
    Engine already emits partial when a required scope fails on-device.
    """
    if result.state != "complete":
        return result
    required = TARGET_REQUIRED_SCOPES.get(target_package)
    if required is None:
        return result
    observed = [item for item in progress if item.target_package == target_package]
    if not observed:
        return result
    completed = {item.scope for item in observed if item.state == "complete"}
    if required <= completed:
        return result
    return result.model_copy(
        update={"state": "failed", "reason": "required_scope_evidence_missing"}
    )


def parse_instrumentation_result(
    output: str,
    expected_target: str,
) -> AutomationResultV1:
    values: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        prefix = next((item for item in RESULT_PREFIXES if line.startswith(item)), None)
        if prefix is not None:
            value = line.removeprefix(prefix).strip()
            if value and value not in values:
                values.append(value)
    if len(values) != 1 or len(values[0]) > 64 * 1024:
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Hasil automation Android tidak valid.",
        )
    try:
        result = AutomationResultV1.model_validate_json(values[0])
    except ValidationError as exc:
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Hasil automation Android tidak valid.",
        ) from exc
    if result.target_package != expected_target:
        raise acquisition_error(
            ErrorCategory.AGENT_INVALID_RESPONSE,
            "Target hasil automation Android tidak konsisten.",
        )
    return result


def normalize_automation_result(
    result: AutomationResultV1,
    target_package: str,
) -> AutomationResultV1:
    if result.state == "partial" and target_package in allowed_social_targets():
        return result.model_copy(
            update={
                "state": "failed",
                "reason": result.reason or "scope_navigation_incomplete",
            }
        )
    return result


def failure_result(target: str, state: str, reason: str) -> AutomationResultV1:
    return AutomationResultV1.model_validate(
        {
            "schema_version": 1,
            "target_package": target,
            "state": state,
            "reason": reason,
            "scroll_count": 0,
            "screenshot_ids": [],
            "duration_ms": 0,
        }
    )
