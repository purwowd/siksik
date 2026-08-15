from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

AutomationProgressCallback = Callable[[str, str], Awaitable[None]]

from pydantic import ValidationError

from app.acquisition.adb import SpecialAccessKind, SpecialAccessState
from app.acquisition.agent_client import AutomationResultV1
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import ProcessResult
from app.acquisition.time_scope import MIN_TIME_SCOPE_EPOCH_MS, build_time_scope
from app.models.schemas import AcquisitionMode

logger = logging.getLogger("siksik.acquisition.automation")
ALLOWED_SOCIAL_TARGETS = frozenset(
    {
        "com.twitter.android",
        "com.facebook.katana",
        "com.instagram.android",
    }
)
TEXT_ONLY_SOCIAL_TARGETS = frozenset({
    "com.twitter.android",
    "com.facebook.katana",
})
RESULT_PREFIXES = (
    "INSTRUMENTATION_STATUS: siksik_result=",
    "INSTRUMENTATION_RESULT: siksik_result=",
)
RESULT_PREFIX = RESULT_PREFIXES[0]


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
            or self.quick_scrolls not in range(0, 41)
            or self.full_scrolls not in range(0, 41)
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
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._config = config
        self._adb = adb
        self._artifact_builder = artifact_builder
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
        if not targets or len(targets) > len(ALLOWED_SOCIAL_TARGETS):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Daftar target social Android tidak valid.",
            )
        if not set(targets) <= ALLOWED_SOCIAL_TARGETS or mode not in {"quick", "full"}:
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
        apk = self._validated_apk()
        logger.info(
            "automation_install_started",
            extra={"request_id": request_id, "session_id": session_id},
        )
        await self._adb.install_apk(
            serial,
            apk,
            grant_runtime_permissions=False,
            allow_test_packages=True,
            replace_package_on_uid_mismatch=self._config.package_name,
            timeout=self._config.install_timeout_seconds,
        )
        logger.info(
            "automation_install_completed",
            extra={"request_id": request_id, "session_id": session_id},
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
                if on_progress is not None:
                    await on_progress(target, "started")
                result, instrumented, requires_force_stop = await self._run_target(
                    serial=serial,
                    session_id=session_id,
                    crawl_id=crawl_id,
                    mode=mode,
                    not_before_epoch_ms=cutoff_epoch_ms,
                    target_package=target,
                    request_id=request_id,
                )
                await self._pull_debug_mapping(
                    serial=serial,
                    session_id=session_id,
                    crawl_id=crawl_id,
                    target_package=target,
                    request_id=request_id,
                )
                if on_progress is not None:
                    detail = result.reason or result.state
                    await on_progress(target, f"{result.state}:{detail}")
                if instrumented:
                    if requires_force_stop:
                        await self._stop_instrumentation(serial, request_id)
                    if restore_accessibility and user_id is not None:
                        try:
                            restored = await self._adb.restore_accessibility_service(
                                serial,
                                self._config.agent_package_name,
                                self._config.accessibility_component,
                                user_id=user_id,
                            )
                        except AcquisitionError as exc:
                            restored = SpecialAccessState.UNAVAILABLE
                            logger.error(
                                "automation_accessibility_restore_command_failed",
                                extra={
                                    "request_id": request_id,
                                    "session_id": session_id,
                                    "crawl_id": crawl_id,
                                    "target_package": target,
                                    "error_category": exc.category.value,
                                },
                            )
                        if restored != SpecialAccessState.GRANTED:
                            logger.error(
                                "automation_accessibility_restore_failed",
                                extra={
                                    "request_id": request_id,
                                    "session_id": session_id,
                                    "crawl_id": crawl_id,
                                    "target_package": target,
                                    "access_state": restored.value,
                                },
                            )
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
                        result = failure_result(target, "failed", "agent_restart_failed")
                results.append(result)
        except asyncio.CancelledError:
            await self._adb.force_stop(serial, self._config.package_name)
            await self._restart_agent_best_effort(
                serial,
                session_id,
                session_token,
                token_expires_at_epoch_ms,
                request_id,
            )
            raise
        return results

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
    ) -> tuple[AutomationResultV1, bool, bool]:
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
            min(175.0, max(15.0, self._config.target_timeout_seconds - 10.0)) * 1000,
        )
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
            )
        except AcquisitionError as exc:
            state = "timeout" if exc.category == ErrorCategory.ADB_TIMEOUT else "failed"
            reason = "automation_timeout" if state == "timeout" else "automation_adb_failure"
            return failure_result(target_package, state, reason), True, True
        if result.returncode != 0 or result.output_truncated:
            return (
                failure_result(target_package, "failed", "instrumentation_failed"),
                True,
                True,
            )
        try:
            parsed = parse_instrumentation_result(
                f"{result.stdout}\n{result.stderr}",
                target_package,
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

    def _validated_apk(self) -> Path:
        path = self._config.apk_path.expanduser().resolve()
        if (
            not path.is_file()
            or path.suffix.lower() != ".apk"
            or not 0 < path.stat().st_size <= 250 * 1024 * 1024
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_FAILED,
                "Build tidak menghasilkan APK automation yang valid.",
            )
        return path


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
