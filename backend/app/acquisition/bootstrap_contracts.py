from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from app.acquisition.adb import InstalledPackage, SpecialAccessKind
from app.acquisition.agent_artifact import AgentArtifact
from app.acquisition.agent_client import AgentCapabilitiesV1
from app.acquisition.apk_metadata import ApkMetadata
from app.acquisition.runtime import AgentRuntimeState

Progress = Callable[..., Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


class ArtifactBuilder(Protocol):
    async def build_debug_apk(self, request_id: str | None = None) -> AgentArtifact: ...


class MetadataInspector(Protocol):
    async def inspect(self, apk_path: Path) -> ApkMetadata: ...


class BootstrapAgentClient(Protocol):
    async def health(self, *, request_id: str | None = None): ...
    async def capabilities(self, *, request_id: str | None = None): ...
    async def bootstrap(
        self,
        session_id: str,
        api_version: str,
        *,
        request_id: str | None = None,
    ): ...
    async def stop(self, session_id: str, *, request_id: str | None = None): ...


AgentClientFactory = Callable[[int, str], BootstrapAgentClient]


class InstallAction(str, Enum):
    INSTALL = "install"
    CURRENT = "current"
    UPDATE = "update"


@dataclass(frozen=True, slots=True)
class RuntimePermissionRequirement:
    permission: str
    required: bool


@dataclass(frozen=True, slots=True)
class AgentBootstrapConfig:
    package_name: str
    component: str
    api_version: str
    device_port: int
    minimum_api: int
    token_ttl_seconds: int
    install_timeout_seconds: float
    minimum_device_storage_bytes: int
    special_access_timeout_seconds: float
    special_access_poll_seconds: float
    required_special_access: tuple[SpecialAccessKind, ...]
    accessibility_component: str
    notification_component: str
    inspection_root: Path
    # Every bootstrap always adb install -r (lab UX: JALANKAN AKUISISI = latest APK).
    force_reinstall: bool = True


@dataclass(slots=True)
class BootstrapWorkingState:
    artifact: AgentArtifact | None = None
    desired_apk: ApkMetadata | None = None
    installed_package: InstalledPackage | None = None
    installed_apk: ApkMetadata | None = None
    install_action: InstallAction | None = None
    automation_install_action: InstallAction | None = None
    install_strategy: str | None = None
    install_attempt_count: int = 0
    runtime_granted_during_install: bool | None = None
    device_manufacturer: str | None = None
    device_model: str | None = None
    device_api_level: int | None = None
    forward_host_port: int | None = None
    token: str | None = None
    token_expires_at: datetime | None = None
    runtime_permissions: dict[str, str] = field(default_factory=dict)
    special_access: dict[str, str] = field(default_factory=dict)
    capabilities: AgentCapabilitiesV1 | None = None

    def safe_details(self) -> dict[str, object]:
        result: dict[str, object] = {
            "runtime_permissions": dict(self.runtime_permissions),
            "special_access": dict(self.special_access),
        }
        if self.install_action is not None:
            result["install_action"] = self.install_action.value
        if self.automation_install_action is not None:
            result["automation_install_action"] = self.automation_install_action.value
        if self.install_strategy is not None:
            result["install_strategy"] = self.install_strategy
            result["install_attempt_count"] = self.install_attempt_count
        if self.runtime_granted_during_install is not None:
            result["runtime_granted_during_install"] = self.runtime_granted_during_install
        if self.device_manufacturer is not None:
            result["device_manufacturer"] = self.device_manufacturer
        if self.device_model is not None:
            result["device_model"] = self.device_model
        if self.device_api_level is not None:
            result["device_api_level"] = self.device_api_level
        if self.installed_package is not None:
            result["installed_version_code"] = self.installed_package.version_code
            result["installed_version_name"] = self.installed_package.version_name
        if self.capabilities is not None:
            result["capabilities"] = self.capabilities.model_dump(mode="json")
        return result


STATE_PERCENT = {
    AgentRuntimeState.DETECT_DEVICE: 5.0,
    AgentRuntimeState.VALIDATE_DEVICE: 10.0,
    AgentRuntimeState.RESOLVE_OR_BUILD_AGENT: 20.0,
    AgentRuntimeState.INSPECT_INSTALLED_PACKAGE: 32.0,
    AgentRuntimeState.INSTALL_OR_UPDATE: 45.0,
    AgentRuntimeState.INSTALL_AUTOMATION: 50.0,
    AgentRuntimeState.AWAITING_INSTALL_APPROVAL: 48.0,
    AgentRuntimeState.START_AGENT: 55.0,
    AgentRuntimeState.CREATE_FORWARD: 62.0,
    AgentRuntimeState.AUTHENTICATE_AND_NEGOTIATE: 70.0,
    AgentRuntimeState.APPLY_RUNTIME_PERMISSIONS: 78.0,
    AgentRuntimeState.AWAITING_RUNTIME_PERMISSION: 82.0,
    AgentRuntimeState.VERIFY_SPECIAL_ACCESS: 88.0,
    AgentRuntimeState.AWAITING_ACCESS: 92.0,
    AgentRuntimeState.READY: 100.0,
    AgentRuntimeState.FAILED: 100.0,
    AgentRuntimeState.CANCELLED: 100.0,
    AgentRuntimeState.CLOSED: 100.0,
}

STATE_MESSAGES = {
    AgentRuntimeState.DETECT_DEVICE: "Mendeteksi perangkat Android",
    AgentRuntimeState.VALIDATE_DEVICE: "Memvalidasi kesiapan perangkat",
    AgentRuntimeState.RESOLVE_OR_BUILD_AGENT: "Build APK Android agent terbaru",
    AgentRuntimeState.INSPECT_INSTALLED_PACKAGE: "Memeriksa package Android agent",
    AgentRuntimeState.INSTALL_OR_UPDATE: "Memasang Android agent terbaru ke perangkat",
    AgentRuntimeState.INSTALL_AUTOMATION: "Memasang paket UiAutomator ke perangkat",
    AgentRuntimeState.AWAITING_INSTALL_APPROVAL: (
        "Menunggu persetujuan instalasi USB pada perangkat"
    ),
    AgentRuntimeState.START_AGENT: "Menjalankan Android agent",
    AgentRuntimeState.CREATE_FORWARD: "Membuat koneksi ADB lokal",
    AgentRuntimeState.AUTHENTICATE_AND_NEGOTIATE: "Memverifikasi sesi Android agent",
    AgentRuntimeState.APPLY_RUNTIME_PERMISSIONS: "Menerapkan izin runtime Android agent",
    AgentRuntimeState.AWAITING_RUNTIME_PERMISSION: (
        "Izinkan akses media dan komunikasi pada popup di layar agent"
    ),
    AgentRuntimeState.VERIFY_SPECIAL_ACCESS: "Memverifikasi special access Android",
    AgentRuntimeState.AWAITING_ACCESS: "Menunggu konfirmasi akses pada perangkat",
    AgentRuntimeState.READY: "Android agent siap",
    AgentRuntimeState.FAILED: "Bootstrap Android agent gagal",
    AgentRuntimeState.CANCELLED: "Bootstrap Android agent dibatalkan",
    AgentRuntimeState.CLOSED: "Sesi Android agent ditutup",
}

SPECIAL_ACCESS_WAIT_MESSAGES = {
    SpecialAccessKind.ACCESSIBILITY: "Menunggu konfirmasi Aksesibilitas pada perangkat",
    SpecialAccessKind.MANAGE_ALL_FILES: "Menunggu izin Semua file pada perangkat",
}


def runtime_permissions_for_api(api_level: int) -> tuple[RuntimePermissionRequirement, ...]:
    if api_level >= 33:
        return (
            RuntimePermissionRequirement("android.permission.READ_MEDIA_IMAGES", True),
            RuntimePermissionRequirement("android.permission.READ_MEDIA_VIDEO", True),
            RuntimePermissionRequirement("android.permission.READ_MEDIA_AUDIO", True),
            RuntimePermissionRequirement("android.permission.ACCESS_MEDIA_LOCATION", False),
            RuntimePermissionRequirement("android.permission.POST_NOTIFICATIONS", False),
            RuntimePermissionRequirement("android.permission.READ_SMS", True),
            RuntimePermissionRequirement("android.permission.READ_CONTACTS", True),
            RuntimePermissionRequirement("android.permission.GET_ACCOUNTS", False),
            RuntimePermissionRequirement("android.permission.USE_CREDENTIALS", False),
        )
    if api_level >= 29:
        return (
            RuntimePermissionRequirement("android.permission.READ_EXTERNAL_STORAGE", True),
            RuntimePermissionRequirement("android.permission.ACCESS_MEDIA_LOCATION", False),
            RuntimePermissionRequirement("android.permission.READ_SMS", True),
            RuntimePermissionRequirement("android.permission.READ_CONTACTS", True),
            RuntimePermissionRequirement("android.permission.GET_ACCOUNTS", False),
            RuntimePermissionRequirement("android.permission.USE_CREDENTIALS", False),
        )
    return (
        RuntimePermissionRequirement("android.permission.READ_EXTERNAL_STORAGE", True),
        RuntimePermissionRequirement("android.permission.READ_SMS", True),
        RuntimePermissionRequirement("android.permission.READ_CONTACTS", True),
        RuntimePermissionRequirement("android.permission.GET_ACCOUNTS", False),
        RuntimePermissionRequirement("android.permission.USE_CREDENTIALS", False),
    )


def special_access_for_inventory_mode(
    mode: str,
) -> tuple[tuple[SpecialAccessKind, ...], tuple[SpecialAccessKind, ...]]:
    if mode not in {"quick", "full"}:
        raise ValueError("Android inventory mode is invalid")
    optional = (
        SpecialAccessKind.MANAGE_ALL_FILES,
        SpecialAccessKind.NOTIFICATION_LISTENER,
    )
    return (SpecialAccessKind.ACCESSIBILITY,), optional
