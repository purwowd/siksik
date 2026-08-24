from __future__ import annotations

from pathlib import Path

import pytest

from app.acquisition.adb import InstalledPackage
from app.acquisition.apk_metadata import ApkMetadata
from app.acquisition.automation_package import (
    AutomationPackageConfig,
    AutomationPackageCoordinator,
)
from app.acquisition.bootstrap_contracts import InstallAction


def metadata(apk_sha256: str, version_code: int = 1) -> ApkMetadata:
    return ApkMetadata(
        path=Path("/tmp/automation.apk"),
        package_name="com.siksik.agent.automation",
        version_code=version_code,
        version_name="0.1.0",
        apk_sha256=apk_sha256,
        signer_sha256="s" * 64,
        uses_shared_user_id=False,
        size_bytes=1024,
    )


@pytest.mark.unit
def test_install_action_current_when_hash_matches() -> None:
    desired = metadata("a" * 64)
    installed = metadata("a" * 64)
    coordinator = AutomationPackageCoordinator(
        AutomationPackageConfig(
            package_name="com.siksik.agent.automation",
            apk_path=Path("/tmp/automation.apk"),
            install_timeout_seconds=30,
            inspection_root=Path("/tmp/inspect"),
            force_reinstall=True,
        ),
        adb=None,  # type: ignore[arg-type]
        inspector=None,  # type: ignore[arg-type]
    )
    action = coordinator.install_action(
        desired_apk=desired,
        installed_package=InstalledPackage(
            installed=True,
            version_code=1,
            version_name="0.1.0",
            apk_path="/data/app/base.apk",
        ),
        installed_apk=installed,
    )
    assert action == InstallAction.CURRENT


@pytest.mark.unit
def test_install_action_update_when_hash_differs() -> None:
    desired = metadata("b" * 64)
    installed = metadata("a" * 64)
    coordinator = AutomationPackageCoordinator(
        AutomationPackageConfig(
            package_name="com.siksik.agent.automation",
            apk_path=Path("/tmp/automation.apk"),
            install_timeout_seconds=30,
            inspection_root=Path("/tmp/inspect"),
            force_reinstall=True,
        ),
        adb=None,  # type: ignore[arg-type]
        inspector=None,  # type: ignore[arg-type]
    )
    action = coordinator.install_action(
        desired_apk=desired,
        installed_package=InstalledPackage(
            installed=True,
            version_code=1,
            version_name="0.1.0",
            apk_path="/data/app/base.apk",
        ),
        installed_apk=installed,
    )
    assert action == InstallAction.UPDATE
