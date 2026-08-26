from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.acquisition.adb import AsyncAdbTransport, InstalledPackage
from app.acquisition.apk_metadata import ApkMetadata
from app.acquisition.bootstrap_contracts import InstallAction, MetadataInspector
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.runtime import device_ref

logger = logging.getLogger("siksik.acquisition.automation_package")


@dataclass(frozen=True, slots=True)
class AutomationPackageConfig:
    package_name: str
    apk_path: Path
    install_timeout_seconds: float
    inspection_root: Path
    force_reinstall: bool = True


class AutomationPackageCoordinator:
    def __init__(
        self,
        config: AutomationPackageConfig,
        adb: AsyncAdbTransport,
        inspector: MetadataInspector,
    ) -> None:
        self._config = config
        self._adb = adb
        self._inspector = inspector

    def _resolved_apk(self) -> Path:
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

    async def inspect_desired_apk(self) -> ApkMetadata:
        return await self._inspector.inspect(self._resolved_apk())

    async def inspect_installed_apk(
        self,
        serial: str,
        package: InstalledPackage,
    ) -> ApkMetadata:
        if package.apk_path is None:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Path APK automation terpasang tidak tersedia.",
            )
        root = self._config.inspection_root.expanduser().resolve()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        temporary = Path(
            await asyncio.to_thread(tempfile.mkdtemp, prefix="automation-apk-", dir=root)
        )
        destination = temporary / "installed-base.apk"
        try:
            await self._adb.pull_installed_apk(serial, package.apk_path, destination)
            return await self._inspector.inspect(destination)
        finally:
            await asyncio.to_thread(shutil.rmtree, temporary, True)

    def install_action(
        self,
        *,
        desired_apk: ApkMetadata,
        installed_package: InstalledPackage,
        installed_apk: ApkMetadata | None,
    ) -> InstallAction:
        if not installed_package.installed:
            return InstallAction.INSTALL
        if installed_apk is None:
            return InstallAction.UPDATE
        if installed_apk.version_code > desired_apk.version_code:
            raise acquisition_error(
                ErrorCategory.AGENT_VERSION_MISMATCH,
                "Versi paket UiAutomator terpasang lebih baru dari artifact SIKSIK.",
            )
        if installed_apk.signer_sha256 != desired_apk.signer_sha256:
            return InstallAction.UPDATE
        if installed_apk.apk_sha256 == desired_apk.apk_sha256:
            return InstallAction.CURRENT
        if self._config.force_reinstall:
            return InstallAction.UPDATE
        return InstallAction.UPDATE

    async def ensure_installed(
        self,
        *,
        session_id: str,
        serial: str,
        request_id: str | None,
        on_user_restricted: Callable[[], Awaitable[None]] | None = None,
    ) -> InstallAction:
        desired_apk = await self.inspect_desired_apk()
        apk_path = self._resolved_apk()
        installed_package = await self._adb.inspect_package(serial, self._config.package_name)
        installed_apk: ApkMetadata | None = None
        if installed_package.installed:
            installed_apk = await self.inspect_installed_apk(serial, installed_package)
        action = self.install_action(
            desired_apk=desired_apk,
            installed_package=installed_package,
            installed_apk=installed_apk,
        )
        if action == InstallAction.CURRENT:
            if not await self._adb.package_exists(serial, self._config.package_name):
                raise acquisition_error(
                    ErrorCategory.AGENT_INSTALL_FAILED,
                    "Paket UiAutomator terdaftar tidak konsisten di perangkat.",
                    retryable=True,
                )
            logger.info(
                "automation_install_completed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "state": InstallAction.CURRENT.value,
                    "apk_sha256": desired_apk.apk_sha256,
                    "signer_sha256": desired_apk.signer_sha256,
                    "version_code": desired_apk.version_code,
                },
            )
            return action
        started = time.monotonic()
        logger.info(
            "automation_install_started",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "state": action.value,
                "apk_sha256": desired_apk.apk_sha256,
                "signer_sha256": desired_apk.signer_sha256,
                "version_code": desired_apk.version_code,
            },
        )
        try:
            await self._adb.install_apk(
                serial,
                apk_path,
                grant_runtime_permissions=False,
                allow_test_packages=True,
                replace_package_on_uid_mismatch=self._config.package_name,
                timeout=self._config.install_timeout_seconds,
                approval_poll_seconds=1.0,
                on_user_restricted=on_user_restricted,
            )
        except AcquisitionError as exc:
            logger.warning(
                "automation_install_failed",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "device_ref": device_ref(serial),
                    "error_category": exc.category.value,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        if not await self._adb.package_exists(serial, self._config.package_name):
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Instalasi paket UiAutomator gagal — periksa persetujuan instalasi di perangkat.",
                retryable=True,
            )
        verified = await self._adb.inspect_package(serial, self._config.package_name)
        if not verified.installed:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Paket UiAutomator tidak terpasang di perangkat.",
                retryable=True,
            )
        if verified.version_code == desired_apk.version_code:
            inspected = desired_apk
        else:
            inspected = await self.inspect_installed_apk(serial, verified)
            if (
                inspected.apk_sha256 != desired_apk.apk_sha256
                or inspected.signer_sha256 != desired_apk.signer_sha256
                or inspected.version_code != desired_apk.version_code
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INSTALL_FAILED,
                    "Hasil instalasi paket UiAutomator tidak sesuai artifact SIKSIK.",
                )
        logger.info(
            "automation_install_completed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "device_ref": device_ref(serial),
                "state": action.value,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "apk_sha256": desired_apk.apk_sha256,
                "signer_sha256": desired_apk.signer_sha256,
                "version_code": desired_apk.version_code,
            },
        )
        return action
