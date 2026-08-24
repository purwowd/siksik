from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.install_policy import (
    ApkInstallOutcome,
    InstallAttempt,
    InstallFailureKind,
    evaluate_install_result,
    initial_install_attempt,
    next_install_attempt,
    oem_install_guidance,
)
from app.acquisition.process import ProcessResult, run_process

logger = logging.getLogger("siksik.acquisition.adb")

SAFE_SERIAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_EXTRA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
SAFE_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{1,254}$")
SAFE_PERMISSION = re.compile(r"^android\.permission\.[A-Z0-9_]{2,128}$")
SAFE_CLASS = re.compile(
    r"^[A-Za-z][A-Za-z0-9_$]*(?:\.[A-Za-z][A-Za-z0-9_$]*)*$",
)
RUNTIME_PERMISSION_PROBE_ACTION = "com.siksik.agent.action.PROBE_RUNTIME_PERMISSIONS"
RUNTIME_PERMISSION_PROBE_PREFIX = "SIKSIK_PERMISSION_V1"
RUNTIME_PERMISSION_PROBE_CLASS = "permission.RuntimePermissionProbeReceiver"
RUNTIME_PERMISSION_PROBE_TTL_SECONDS = 0.5
RUNTIME_PERMISSION_NAMES = (
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_MEDIA_AUDIO",
    "android.permission.ACCESS_MEDIA_LOCATION",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.READ_SMS",
    "android.permission.READ_CONTACTS",
    "android.permission.GET_ACCOUNTS",
)
TEXT_ONLY_COVER_PROBE_ACTION = "com.siksik.agent.action.TEXT_ONLY_CRAWL_COVER_PROBE"
TEXT_ONLY_COVER_ACTION = "com.siksik.agent.action.TEXT_ONLY_CRAWL_COVER"
TEXT_ONLY_COVER_PROBE_PREFIX = "SIKSIK_COVER_V1"
TEXT_ONLY_COVER_RECEIVER_CLASS = "accessibility.TextOnlyCrawlCoverReceiver"
TEXT_ONLY_COVER_VISIBLE_EXTRA = "visible"
ACCESSIBILITY_RECOVERY_ACTION = "com.siksik.agent.action.RECOVER_ACCESSIBILITY"
ACCESSIBILITY_SUSPEND_ACTION = "com.siksik.agent.action.SUSPEND_ACCESSIBILITY"
ACCESSIBILITY_PROBE_ACTION = "com.siksik.agent.action.PROBE_ACCESSIBILITY_BINDING"
ACCESSIBILITY_PROBE_PREFIX = "SIKSIK_A11Y_V1"
ACCESSIBILITY_RECOVERY_RECEIVER_CLASS = "accessibility.AccessibilityRecoveryReceiver"
ProcessRunner = Callable[..., Awaitable[ProcessResult]]


@dataclass(frozen=True, slots=True)
class AdbDevice:
    serial: str
    state: str
    product: str | None = None
    model: str | None = None
    device: str | None = None
    transport_id: str | None = None
    usb: str | None = None

    @property
    def redacted_ref(self) -> str:
        digest = hashlib.sha256(f"siksik-adb:{self.serial}".encode("utf-8")).hexdigest()
        return f"android:{digest[:20]}"


@dataclass(frozen=True, slots=True)
class AndroidDeviceCapabilities:
    device: AdbDevice
    manufacturer: str
    model: str
    android_release: str
    api_level: int
    package_installed: bool


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    installed: bool
    apk_path: str | None = None
    version_code: int | None = None
    version_name: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceReadiness:
    boot_completed: bool
    unlocked: bool | None
    available_data_bytes: int | None


class PermissionState(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNSUPPORTED = "unsupported"


class SpecialAccessKind(str, Enum):
    ACCESSIBILITY = "accessibility"
    NOTIFICATION_LISTENER = "notification_listener"
    MANAGE_ALL_FILES = "manage_all_files"


class SpecialAccessState(str, Enum):
    GRANTED = "granted"
    NOT_GRANTED = "not_granted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


def parse_runtime_permission_dump(
    output: str,
    permission: str,
    user_id: int,
) -> PermissionState:
    scopes = []
    for match in re.finditer(
        rf"(?ms)^[ \t]*User\s+{user_id}:[^\r\n]*\r?\n"
        rf"(.*?)(?=^[ \t]*User\s+\d+:[^\r\n]*$|\Z)",
        output,
    ):
        scopes.append(match.group(1))

    for scope in reversed(scopes):
        state_match = re.search(
            rf"(?m)^\s*{re.escape(permission)}:\s*granted=(true|false)\b",
            scope,
        )
        if state_match is not None:
            return (
                PermissionState.GRANTED
                if state_match.group(1) == "true"
                else PermissionState.DENIED
            )
    requested = re.search(rf"(?m)^\s*{re.escape(permission)}\s*$", output)
    return PermissionState.DENIED if requested is not None else PermissionState.UNSUPPORTED


def parse_runtime_permission_probe(output: str) -> dict[str, PermissionState] | None:
    match = re.search(
        rf"{RUNTIME_PERMISSION_PROBE_PREFIX}"
        rf"(?:;android\.permission\.[A-Z0-9_]+=(?:granted|denied|unsupported))+",
        output,
    )
    if match is None:
        return None
    states: dict[str, PermissionState] = {}
    for raw_entry in match.group(0).split(";")[1:]:
        permission, raw_state = raw_entry.split("=", 1)
        if permission in states or permission not in RUNTIME_PERMISSION_NAMES:
            return None
        states[permission] = PermissionState(raw_state)
    return states if states else None


def parse_text_only_cover_probe(output: str) -> str | None:
    match = re.search(
        rf"{TEXT_ONLY_COVER_PROBE_PREFIX};status=(shown|hidden)",
        output,
    )
    return match.group(1) if match is not None else None


def parse_accessibility_recovery_probe(output: str) -> str | None:
    match = re.search(
        rf"{ACCESSIBILITY_PROBE_PREFIX};status=(bound|crashed|unbound|denied)",
        output,
    )
    return match.group(1) if match is not None else None


ACCESSIBILITY_SERVICE_LABELS = {
    "Enabled services": "enabled",
    "Bound services": "bound",
    "Binding services": "binding",
    "Crashed services": "crashed",
}
ACCESSIBILITY_BINDING_SETTLE_SECONDS = 8.0
ACCESSIBILITY_BINDING_POLL_SECONDS = 0.3
ACCESSIBILITY_UNBIND_SETTLE_SECONDS = 2.0
ACCESSIBILITY_REBIND_PAUSE_SECONDS = 0.5


class AccessibilityBindingState(str, Enum):
    BOUND = "bound"
    CRASHED = "crashed"
    UNBOUND = "unbound"
    UNAVAILABLE = "unavailable"


def _normalize_accessibility_component_token(raw: str) -> str | None:
    token = raw.strip()
    while token.startswith("{") and token.endswith("}"):
        inner = token[1:-1].strip()
        if not inner:
            return None
        token = inner
    if not token:
        return None
    try:
        return validate_component_name(token)
    except AcquisitionError:
        if token.count("/") != 1:
            return None
        package_name, raw_class = token.split("/", 1)
        if not SAFE_PACKAGE.fullmatch(package_name):
            return None
        full_class = (
            f"{package_name}{raw_class}" if raw_class.startswith(".") else raw_class
        )
        if not SAFE_CLASS.fullmatch(full_class):
            return None
        return token


def _parse_accessibility_brace_body(body: str) -> set[str]:
    fragment = body.strip()
    if not fragment or fragment in {"{}", "{{}}"}:
        return set()
    while fragment.startswith("{") and fragment.endswith("}"):
        inner = fragment[1:-1].strip()
        if not inner:
            return set()
        fragment = inner
    components: set[str] = set()
    for part in fragment.split(","):
        normalized = _normalize_accessibility_component_token(part)
        if normalized is not None:
            components.add(normalized)
    return components


def parse_accessibility_service_sets(text: str) -> dict[str, set[str]]:
    sets: dict[str, set[str]] = {
        "enabled": set(),
        "bound": set(),
        "bound_unknown": set(),
        "binding": set(),
        "crashed": set(),
    }
    for line in text.splitlines():
        stripped = line.strip()
        for label, key in ACCESSIBILITY_SERVICE_LABELS.items():
            if label not in stripped:
                continue
            body = stripped.split(label, 1)[1].lstrip(":").strip()
            parsed = _parse_accessibility_brace_body(body)
            sets[key].update(parsed)
            if (
                key == "bound"
                and not parsed
                and body not in {"", "{}", "{{}}"}
            ):
                # MIUI 12 emits Service[label=…] without a component name.
                # Retain the descriptor so a sole-enabled-service inference can
                # be made without guessing when multiple services are enabled.
                sets["bound_unknown"].add(body)
            break
    return sets


def accessibility_binding_state_for_component(
    service_sets: dict[str, set[str]],
    component: str,
) -> AccessibilityBindingState:
    expected = validate_component_name(component)
    # Android can retain a stale Crashed-services entry after the same service
    # has rebound. A current positive binder entry must always win.
    if expected in service_sets.get("bound", set()):
        return AccessibilityBindingState.BOUND
    if (
        expected in service_sets.get("enabled", set())
        and service_sets.get("enabled", set()) == {expected}
        and service_sets.get("bound_unknown", set())
    ):
        return AccessibilityBindingState.BOUND
    if expected in service_sets.get("crashed", set()):
        return AccessibilityBindingState.CRASHED
    # "Binding services" is a transitional set, not proof that the service is
    # connected. The bounded waiter will keep polling until Bound appears.
    return AccessibilityBindingState.UNBOUND


def parse_package_dump(output: str) -> tuple[int | None, str | None]:
    version_code: int | None = None
    version_name: str | None = None
    code_match = re.search(r"(?m)^\s*versionCode=(\d+)\b", output)
    name_match = re.search(r"(?m)^\s*versionName=([^\r\n]+)", output)
    if code_match is not None:
        version_code = int(code_match.group(1))
    if name_match is not None:
        raw_name = name_match.group(1).strip()
        version_name = raw_name if raw_name and raw_name != "null" else None
    return version_code, version_name


def parse_available_data_bytes(output: str) -> int | None:
    lines = [line.split() for line in output.splitlines() if line.strip()]
    for parts in reversed(lines):
        if len(parts) < 4 or not parts[-3].isdigit():
            continue
        return int(parts[-3]) * 1024
    return None


def parse_device_unlocked(output: str) -> bool | None:
    lowered = output.casefold()
    locked_markers = (
        "mshowinglockscreen=true",
        "mkeyguardshowing=true",
        "isstatusbarkeyguard=true",
        "showing=true occluded=false",
    )
    unlocked_markers = (
        "mshowinglockscreen=false",
        "mkeyguardshowing=false",
        "isstatusbarkeyguard=false",
        "showing=false",
    )
    if any(marker in lowered for marker in locked_markers):
        return False
    if any(marker in lowered for marker in unlocked_markers):
        return True
    return None


def validate_serial(serial: str) -> str:
    if not isinstance(serial, str) or not SAFE_SERIAL.fullmatch(serial):
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Serial perangkat Android tidak valid.",
        )
    return serial


def validate_package_name(package_name: str) -> str:
    if not isinstance(package_name, str) or not SAFE_PACKAGE.fullmatch(package_name):
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Nama package tidak valid.")
    return package_name


def validate_component_name(component: str, package_name: str | None = None) -> str:
    if not isinstance(component, str) or component.count("/") != 1:
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Nama komponen tidak valid.",
        )
    component_package, raw_class = component.split("/", 1)
    validate_package_name(component_package)
    if package_name is not None and component_package != validate_package_name(package_name):
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Komponen bukan milik package yang diharapkan.",
        )
    full_class = (
        f"{component_package}{raw_class}" if raw_class.startswith(".") else raw_class
    )
    if not SAFE_CLASS.fullmatch(full_class) or not full_class.startswith(
        f"{component_package}."
    ):
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Nama komponen tidak valid.")
    return f"{component_package}/{full_class}"


def resolve_adb(configured_path: str) -> Path:
    if not configured_path or "\x00" in configured_path:
        raise acquisition_error(ErrorCategory.ADB_NOT_FOUND, "Executable ADB tidak valid.")
    expanded = Path(configured_path).expanduser()
    candidates: list[Path] = []
    if expanded.parent == Path("."):
        discovered = shutil.which(configured_path)
        if discovered:
            candidates.append(Path(discovered))
        if configured_path == "adb":
            for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
                if os.environ.get(variable):
                    candidates.append(Path(os.environ[variable]) / "platform-tools" / "adb")
            candidates.extend(
                (
                    Path.home()
                    / "Library"
                    / "Android"
                    / "sdk"
                    / "platform-tools"
                    / "adb",
                    Path("/opt/homebrew/share/android-commandlinetools/platform-tools/adb"),
                    Path("/usr/local/share/android-commandlinetools/platform-tools/adb"),
                )
            )
    else:
        candidates.append(expanded)
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    raise acquisition_error(ErrorCategory.ADB_NOT_FOUND, "Executable ADB tidak ditemukan.")


def _running_under_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").casefold()
    except OSError:
        return False


def _wsl_default_gateway() -> str | None:
    try:
        import subprocess

        proc = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split()
    if len(parts) < 3:
        return None
    gateway = parts[2].strip()
    if not gateway or gateway in {"127.0.0.1", "localhost", "::1"}:
        return None
    return gateway


def resolve_agent_forward_host(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    env_host = os.environ.get("SADT_AGENT_FORWARD_HOST", "").strip()
    if env_host:
        return env_host
    adb_socket = os.environ.get("ADB_SERVER_SOCKET", "")
    if adb_socket.startswith("tcp:"):
        host = adb_socket[4:].rsplit(":", 1)[0].strip()
        if host and host not in {"127.0.0.1", "localhost", "::1"}:
            return host
    if _running_under_wsl():
        gateway = _wsl_default_gateway()
        if gateway:
            return gateway
    return "127.0.0.1"


def parse_devices(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            serial = validate_serial(parts[0])
        except AcquisitionError:
            continue
        values: dict[str, str] = {}
        for field in parts[2:]:
            if ":" not in field:
                continue
            key, value = field.split(":", 1)
            values[key] = value
        devices.append(
            AdbDevice(
                serial=serial,
                state=parts[1],
                product=values.get("product"),
                model=values.get("model"),
                device=values.get("device"),
                transport_id=values.get("transport_id"),
                usb=values.get("usb"),
            )
        )
    return devices


def select_device(devices: Sequence[AdbDevice], requested_serial: str | None) -> AdbDevice:
    if requested_serial is not None:
        serial = validate_serial(requested_serial)
        selected = next((item for item in devices if item.serial == serial), None)
        if selected is None:
            raise acquisition_error(
                ErrorCategory.ADB_NO_DEVICE,
                "Perangkat Android yang dipilih tidak ditemukan.",
                retryable=True,
            )
    else:
        ready = [item for item in devices if item.state == "device"]
        if not ready:
            if any(item.state == "unauthorized" for item in devices):
                raise acquisition_error(
                    ErrorCategory.ADB_UNAUTHORIZED,
                    "Otorisasi USB debugging pada perangkat masih diperlukan.",
                    retryable=True,
                )
            if any(item.state == "offline" for item in devices):
                raise acquisition_error(
                    ErrorCategory.ADB_OFFLINE,
                    "Perangkat Android terdeteksi offline.",
                    retryable=True,
                )
            raise acquisition_error(
                ErrorCategory.ADB_NO_DEVICE,
                "Tidak ada perangkat Android yang siap.",
                retryable=True,
            )
        if len(ready) > 1:
            raise acquisition_error(
                ErrorCategory.ADB_MULTIPLE_DEVICES,
                "Pilih satu perangkat Android secara eksplisit.",
            )
        selected = ready[0]

    if selected.state == "unauthorized":
        raise acquisition_error(
            ErrorCategory.ADB_UNAUTHORIZED,
            "Otorisasi USB debugging pada perangkat masih diperlukan.",
            retryable=True,
        )
    if selected.state == "offline":
        raise acquisition_error(
            ErrorCategory.ADB_OFFLINE,
            "Perangkat Android terdeteksi offline.",
            retryable=True,
        )
    if selected.state != "device":
        raise acquisition_error(
            ErrorCategory.ADB_NO_DEVICE,
            "Perangkat Android belum siap.",
            retryable=True,
        )
    return selected


class AsyncAdbTransport:
    def __init__(
        self,
        configured_path: str = "adb",
        *,
        timeout_seconds: float = 30.0,
        output_limit_bytes: int = 1024 * 1024,
        runner: ProcessRunner = run_process,
    ) -> None:
        if timeout_seconds <= 0 or output_limit_bytes <= 0:
            raise ValueError("ADB timeout and output limit must be positive")
        self._configured_path = configured_path
        self._timeout_seconds = timeout_seconds
        self._output_limit_bytes = output_limit_bytes
        self._runner = runner
        self._resolved_path: Path | None = None
        self._runtime_grant_shell_support: dict[str, bool] = {}
        self._prefer_no_streaming: dict[str, bool] = {}
        self._runtime_permission_state_cache: dict[
            tuple[str, str, int],
            tuple[float, dict[str, PermissionState]],
        ] = {}

    @property
    def executable(self) -> Path:
        if self._resolved_path is None:
            self._resolved_path = resolve_adb(self._configured_path)
        return self._resolved_path

    async def run(
        self,
        serial: str | None,
        args: Sequence[str],
        *,
        operation: str,
        timeout: float | None = None,
        check: bool = True,
    ) -> ProcessResult:
        if not args:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Perintah ADB kosong.")
        argv = [str(self.executable)]
        if serial is not None:
            argv.extend(["-s", validate_serial(serial)])
        argv.extend(str(value) for value in args)
        result = await self._runner(
            argv,
            timeout=timeout or self._timeout_seconds,
            check=False,
            output_limit_bytes=self._output_limit_bytes,
            not_found_category=ErrorCategory.ADB_NOT_FOUND,
            timeout_category=ErrorCategory.ADB_TIMEOUT,
            failure_category=ErrorCategory.ADB_COMMAND_FAILED,
            operation=operation,
        )
        if check and result.returncode != 0:
            raise self._categorized_command_error(result, operation)
        return result

    @staticmethod
    def _categorized_command_error(result: ProcessResult, operation: str) -> AcquisitionError:
        output = f"{result.stdout}\n{result.stderr}".casefold()
        if "unauthorized" in output:
            return acquisition_error(
                ErrorCategory.ADB_UNAUTHORIZED,
                "Otorisasi USB debugging pada perangkat masih diperlukan.",
                retryable=True,
                dependency_exit_code=result.returncode,
            )
        if "offline" in output:
            return acquisition_error(
                ErrorCategory.ADB_OFFLINE,
                "Perangkat Android terdeteksi offline.",
                retryable=True,
                dependency_exit_code=result.returncode,
            )
        if "no devices" in output or "device not found" in output:
            return acquisition_error(
                ErrorCategory.ADB_NO_DEVICE,
                "Perangkat Android terputus.",
                retryable=True,
                dependency_exit_code=result.returncode,
            )
        return acquisition_error(
            ErrorCategory.ADB_COMMAND_FAILED,
            f"Operasi ADB {operation} gagal.",
            dependency_exit_code=result.returncode,
        )

    async def list_devices(self) -> list[AdbDevice]:
        result = await self.run(
            None,
            ["devices", "-l"],
            operation="device_discovery",
        )
        return parse_devices(result.stdout)

    async def select_device(self, requested_serial: str | None) -> AdbDevice:
        return select_device(await self.list_devices(), requested_serial)

    async def getprop(self, serial: str, property_name: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", property_name):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Nama properti Android tidak valid.",
            )
        result = await self.run(
            serial,
            ["shell", "getprop", property_name],
            operation="device_property_probe",
        )
        return result.stdout.strip()

    async def capabilities(
        self,
        serial: str,
        *,
        package_name: str,
        minimum_api: int = 26,
    ) -> AndroidDeviceCapabilities:
        validate_package_name(package_name)
        device = await self.select_device(serial)
        manufacturer = await self.getprop(serial, "ro.product.manufacturer")
        model = await self.getprop(serial, "ro.product.model")
        release = await self.getprop(serial, "ro.build.version.release")
        api_raw = await self.getprop(serial, "ro.build.version.sdk")
        if not api_raw.isdigit():
            raise acquisition_error(
                ErrorCategory.ADB_COMMAND_FAILED,
                "Perangkat mengembalikan level API Android yang tidak valid.",
            )
        api_level = int(api_raw)
        if api_level < minimum_api:
            raise acquisition_error(
                ErrorCategory.DEVICE_UNSUPPORTED,
                f"Android API {api_level} tidak didukung; minimum API {minimum_api}.",
            )
        package = await self.run(
            serial,
            ["shell", "pm", "path", package_name],
            operation="agent_installation_probe",
            check=False,
        )
        return AndroidDeviceCapabilities(
            device=device,
            manufacturer=manufacturer,
            model=model,
            android_release=release,
            api_level=api_level,
            package_installed=package.returncode == 0 and "package:" in package.stdout,
        )

    async def install_apk(
        self,
        serial: str,
        apk_path: Path,
        *,
        grant_runtime_permissions: bool = True,
        allow_test_packages: bool = False,
        replace_package_on_uid_mismatch: str | None = None,
        timeout: float = 180.0,
        approval_poll_seconds: float = 2.0,
        on_user_restricted: Callable[[], Awaitable[None]] | None = None,
    ) -> ApkInstallOutcome:
        path = apk_path.expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Artifact APK tidak valid.")
        if timeout <= 0:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas waktu instalasi APK tidak valid.",
            )
        if approval_poll_seconds <= 0:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Interval polling persetujuan instalasi tidak valid.",
            )
        if not 0 < path.stat().st_size <= 250 * 1024 * 1024:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Ukuran APK tidak valid.")
        if replace_package_on_uid_mismatch is not None:
            validate_package_name(replace_package_on_uid_mismatch)
        await self.select_device(serial)
        await self._ensure_install_readiness(serial)
        manufacturer, brand = await self._oem_identity(serial)
        api_level = await self._api_level(serial)
        guidance = oem_install_guidance(manufacturer=manufacturer, brand=brand)
        self._clear_runtime_permission_cache(serial)
        initial_attempt = initial_install_attempt(
            api_level=api_level,
            grant_runtime_permissions=grant_runtime_permissions,
            allow_test_packages=allow_test_packages,
            manufacturer=manufacturer,
            brand=brand,
            prefer_no_streaming=self._prefer_no_streaming.get(serial, False),
            runtime_grant_supported=self._runtime_grant_shell_support.get(serial, True),
        )
        logger.info(
            "agent_install_strategy_selected",
            extra={
                "strategy": initial_attempt.name,
                "api_level": api_level,
                "manufacturer": manufacturer,
                "brand": brand,
            },
        )
        deadline = time.monotonic() + timeout
        attempt_count = 0
        attempt = initial_attempt
        result: ProcessResult | None = None
        failure = InstallFailureKind.UNKNOWN
        restricted_rounds = 0
        while True:
            result, attempt, round_attempts, failure = await self._run_install_attempts(
                serial,
                path,
                attempt,
                deadline,
            )
            attempt_count += round_attempts
            if failure == InstallFailureKind.NONE:
                return ApkInstallOutcome(
                    strategy=attempt.name,
                    attempt_count=attempt_count,
                    runtime_granted_during_install=attempt.grant_runtime_permissions,
                )
            if failure == InstallFailureKind.STREAMING_UNAVAILABLE:
                self._prefer_no_streaming[serial] = True
            if failure != InstallFailureKind.USER_RESTRICTED:
                break
            restricted_rounds += 1
            await self.select_device(serial)
            await self._ensure_install_readiness(serial)
            remaining = deadline - time.monotonic()
            if remaining <= approval_poll_seconds:
                break
            logger.warning(
                "agent_install_awaiting_user",
                extra={
                    "retry_count": restricted_rounds,
                    "api_level": api_level,
                    "manufacturer": manufacturer,
                    "brand": brand,
                },
            )
            if on_user_restricted is not None:
                await on_user_restricted()
            await asyncio.sleep(min(approval_poll_seconds, remaining))
            attempt = initial_install_attempt(
                api_level=api_level,
                grant_runtime_permissions=grant_runtime_permissions,
                allow_test_packages=allow_test_packages,
                manufacturer=manufacturer,
                brand=brand,
                prefer_no_streaming=self._prefer_no_streaming.get(serial, False),
                runtime_grant_supported=self._runtime_grant_shell_support.get(
                    serial,
                    True,
                ),
            )
        assert result is not None
        if (
            failure
            in {
                InstallFailureKind.UPDATE_INCOMPATIBLE,
                InstallFailureKind.UID_INCOMPATIBLE,
            }
            and replace_package_on_uid_mismatch is not None
        ):
            remaining = self._install_time_remaining(deadline)
            await self._uninstall_package_for_reinstall(
                serial,
                replace_package_on_uid_mismatch,
                timeout=remaining,
            )
            result, attempt, migration_attempts, failure = await self._run_install_attempts(
                serial,
                path,
                attempt,
                deadline,
            )
            attempt_count += migration_attempts
            if failure == InstallFailureKind.NONE:
                return ApkInstallOutcome(
                    strategy=attempt.name,
                    attempt_count=attempt_count,
                    runtime_granted_during_install=attempt.grant_runtime_permissions,
                )
        if failure == InstallFailureKind.UPDATE_INCOMPATIBLE:
            raise acquisition_error(
                ErrorCategory.AGENT_SIGNATURE_MISMATCH,
                "APK agent yang terpasang memakai signature berbeda.",
            )
        if failure == InstallFailureKind.UID_INCOMPATIBLE:
            raise acquisition_error(
                ErrorCategory.AGENT_SIGNATURE_MISMATCH,
                "APK memakai UID yang tidak kompatibel dengan package terpasang.",
            )
        if failure == InstallFailureKind.INSUFFICIENT_STORAGE:
            raise acquisition_error(
                ErrorCategory.STORAGE_UNAVAILABLE,
                "Penyimpanan perangkat tidak cukup untuk memasang agent.",
            )
        if failure == InstallFailureKind.VERSION_DOWNGRADE:
            raise acquisition_error(
                ErrorCategory.AGENT_VERSION_MISMATCH,
                "Versi APK agent terpasang lebih baru dari artifact SIKSIK.",
            )
        if failure == InstallFailureKind.DEVICE_INCOMPATIBLE:
            raise acquisition_error(
                ErrorCategory.DEVICE_UNSUPPORTED,
                "Perangkat Android tidak kompatibel dengan APK agent SIKSIK.",
            )
        if failure == InstallFailureKind.USER_RESTRICTED:
            raise acquisition_error(
                ErrorCategory.ACCESS_DENIED,
                guidance,
                retryable=True,
            )
        if failure == InstallFailureKind.DEVICE_POLICY:
            raise acquisition_error(
                ErrorCategory.ACCESS_DENIED,
                "Kebijakan perangkat memblokir instalasi APK melalui ADB.",
            )
        if failure == InstallFailureKind.INVALID_APK:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Artifact APK agent tidak dapat diproses oleh Package Manager Android.",
            )
        categorized = self._categorized_command_error(result, "agent_install")
        if categorized.category != ErrorCategory.ADB_COMMAND_FAILED:
            raise categorized
        raise acquisition_error(
            ErrorCategory.AGENT_INSTALL_FAILED,
            "Instalasi APK agent gagal.",
            dependency_exit_code=result.returncode,
        )

    async def _ensure_install_readiness(self, serial: str) -> None:
        readiness = await self.device_readiness(serial)
        if not readiness.boot_completed:
            raise acquisition_error(
                ErrorCategory.ADB_OFFLINE,
                "Perangkat Android belum selesai melakukan boot.",
                retryable=True,
            )
        if readiness.unlocked is False:
            raise acquisition_error(
                ErrorCategory.DEVICE_LOCKED,
                "Buka kunci perangkat Android sebelum instalasi APK.",
                retryable=True,
            )

    async def _oem_identity(self, serial: str) -> tuple[str | None, str | None]:
        manufacturer = None
        brand = None
        try:
            manufacturer = await self.getprop(serial, "ro.product.manufacturer")
        except AcquisitionError:
            pass
        try:
            brand = await self.getprop(serial, "ro.product.brand")
        except AcquisitionError:
            pass
        return (
            manufacturer.strip() if manufacturer else None,
            brand.strip() if brand else None,
        )

    async def _api_level(self, serial: str) -> int | None:
        try:
            raw = await self.getprop(serial, "ro.build.version.sdk")
        except AcquisitionError:
            return None
        return int(raw) if raw.isdigit() else None

    async def _run_install_attempts(
        self,
        serial: str,
        path: Path,
        initial_attempt: InstallAttempt,
        deadline: float,
    ) -> tuple[ProcessResult, InstallAttempt, int, InstallFailureKind]:
        attempt = initial_attempt
        attempt_count = 0
        while True:
            attempt_count += 1
            remaining = self._install_time_remaining(deadline)
            result = await self.run(
                serial,
                attempt.argv(path),
                operation="agent_install",
                timeout=remaining,
                check=False,
            )
            evaluation = evaluate_install_result(
                result.returncode,
                result.stdout,
                result.stderr,
            )
            if evaluation.failure == InstallFailureKind.RUNTIME_GRANT_UNSUPPORTED:
                self._runtime_grant_shell_support[serial] = False
            if evaluation.failure == InstallFailureKind.STREAMING_UNAVAILABLE:
                self._prefer_no_streaming[serial] = True
            if evaluation.success:
                return result, attempt, attempt_count, InstallFailureKind.NONE
            fallback = next_install_attempt(attempt, evaluation.failure)
            if fallback is None:
                return result, attempt, attempt_count, evaluation.failure
            logger.info(
                "agent_install_fallback",
                extra={
                    "state": evaluation.failure.value,
                    "fallback": fallback.name,
                    "retry_count": attempt_count,
                },
            )
            attempt = fallback

    @staticmethod
    def _install_time_remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise acquisition_error(
                ErrorCategory.ADB_TIMEOUT,
                "Instalasi APK agent melewati batas waktu.",
                retryable=True,
            )
        return remaining

    async def _uninstall_package_for_reinstall(
        self,
        serial: str,
        package_name: str,
        *,
        timeout: float,
    ) -> None:
        """Remove a package before reinstalling across signature/UID mismatch.

        OEM quirk (esp. MIUI): ``adb uninstall`` may return
        ``DELETE_FAILED_INTERNAL_ERROR`` even when the package is already gone.
        Treat removal as successful only after ``pm path`` confirms absence.
        """
        validate_package_name(package_name)
        deadline = time.monotonic() + max(timeout, 1.0)
        attempts: list[list[str]] = [
            ["uninstall", package_name],
            ["shell", "pm", "uninstall", "--user", "0", package_name],
            ["shell", "cmd", "package", "uninstall", "--user", "0", package_name],
        ]
        last: ProcessResult | None = None
        for argv in attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            last = await self.run(
                serial,
                argv,
                operation="incompatible_package_uninstall",
                timeout=remaining,
                check=False,
            )
            blob = f"{last.stdout}\n{last.stderr}".casefold()
            if last.returncode == 0 or any(
                marker in blob
                for marker in (
                    "success",
                    "unknown package",
                    "not installed",
                )
            ):
                break
            # MIUI often emits DELETE_FAILED_INTERNAL_ERROR for ghost/absent pkgs.
            if "delete_failed_internal_error" in blob:
                break

        installed = await self.inspect_package(serial, package_name)
        if not installed.installed:
            logger.info(
                "incompatible_package_uninstalled",
                extra={"package": package_name},
            )
            return

        detail = ""
        if last is not None:
            detail = (last.stdout or last.stderr or "").strip()[:240]
        raise acquisition_error(
            ErrorCategory.AGENT_SIGNATURE_MISMATCH,
            (
                f"Gagal menghapus package lama {package_name} sebelum reinstall "
                f"(signature/UID tidak cocok). Hapus manual di Settings → Apps, "
                f"atau samakan debug.keystore dengan mesin yang memasang APK sebelumnya."
                + (f" Detail ADB: {detail}" if detail else "")
            ),
            dependency_exit_code=None if last is None else last.returncode,
        )

    async def inspect_package(self, serial: str, package_name: str) -> InstalledPackage:
        validate_package_name(package_name)
        await self.select_device(serial)
        path_result = await self.run(
            serial,
            ["shell", "pm", "path", package_name],
            operation="agent_package_path_probe",
            check=False,
        )
        paths = [
            line.removeprefix("package:").strip()
            for line in path_result.stdout.splitlines()
            if line.startswith("package:")
        ]
        base_path = next((path for path in paths if path.endswith("/base.apk")), None)
        if path_result.returncode != 0 or base_path is None:
            return InstalledPackage(installed=False)
        dump = await self.run(
            serial,
            ["shell", "dumpsys", "package", package_name],
            operation="agent_package_metadata_probe",
        )
        version_code, version_name = parse_package_dump(dump.stdout)
        if version_code is None:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Metadata package Android agent tidak dapat diverifikasi.",
            )
        return InstalledPackage(
            installed=True,
            apk_path=base_path,
            version_code=version_code,
            version_name=version_name,
        )

    async def package_exists(self, serial: str, package_name: str) -> bool:
        validate_package_name(package_name)
        result = await self.run(
            serial,
            ["shell", "pm", "path", package_name],
            operation="target_package_probe",
            check=False,
        )
        return result.returncode == 0 and any(
            line.startswith("package:") for line in result.stdout.splitlines()
        )

    async def run_instrumentation(
        self,
        serial: str,
        *,
        runner_component: str,
        test_class: str,
        arguments: Mapping[str, str | int],
        timeout: float,
    ) -> ProcessResult:
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{1,254}/[A-Za-z][A-Za-z0-9_.]{1,254}", runner_component)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{1,254}", test_class)
            or timeout <= 0
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Konfigurasi instrumentation tidak valid.",
            )
        args = ["shell", "am", "instrument", "-w", "-r"]
        for key, value in arguments.items():
            if not SAFE_EXTRA_KEY.fullmatch(key):
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Nama argument instrumentation tidak valid.",
                )
            text = str(value)
            if not text or "\x00" in text or len(text) > 1024:
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Nilai argument instrumentation tidak valid.",
                )
            args.extend(["-e", key, text])
        args.extend(["-e", "class", test_class, runner_component])
        return await self.run(
            serial,
            args,
            operation="social_ui_automation",
            timeout=timeout,
            check=False,
        )

    async def pull_installed_apk(
        self,
        serial: str,
        remote_path: str,
        destination: Path,
        *,
        timeout: float = 120.0,
    ) -> None:
        if (
            not remote_path.startswith("/data/app/")
            or not remote_path.endswith("/base.apk")
            or not re.fullmatch(r"[A-Za-z0-9_./=+~-]{16,1024}", remote_path)
        ):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Path package tidak valid.")
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        pull_path = remote_path
        last_detail = ""
        last_code = 1
        for attempt in range(1, 4):
            if target.exists():
                target.unlink(missing_ok=True)
            result = await self.run(
                serial,
                ["pull", pull_path, str(target)],
                operation="agent_installed_apk_pull",
                timeout=timeout,
                check=False,
            )
            if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                return
            last_code = result.returncode
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            last_detail = lines[-1] if lines else f"exit={result.returncode}"
            logger.warning(
                "agent_installed_apk_pull_retry",
                extra={
                    "attempt": attempt,
                    "returncode": result.returncode,
                    "stderr": (result.stderr or "")[:500],
                    "pull_path": pull_path,
                },
            )
            if attempt >= 3:
                break
            await asyncio.sleep(0.4 * attempt)
            # Some OEM adb pulls flake on /data/app; stage then pull.
            staged = "/data/local/tmp/siksik_agent_base.apk"
            stage = await self.run(
                serial,
                ["shell", "cp", remote_path, staged],
                operation="agent_installed_apk_stage",
                timeout=min(timeout, 60.0),
                check=False,
            )
            if stage.returncode == 0:
                pull_path = staged
        raise acquisition_error(
            ErrorCategory.ADB_COMMAND_FAILED,
            f"Operasi ADB agent_installed_apk_pull gagal: {last_detail[:240]}",
            dependency_exit_code=last_code,
        )

    async def device_readiness(self, serial: str) -> DeviceReadiness:
        await self.select_device(serial)
        boot = await self.getprop(serial, "sys.boot_completed")
        window = await self.run(
            serial,
            ["shell", "dumpsys", "window", "policy"],
            operation="device_lock_probe",
            check=False,
        )
        storage = await self.run(
            serial,
            ["shell", "df", "-k", "/data"],
            operation="device_storage_probe",
            check=False,
        )
        return DeviceReadiness(
            boot_completed=boot == "1",
            unlocked=parse_device_unlocked(window.stdout) if window.returncode == 0 else None,
            available_data_bytes=parse_available_data_bytes(storage.stdout)
            if storage.returncode == 0
            else None,
        )

    async def current_user_id(self, serial: str) -> int:
        result = await self.run(
            serial,
            ["shell", "am", "get-current-user"],
            operation="android_user_probe",
        )
        value = result.stdout.strip()
        if not value.isdigit() or not 0 <= int(value) <= 100_000:
            raise acquisition_error(
                ErrorCategory.ADB_COMMAND_FAILED,
                "Android user aktif tidak dapat diverifikasi.",
            )
        return int(value)

    async def runtime_permission_state(
        self,
        serial: str,
        package_name: str,
        permission: str,
        user_id: int,
    ) -> PermissionState:
        validate_package_name(package_name)
        if not SAFE_PERMISSION.fullmatch(permission):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Nama permission tidak valid.")
        if not 0 <= user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        states = await self._runtime_permission_snapshot(
            serial,
            package_name,
            user_id,
            permission,
        )
        return states[permission]

    async def grant_runtime_permission(
        self,
        serial: str,
        package_name: str,
        permission: str,
        user_id: int,
    ) -> PermissionState:
        validate_package_name(package_name)
        current = await self.runtime_permission_state(
            serial,
            package_name,
            permission,
            user_id,
        )
        if current == PermissionState.GRANTED:
            return current
        if self._runtime_grant_shell_support.get(serial) is False:
            return current
        result = await self.run(
            serial,
            [
                "shell",
                "pm",
                "grant",
                "--user",
                str(user_id),
                package_name,
                permission,
            ],
            operation="runtime_permission_grant",
            check=False,
        )
        if result.returncode != 0:
            text = f"{result.stdout}\n{result.stderr}".casefold()
            if "not a changeable permission" in text or "has not requested permission" in text:
                return PermissionState.UNSUPPORTED
            if "securityexception" in text and "grant_runtime_permissions" in text:
                self._runtime_grant_shell_support[serial] = False
                logger.info(
                    "agent_runtime_grant_shell_unsupported",
                    extra={"state": "interactive_storage_required"},
                )
                return PermissionState.DENIED
            categorized = self._categorized_command_error(result, "runtime_permission_grant")
            if categorized.category != ErrorCategory.ADB_COMMAND_FAILED:
                raise categorized
            return PermissionState.DENIED
        self._runtime_grant_shell_support[serial] = True
        self._clear_runtime_permission_cache(serial, package_name, user_id)
        return await self.runtime_permission_state(
            serial,
            package_name,
            permission,
            user_id,
        )

    async def _runtime_permission_snapshot(
        self,
        serial: str,
        package_name: str,
        user_id: int,
        required_permission: str,
    ) -> dict[str, PermissionState]:
        cache_key = (serial, package_name, user_id)
        now = time.monotonic()
        cached = self._runtime_permission_state_cache.get(cache_key)
        if (
            cached is not None
            and now - cached[0] <= RUNTIME_PERMISSION_PROBE_TTL_SECONDS
            and required_permission in cached[1]
        ):
            return cached[1]

        component = validate_component_name(
            f"{package_name}/{package_name}.{RUNTIME_PERMISSION_PROBE_CLASS}",
            package_name,
        )
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(user_id),
                "--include-stopped-packages",
                "-a",
                RUNTIME_PERMISSION_PROBE_ACTION,
                "-n",
                component,
            ],
            operation="runtime_permission_agent_probe",
            check=False,
        )
        states = (
            parse_runtime_permission_probe(f"{result.stdout}\n{result.stderr}")
            if result.returncode == 0
            else None
        )
        if states is not None and required_permission not in states:
            states = None
        if states is None:
            if result.returncode != 0:
                categorized = self._categorized_command_error(
                    result,
                    "runtime_permission_agent_probe",
                )
                if categorized.category != ErrorCategory.ADB_COMMAND_FAILED:
                    raise categorized
            fallback = await self.run(
                serial,
                ["shell", "dumpsys", "package", package_name],
                operation="runtime_permission_probe",
                check=False,
            )
            if fallback.returncode != 0:
                raise self._categorized_command_error(fallback, "runtime_permission_probe")
            permissions = tuple(dict.fromkeys((*RUNTIME_PERMISSION_NAMES, required_permission)))
            states = {
                permission: parse_runtime_permission_dump(
                    fallback.stdout,
                    permission,
                    user_id,
                )
                for permission in permissions
            }
        self._runtime_permission_state_cache[cache_key] = (time.monotonic(), states)
        return states

    def _clear_runtime_permission_cache(
        self,
        serial: str,
        package_name: str | None = None,
        user_id: int | None = None,
    ) -> None:
        stale = [
            key
            for key in self._runtime_permission_state_cache
            if key[0] == serial
            and (package_name is None or key[1] == package_name)
            and (user_id is None or key[2] == user_id)
        ]
        for key in stale:
            self._runtime_permission_state_cache.pop(key, None)

    async def open_runtime_permission_settings(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> None:
        validate_package_name(package_name)
        if user_id is not None and not 0 <= user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        args = ["shell", "am", "start"]
        if user_id is not None:
            args.extend(["--user", str(user_id)])
        args.extend(
            [
                "-W",
                "-a",
                "android.settings.APPLICATION_DETAILS_SETTINGS",
                "-d",
                f"package:{package_name}",
            ]
        )
        result = await self.run(
            serial,
            args,
            operation="runtime_permission_settings_open",
            check=False,
        )
        if not self._activity_started(result):
            raise self._categorized_command_error(result, "runtime_permission_settings_open")

    async def special_access_state(
        self,
        serial: str,
        package_name: str,
        access: SpecialAccessKind,
        *,
        component: str | None = None,
        user_id: int | None = None,
    ) -> SpecialAccessState:
        validate_package_name(package_name)
        if user_id is not None and not 0 <= user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        settings_prefix = ["shell", "settings"]
        if user_id is not None:
            settings_prefix.extend(["--user", str(user_id)])
        if access == SpecialAccessKind.ACCESSIBILITY:
            if not component:
                return SpecialAccessState.UNAVAILABLE
            expected = validate_component_name(component, package_name)
            enabled = await self.run(
                serial,
                [*settings_prefix, "get", "secure", "enabled_accessibility_services"],
                operation="accessibility_access_probe",
                check=False,
            )
            if enabled.returncode != 0:
                return self._unavailable_or_raise(enabled, "accessibility_access_probe")
            return (
                SpecialAccessState.GRANTED
                if self._component_enabled(enabled.stdout, expected)
                else SpecialAccessState.NOT_GRANTED
            )
        if access == SpecialAccessKind.NOTIFICATION_LISTENER:
            if not component:
                return SpecialAccessState.UNAVAILABLE
            expected = validate_component_name(component, package_name)
            enabled = await self.run(
                serial,
                [*settings_prefix, "get", "secure", "enabled_notification_listeners"],
                operation="notification_access_probe",
                check=False,
            )
            if enabled.returncode != 0:
                return self._unavailable_or_raise(enabled, "notification_access_probe")
            return (
                SpecialAccessState.GRANTED
                if self._component_enabled(enabled.stdout, expected)
                else SpecialAccessState.NOT_GRANTED
            )
        appops = ["shell", "cmd", "appops", "get"]
        if user_id is not None:
            appops.extend(["--user", str(user_id)])
        appops.extend([package_name, "MANAGE_EXTERNAL_STORAGE"])
        result = await self.run(
            serial,
            appops,
            operation="all_files_access_probe",
            check=False,
        )
        if result.returncode != 0:
            return self._unavailable_or_raise(result, "all_files_access_probe")
        text = f"{result.stdout}\n{result.stderr}".casefold()
        if "allow" in text:
            return SpecialAccessState.GRANTED
        if "no operations" in text or "unknown operation" in text:
            return SpecialAccessState.UNAVAILABLE
        return SpecialAccessState.NOT_GRANTED

    async def dumpsys_accessibility(self, serial: str) -> str:
        result = await self.run(
            serial,
            ["shell", "dumpsys", "accessibility"],
            operation="accessibility_dumpsys",
            check=False,
        )
        if result.returncode != 0:
            raise self._categorized_command_error(result, "accessibility_dumpsys")
        return f"{result.stdout}\n{result.stderr}"

    async def accessibility_service_sets(
        self,
        serial: str,
    ) -> dict[str, set[str]] | None:
        try:
            blob = await self.dumpsys_accessibility(serial)
        except AcquisitionError:
            return None
        return parse_accessibility_service_sets(blob)

    async def accessibility_binding_state(
        self,
        serial: str,
        component: str,
    ) -> AccessibilityBindingState:
        expected = validate_component_name(component)
        package_name = expected.split("/", 1)[0]
        direct_status = await self.probe_accessibility_recovery_status(
            serial,
            package_name,
        )
        if direct_status == "bound":
            return AccessibilityBindingState.BOUND
        service_sets = await self.accessibility_service_sets(serial)
        if service_sets is None:
            if direct_status in {"crashed", "unbound"}:
                return AccessibilityBindingState(direct_status)
            return AccessibilityBindingState.UNAVAILABLE
        dumpsys_state = accessibility_binding_state_for_component(
            service_sets,
            expected,
        )
        # A live binder reported by Android is stronger than a stale status
        # returned by the app process (or a stale Crashed-services entry).
        if dumpsys_state == AccessibilityBindingState.BOUND:
            return dumpsys_state
        if direct_status == "crashed":
            return AccessibilityBindingState.CRASHED
        return dumpsys_state

    async def accessibility_service_bound(
        self,
        serial: str,
        component: str,
    ) -> bool:
        return (
            await self.accessibility_binding_state(serial, component)
            == AccessibilityBindingState.BOUND
        )

    async def wait_accessibility_service_bound(
        self,
        serial: str,
        component: str,
        *,
        timeout_seconds: float = ACCESSIBILITY_BINDING_SETTLE_SECONDS,
        poll_seconds: float = ACCESSIBILITY_BINDING_POLL_SECONDS,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if await self.accessibility_service_bound(serial, component):
                return True
            await asyncio.sleep(poll_seconds)
        return await self.accessibility_service_bound(serial, component)

    async def wait_accessibility_service_unbound(
        self,
        serial: str,
        component: str,
        *,
        timeout_seconds: float = ACCESSIBILITY_UNBIND_SETTLE_SECONDS,
        poll_seconds: float = ACCESSIBILITY_BINDING_POLL_SECONDS,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = await self.accessibility_binding_state(serial, component)
            if state in {
                AccessibilityBindingState.UNBOUND,
                AccessibilityBindingState.CRASHED,
            }:
                return True
            await asyncio.sleep(poll_seconds)
        return await self.accessibility_binding_state(serial, component) in {
            AccessibilityBindingState.UNBOUND,
            AccessibilityBindingState.CRASHED,
        }

    async def secure_settings_writable(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> bool:
        validate_package_name(package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        settings_prefix = ["shell", "settings", "--user", str(active_user_id)]
        current = await self.run(
            serial,
            [*settings_prefix, "get", "secure", "accessibility_enabled"],
            operation="secure_settings_write_probe_get",
            check=False,
        )
        if current.returncode != 0:
            return False
        probe_value = current.stdout.strip() or "0"
        await self._grant_write_secure_settings(serial, package_name, active_user_id)
        probe = await self._put_secure_setting(
            serial,
            settings_prefix,
            "accessibility_enabled",
            probe_value,
            operation="secure_settings_write_probe_put",
        )
        return probe.returncode == 0 and not self._secure_settings_denied(probe)

    async def accessibility_master_enabled(
        self,
        serial: str,
        *,
        user_id: int | None = None,
    ) -> bool:
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        settings_prefix = ["shell", "settings", "--user", str(active_user_id)]
        result = await self.run(
            serial,
            [*settings_prefix, "get", "secure", "accessibility_enabled"],
            operation="accessibility_master_probe",
            check=False,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip() == "1"

    async def probe_accessibility_recovery_status(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> str | None:
        validate_package_name(package_name)
        component = validate_component_name(
            f"{package_name}/{package_name}.{ACCESSIBILITY_RECOVERY_RECEIVER_CLASS}",
            package_name,
        )
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(active_user_id),
                "--include-stopped-packages",
                "-a",
                ACCESSIBILITY_PROBE_ACTION,
                "-n",
                component,
            ],
            operation="accessibility_recovery_probe",
            check=False,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            return None
        return parse_accessibility_recovery_probe(blob)

    async def suspend_accessibility_via_agent(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> str | None:
        validate_package_name(package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        await self._grant_write_secure_settings(serial, package_name, active_user_id)
        component = validate_component_name(
            f"{package_name}/{package_name}.{ACCESSIBILITY_RECOVERY_RECEIVER_CLASS}",
            package_name,
        )
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(active_user_id),
                "--include-stopped-packages",
                "-a",
                ACCESSIBILITY_SUSPEND_ACTION,
                "-n",
                component,
            ],
            operation="accessibility_recovery_suspend",
            check=False,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            return None
        return parse_accessibility_recovery_probe(blob)

    async def suspend_accessibility_service(
        self,
        serial: str,
        package_name: str,
        component: str,
        *,
        user_id: int | None = None,
    ) -> bool:
        validate_package_name(package_name)
        expected = validate_component_name(component, package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        if not 0 <= active_user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        binding = await self.accessibility_binding_state(serial, expected)
        settings_granted = (
            await self.special_access_state(
                serial,
                package_name,
                SpecialAccessKind.ACCESSIBILITY,
                component=expected,
                user_id=active_user_id,
            )
            == SpecialAccessState.GRANTED
        )
        if (
            binding == AccessibilityBindingState.UNBOUND
            and not settings_granted
            and not await self.accessibility_master_enabled(serial, user_id=active_user_id)
        ):
            return True
        shell_writable = await self.secure_settings_writable(
            serial,
            package_name,
            user_id=active_user_id,
        )
        if shell_writable:
            disabled = await self._disable_accessibility_service(
                serial,
                package_name,
                expected,
                active_user_id,
            )
            if disabled in {SpecialAccessState.DENIED, SpecialAccessState.UNAVAILABLE}:
                return False
        else:
            agent_status = await self.suspend_accessibility_via_agent(
                serial,
                package_name,
                user_id=active_user_id,
            )
            logger.info(
                "accessibility_service_agent_suspend",
                extra={
                    "serial_redacted": serial[:4] + "…" if serial else None,
                    "agent_status": agent_status,
                },
            )
            if agent_status == "bound":
                return False
            if agent_status == "denied":
                return False
        return await self.wait_accessibility_service_unbound(serial, expected)

    async def restore_accessibility_via_agent(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> str | None:
        validate_package_name(package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        await self._grant_write_secure_settings(serial, package_name, active_user_id)
        component = validate_component_name(
            f"{package_name}/{package_name}.{ACCESSIBILITY_RECOVERY_RECEIVER_CLASS}",
            package_name,
        )
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(active_user_id),
                "--include-stopped-packages",
                "-a",
                ACCESSIBILITY_RECOVERY_ACTION,
                "-n",
                component,
            ],
            operation="accessibility_recovery_recover",
            check=False,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            return None
        return parse_accessibility_recovery_probe(blob)

    async def accessibility_ready_for_text_only(
        self,
        serial: str,
        component: str,
    ) -> bool:
        expected = validate_component_name(component)
        return (
            await self.accessibility_binding_state(serial, expected)
            == AccessibilityBindingState.BOUND
        )

    async def restore_accessibility_service(
        self,
        serial: str,
        package_name: str,
        component: str,
        *,
        user_id: int | None = None,
    ) -> SpecialAccessState:
        validate_package_name(package_name)
        expected = validate_component_name(component, package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        if not 0 <= active_user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        if await self.accessibility_service_bound(serial, expected):
            return SpecialAccessState.GRANTED
        settings_granted = (
            await self.special_access_state(
                serial,
                package_name,
                SpecialAccessKind.ACCESSIBILITY,
                component=expected,
                user_id=active_user_id,
            )
            == SpecialAccessState.GRANTED
        )
        service_settled = settings_granted and await self.wait_accessibility_service_bound(
            serial,
            expected,
            timeout_seconds=min(2.0, ACCESSIBILITY_BINDING_SETTLE_SECONDS),
        )
        if not service_settled:
            shell_writable = await self.secure_settings_writable(
                serial,
                package_name,
                user_id=active_user_id,
            )
            if shell_writable:
                if settings_granted:
                    logger.warning(
                        "accessibility_service_rebind_required",
                        extra={
                            "serial_redacted": serial[:4] + "…" if serial else None,
                            "binding_state": (
                                await self.accessibility_binding_state(serial, expected)
                            ).value,
                        },
                    )
                    rebind_state = await self._rebind_accessibility_service(
                        serial,
                        package_name,
                        expected,
                        active_user_id,
                    )
                    if rebind_state != SpecialAccessState.GRANTED:
                        return rebind_state
                else:
                    enable_state = await self._enable_accessibility_service(
                        serial,
                        package_name,
                        expected,
                        active_user_id,
                    )
                    if enable_state != SpecialAccessState.GRANTED:
                        return enable_state
            else:
                logger.warning(
                    "accessibility_service_shell_restore_unavailable",
                    extra={
                        "serial_redacted": serial[:4] + "…" if serial else None,
                        "binding_state": (
                            await self.accessibility_binding_state(serial, expected)
                        ).value,
                    },
                )
                agent_status = await self.restore_accessibility_via_agent(
                    serial,
                    package_name,
                    user_id=active_user_id,
                )
                logger.info(
                    "accessibility_service_agent_recovery",
                    extra={
                        "serial_redacted": serial[:4] + "…" if serial else None,
                        "agent_status": agent_status,
                    },
                )
        if await self.wait_accessibility_service_bound(serial, expected):
            return SpecialAccessState.GRANTED
        if await self.accessibility_service_bound(serial, expected):
            return SpecialAccessState.GRANTED
        return await self.special_access_state(
            serial,
            package_name,
            SpecialAccessKind.ACCESSIBILITY,
            component=expected,
            user_id=active_user_id,
        )

    async def _read_enabled_accessibility_components(
        self,
        serial: str,
        settings_prefix: list[str],
    ) -> list[str] | None:
        enabled = await self.run(
            serial,
            [*settings_prefix, "get", "secure", "enabled_accessibility_services"],
            operation="accessibility_restore_probe",
            check=False,
        )
        if enabled.returncode != 0:
            return None
        raw_components = enabled.stdout.strip()
        components: list[str] = []
        if raw_components.casefold() not in {"", "null", "none"}:
            for raw_component in raw_components.split(":"):
                preserved = self._coalesce_enabled_component(raw_component)
                if preserved is None:
                    continue
                if preserved not in components:
                    components.append(preserved)
        return components

    async def _write_accessibility_master(
        self,
        serial: str,
        package_name: str,
        settings_prefix: list[str],
        active_user_id: int,
        *,
        enabled: bool,
        operation: str,
    ) -> SpecialAccessState | None:
        master = await self._put_secure_setting(
            serial,
            settings_prefix,
            "accessibility_enabled",
            "1" if enabled else "0",
            operation=operation,
        )
        if master.returncode != 0 and self._secure_settings_denied(master):
            if await self._grant_write_secure_settings(
                serial,
                package_name,
                active_user_id,
            ):
                master = await self._put_secure_setting(
                    serial,
                    settings_prefix,
                    "accessibility_enabled",
                    "1" if enabled else "0",
                    operation=f"{operation}_retry",
                )
        if master.returncode != 0:
            if self._secure_settings_denied(master):
                return SpecialAccessState.DENIED
            return self._unavailable_or_raise(master, operation)
        return None

    async def _write_enabled_accessibility_components(
        self,
        serial: str,
        package_name: str,
        settings_prefix: list[str],
        active_user_id: int,
        components: list[str],
        *,
        operation: str,
    ) -> SpecialAccessState | None:
        if components:
            enabled_value = ":".join(components)
            updated = await self._put_secure_setting(
                serial,
                settings_prefix,
                "enabled_accessibility_services",
                enabled_value,
                operation=operation,
            )
        else:
            updated = await self.run(
                serial,
                [
                    *settings_prefix,
                    "delete",
                    "secure",
                    "enabled_accessibility_services",
                ],
                operation=f"{operation}_clear",
                check=False,
            )
        if updated.returncode != 0 and self._secure_settings_denied(updated):
            if await self._grant_write_secure_settings(
                serial,
                package_name,
                active_user_id,
            ):
                if components:
                    updated = await self._put_secure_setting(
                        serial,
                        settings_prefix,
                        "enabled_accessibility_services",
                        ":".join(components),
                        operation=f"{operation}_retry",
                    )
                else:
                    updated = await self.run(
                        serial,
                        [
                            *settings_prefix,
                            "delete",
                            "secure",
                            "enabled_accessibility_services",
                        ],
                        operation=f"{operation}_clear_retry",
                        check=False,
                    )
        if updated.returncode != 0:
            if self._secure_settings_denied(updated):
                return SpecialAccessState.DENIED
            return self._unavailable_or_raise(updated, operation)
        return None

    async def _rebind_accessibility_service(
        self,
        serial: str,
        package_name: str,
        expected: str,
        active_user_id: int,
    ) -> SpecialAccessState:
        settings_prefix = ["shell", "settings", "--user", str(active_user_id)]
        components = await self._read_enabled_accessibility_components(
            serial,
            settings_prefix,
        )
        if components is None:
            return SpecialAccessState.UNAVAILABLE
        without_target = [component for component in components if component != expected]
        restored_components = list(components)
        if expected not in restored_components:
            restored_components.append(expected)
        disabled = await self._write_enabled_accessibility_components(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            without_target,
            operation="accessibility_rebind_disable",
        )
        if isinstance(disabled, SpecialAccessState):
            return disabled
        await asyncio.sleep(ACCESSIBILITY_REBIND_PAUSE_SECONDS)
        enabled = await self._write_enabled_accessibility_components(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            restored_components,
            operation="accessibility_rebind_enable",
        )
        if isinstance(enabled, SpecialAccessState):
            # The component-only cycle already removed SIKSIK. Retry the exact
            # original list before returning so a failed re-enable command does
            # not strand the user's accessibility configuration half-written.
            rollback = await self._write_enabled_accessibility_components(
                serial,
                package_name,
                settings_prefix,
                active_user_id,
                restored_components,
                operation="accessibility_rebind_component_rollback",
            )
            if isinstance(rollback, SpecialAccessState):
                logger.error(
                    "accessibility_service_component_rollback_failed",
                    extra={
                        "serial_redacted": serial[:4] + "…" if serial else None,
                        "restore_state": rollback.value,
                    },
                )
            return enabled
        if not await self.accessibility_master_enabled(
            serial,
            user_id=active_user_id,
        ):
            enabled_master = await self._write_accessibility_master(
                serial,
                package_name,
                settings_prefix,
                active_user_id,
                enabled=True,
                operation="accessibility_rebind_master_on",
            )
            if isinstance(enabled_master, SpecialAccessState):
                return enabled_master
        if await self.wait_accessibility_service_bound(
            serial,
            expected,
            timeout_seconds=min(2.0, ACCESSIBILITY_BINDING_SETTLE_SECONDS),
        ):
            return SpecialAccessState.GRANTED

        # Some OEM accessibility managers only rebuild their binder registry
        # after a master cycle. Do this only after component-only recovery has
        # failed, while preserving every enabled component exactly.
        logger.warning(
            "accessibility_service_global_rebind_fallback",
            extra={
                "serial_redacted": serial[:4] + "…" if serial else None,
                "preserved_service_count": len(restored_components),
            },
        )
        disabled_master = await self._write_accessibility_master(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            enabled=False,
            operation="accessibility_rebind_master_off_fallback",
        )
        if isinstance(disabled_master, SpecialAccessState):
            return disabled_master
        await asyncio.sleep(ACCESSIBILITY_REBIND_PAUSE_SECONDS)
        restored = await self._write_enabled_accessibility_components(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            restored_components,
            operation="accessibility_rebind_restore_fallback",
        )
        if isinstance(restored, SpecialAccessState):
            await self._write_accessibility_master(
                serial,
                package_name,
                settings_prefix,
                active_user_id,
                enabled=True,
                operation="accessibility_rebind_master_rollback",
            )
            return restored
        enabled_master = await self._write_accessibility_master(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            enabled=True,
            operation="accessibility_rebind_master_on_fallback",
        )
        if isinstance(enabled_master, SpecialAccessState):
            # Best effort: never intentionally leave all accessibility services
            # disabled after a failed recovery command.
            await self._write_accessibility_master(
                serial,
                package_name,
                settings_prefix,
                active_user_id,
                enabled=True,
                operation="accessibility_rebind_master_rollback",
            )
            return enabled_master
        return SpecialAccessState.GRANTED

    async def _disable_accessibility_service(
        self,
        serial: str,
        package_name: str,
        expected: str,
        active_user_id: int,
    ) -> SpecialAccessState:
        settings_prefix = ["shell", "settings", "--user", str(active_user_id)]
        components = await self._read_enabled_accessibility_components(
            serial,
            settings_prefix,
        )
        if components is None:
            return SpecialAccessState.UNAVAILABLE
        without_target = [component for component in components if component != expected]
        disabled = await self._write_enabled_accessibility_components(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            without_target,
            operation="accessibility_suspend_disable",
        )
        if isinstance(disabled, SpecialAccessState):
            return disabled
        if not without_target:
            disabled_master = await self._write_accessibility_master(
                serial,
                package_name,
                settings_prefix,
                active_user_id,
                enabled=False,
                operation="accessibility_suspend_master_off",
            )
            if isinstance(disabled_master, SpecialAccessState):
                return disabled_master
        await asyncio.sleep(ACCESSIBILITY_REBIND_PAUSE_SECONDS)
        return SpecialAccessState.GRANTED

    async def _enable_accessibility_service(
        self,
        serial: str,
        package_name: str,
        expected: str,
        active_user_id: int,
    ) -> SpecialAccessState:
        settings_prefix = ["shell", "settings", "--user", str(active_user_id)]
        components = await self._read_enabled_accessibility_components(
            serial,
            settings_prefix,
        )
        if components is None:
            return SpecialAccessState.UNAVAILABLE
        if expected not in components:
            components.append(expected)
        updated = await self._write_enabled_accessibility_components(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            components,
            operation="accessibility_access_restore",
        )
        if isinstance(updated, SpecialAccessState):
            return updated
        master = await self._write_accessibility_master(
            serial,
            package_name,
            settings_prefix,
            active_user_id,
            enabled=True,
            operation="accessibility_master_restore",
        )
        if isinstance(master, SpecialAccessState):
            return master
        return SpecialAccessState.GRANTED

    async def set_text_only_cover_visible(
        self,
        serial: str,
        package_name: str,
        *,
        visible: bool,
        user_id: int | None = None,
    ) -> None:
        validate_package_name(package_name)
        component = validate_component_name(
            f"{package_name}/{package_name}.{TEXT_ONLY_COVER_RECEIVER_CLASS}",
            package_name,
        )
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        flag = "true" if visible else "false"
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(active_user_id),
                "--include-stopped-packages",
                "-a",
                TEXT_ONLY_COVER_ACTION,
                "--ez",
                TEXT_ONLY_COVER_VISIBLE_EXTRA,
                flag,
                "-n",
                component,
            ],
            operation="text_only_cover_set",
            check=False,
        )
        if result.returncode != 0:
            raise self._categorized_command_error(result, "text_only_cover_set")

    async def probe_text_only_cover(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> bool:
        return await self.probe_text_only_cover_status(
            serial,
            package_name,
            user_id=user_id,
        ) == "shown"

    async def probe_text_only_cover_status(
        self,
        serial: str,
        package_name: str,
        *,
        user_id: int | None = None,
    ) -> str | None:
        validate_package_name(package_name)
        component = validate_component_name(
            f"{package_name}/{package_name}.{TEXT_ONLY_COVER_RECEIVER_CLASS}",
            package_name,
        )
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        result = await self.run(
            serial,
            [
                "shell",
                "am",
                "broadcast",
                "--user",
                str(active_user_id),
                "--include-stopped-packages",
                "-a",
                TEXT_ONLY_COVER_PROBE_ACTION,
                "-n",
                component,
            ],
            operation="text_only_cover_probe",
            check=False,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if "Permission Denial" in blob and "not exported" in blob:
            return None
        if result.returncode != 0:
            return None
        return parse_text_only_cover_probe(blob)

    async def grant_notification_listener(
        self,
        serial: str,
        package_name: str,
        component: str,
        *,
        user_id: int | None = None,
    ) -> SpecialAccessState:
        validate_package_name(package_name)
        normalized_component = validate_component_name(component, package_name)
        active_user_id = await self.current_user_id(serial) if user_id is None else user_id
        if not 0 <= active_user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        current = await self.special_access_state(
            serial,
            package_name,
            SpecialAccessKind.NOTIFICATION_LISTENER,
            component=normalized_component,
            user_id=active_user_id,
        )
        if current == SpecialAccessState.GRANTED:
            return current
        result = await self.run(
            serial,
            [
                "shell",
                "cmd",
                "notification",
                "allow_listener",
                normalized_component,
                str(active_user_id),
            ],
            operation="notification_access_grant",
            check=False,
        )
        if result.returncode != 0:
            text = f"{result.stdout}\n{result.stderr}".casefold()
            if any(
                marker in text
                for marker in (
                    "unknown command",
                    "can't find service: notification",
                    "not found",
                    "usage: cmd notification",
                )
            ):
                return SpecialAccessState.UNAVAILABLE
            if "security exception" in text or "permission denial" in text:
                return SpecialAccessState.DENIED
            return self._unavailable_or_raise(result, "notification_access_grant")
        return await self.special_access_state(
            serial,
            package_name,
            SpecialAccessKind.NOTIFICATION_LISTENER,
            component=normalized_component,
            user_id=active_user_id,
        )

    async def open_special_access_settings(
        self,
        serial: str,
        package_name: str,
        access: SpecialAccessKind,
        *,
        user_id: int | None = None,
        component: str | None = None,
    ) -> None:
        validate_package_name(package_name)
        if user_id is not None and not 0 <= user_id <= 100_000:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Android user tidak valid.")
        if access == SpecialAccessKind.NOTIFICATION_LISTENER:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Notification Listener hanya boleh diaktifkan melalui ADB terverifikasi.",
            )
        if component is not None:
            validate_component_name(component, package_name)
        actions = {
            SpecialAccessKind.ACCESSIBILITY: "android.settings.ACCESSIBILITY_SETTINGS",
            SpecialAccessKind.MANAGE_ALL_FILES: (
                "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION"
            ),
        }
        launch = ["shell", "am", "start"]
        if user_id is not None:
            launch.extend(["--user", str(user_id)])
        launch.append("-W")
        if access == SpecialAccessKind.ACCESSIBILITY and component is not None:
            details = await self.run(
                serial,
                [
                    *launch,
                    "-a",
                    "android.settings.ACCESSIBILITY_DETAILS_SETTINGS",
                    "--ecn",
                    "android.intent.extra.COMPONENT_NAME",
                    component,
                ],
                operation="special_access_settings_open",
                check=False,
            )
            if self._activity_started(details):
                return
        args = [*launch, "-a", actions[access]]
        if access == SpecialAccessKind.MANAGE_ALL_FILES:
            args.extend(["-d", f"package:{package_name}"])
        result = await self.run(
            serial,
            args,
            operation="special_access_settings_open",
            check=False,
        )
        if self._activity_started(result):
            return
        if access == SpecialAccessKind.MANAGE_ALL_FILES:
            fallback_args = ["shell", "am", "start"]
            if user_id is not None:
                fallback_args.extend(["--user", str(user_id)])
            fallback_args.extend(
                [
                    "-W",
                    "-a",
                    "android.settings.MANAGE_ALL_FILES_ACCESS_PERMISSION",
                ]
            )
            fallback = await self.run(
                serial,
                fallback_args,
                operation="special_access_settings_open",
                check=False,
            )
            if self._activity_started(fallback):
                return
            result = fallback
        raise self._categorized_command_error(result, "special_access_settings_open")

    @staticmethod
    def _coalesce_enabled_component(raw: str) -> str | None:
        """Keep enabled a11y/listener entries, including foreign OEM packages.

        Strict validate_component_name requires class names under the declaring
        package. Third-party services (e.g. parental controls) often use a
        different class package; aborting restore for those incorrectly marks
        Accessibility as unavailable on otherwise healthy devices.
        """
        value = raw.strip()
        if not value:
            return None
        try:
            return validate_component_name(value)
        except AcquisitionError:
            if value.count("/") != 1:
                return None
            package_name, raw_class = value.split("/", 1)
            if not SAFE_PACKAGE.fullmatch(package_name):
                return None
            full_class = (
                f"{package_name}{raw_class}" if raw_class.startswith(".") else raw_class
            )
            if not SAFE_CLASS.fullmatch(full_class):
                return None
            return value

    @staticmethod
    def _component_enabled(output: str, expected: str) -> bool:
        expected_package, expected_class = validate_component_name(expected).split(
            "/",
            1,
        )
        for raw_component in output.strip().split(":"):
            try:
                normalized = validate_component_name(raw_component.strip())
                package_name, class_name = normalized.split("/", 1)
            except AcquisitionError:
                continue
            if package_name == expected_package and class_name == expected_class:
                return True
        return False

    @staticmethod
    def _activity_started(result: ProcessResult) -> bool:
        if result.returncode != 0:
            return False
        output = f"{result.stdout}\n{result.stderr}".casefold()
        hard_failure_markers = (
            "unable to resolve intent",
            "no activity found to handle intent",
            "activity class does not exist",
            "error type 3",
            "exception occurred while executing",
            "securityexception",
            "security exception",
            "permission denial",
            "not allowed to start",
        )
        if any(marker in output for marker in hard_failure_markers):
            return False
        if "intent has been delivered to currently running" in output:
            return True
        if any(line.strip().startswith("error:") for line in output.splitlines()):
            return False
        statuses = re.findall(r"(?m)^\s*status:\s*([^\s]+)", output)
        if statuses and statuses[-1] != "ok":
            return False
        return True

    async def _put_secure_setting(
        self,
        serial: str,
        settings_prefix: list[str],
        key: str,
        value: str,
        *,
        operation: str,
    ) -> ProcessResult:
        return await self.run(
            serial,
            [*settings_prefix, "put", "secure", key, value],
            operation=operation,
            check=False,
        )

    @staticmethod
    def _secure_settings_denied(result: ProcessResult) -> bool:
        text = f"{result.stdout}\n{result.stderr}".casefold()
        return "security exception" in text or "permission denial" in text

    async def _grant_write_secure_settings(
        self,
        serial: str,
        package_name: str,
        user_id: int,
    ) -> bool:
        result = await self.run(
            serial,
            [
                "shell",
                "pm",
                "grant",
                "--user",
                str(user_id),
                package_name,
                "android.permission.WRITE_SECURE_SETTINGS",
            ],
            operation="write_secure_settings_grant",
            check=False,
        )
        return result.returncode == 0

    def _unavailable_or_raise(
        self,
        result: ProcessResult,
        operation: str,
    ) -> SpecialAccessState:
        categorized = self._categorized_command_error(result, operation)
        if categorized.category != ErrorCategory.ADB_COMMAND_FAILED:
            raise categorized
        return SpecialAccessState.UNAVAILABLE

    async def create_forward(self, serial: str, device_port: int) -> int:
        if not 1 <= device_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Port agent tidak valid.")
        await self.select_device(serial)
        result = await self.run(
            serial,
            ["forward", "tcp:0", f"tcp:{device_port}"],
            operation="adb_forward_create",
        )
        raw_port = result.stdout.strip()
        if not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
            raise acquisition_error(
                ErrorCategory.ADB_COMMAND_FAILED,
                "ADB mengembalikan port forward yang tidak valid.",
            )
        return int(raw_port)

    async def restore_forward(
        self,
        serial: str,
        host_port: int,
        device_port: int,
        *,
        timeout: float = 90.0,
        poll_interval: float = 0.5,
    ) -> None:
        if not 1 <= host_port <= 65535 or not 1 <= device_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Port forward tidak valid.")
        if timeout <= 0 or poll_interval <= 0:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas pemulihan koneksi Android tidak valid.",
            )

        serial = validate_serial(serial)
        deadline = time.monotonic() + timeout
        retryable_categories = {
            ErrorCategory.ADB_NO_DEVICE,
            ErrorCategory.ADB_OFFLINE,
            ErrorCategory.ADB_TIMEOUT,
            ErrorCategory.ADB_COMMAND_FAILED,
        }
        last_error: AcquisitionError | None = None

        while True:
            try:
                await self.select_device(serial)
                result = await self.run(
                    serial,
                    ["forward", f"tcp:{host_port}", f"tcp:{device_port}"],
                    operation="adb_forward_restore",
                    check=False,
                )
                if result.returncode == 0:
                    return
                last_error = self._categorized_command_error(
                    result,
                    "adb_forward_restore",
                )
            except AcquisitionError as exc:
                if exc.category not in retryable_categories:
                    raise
                last_error = exc

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "Koneksi Android terputus dan port agent belum dapat dipulihkan.",
            retryable=True,
            dependency_exit_code=(
                last_error.dependency_exit_code if last_error is not None else None
            ),
        )

    async def remove_forward(self, serial: str, host_port: int) -> None:
        if not 1 <= host_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Port forward tidak valid.")
        await self.run(
            serial,
            ["forward", "--remove", f"tcp:{host_port}"],
            operation="adb_forward_remove",
            check=False,
        )

    async def start_activity(
        self,
        serial: str,
        component: str,
        extras: Mapping[str, str | int],
        *,
        timeout: float = 30.0,
    ) -> None:
        if not component or "\x00" in component or "/" not in component:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Komponen agent tidak valid.")
        args = ["shell", "am", "start", "-W", "-n", component]
        for key, value in extras.items():
            if not SAFE_EXTRA_KEY.fullmatch(key):
                raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Nama extra tidak valid.")
            if isinstance(value, int):
                args.extend(["--el", key, str(value)])
            else:
                if "\x00" in value or len(value) > 4096:
                    raise acquisition_error(
                        ErrorCategory.VALIDATION_ERROR,
                        "Nilai extra tidak valid.",
                    )
                args.extend(["--es", key, value])
        await self.run(serial, args, operation="agent_start", timeout=timeout)

    async def force_stop(self, serial: str, package_name: str) -> None:
        validate_package_name(package_name)
        await self.run(
            serial,
            ["shell", "am", "force-stop", package_name],
            operation="agent_stop",
            check=False,
        )

    async def pull_staged_file(
        self,
        serial: str,
        *,
        remote_root: PurePosixPath,
        relative_path: str,
        destination_root: Path,
        destination: Path,
        timeout: float,
    ) -> None:
        relative = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\x00" in relative_path
            or relative.is_absolute()
            or ".." in relative.parts
            or any(part in {"", "."} for part in relative.parts)
        ):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Path staging tidak valid.")
        root = destination_root.expanduser().resolve()
        target = destination.expanduser().resolve()
        if not target.is_relative_to(root):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan artifact berada di luar staging sesi.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        remote = (remote_root / relative).as_posix()
        await self.run(
            serial,
            ["pull", remote, str(target)],
            operation="agent_artifact_pull",
            timeout=timeout,
        )

    async def pull_staged_directory(
        self,
        serial: str,
        *,
        remote_root: PurePosixPath,
        relative_path: str,
        destination_root: Path,
        destination: Path,
        timeout: float,
    ) -> None:
        relative = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\x00" in relative_path
            or relative.is_absolute()
            or len(relative.parts) != 2
            or ".." in relative.parts
            or any(part in {"", "."} for part in relative.parts)
            or any(not SAFE_SERIAL.fullmatch(part) for part in relative.parts)
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Path direktori staging tidak valid.",
            )
        root = destination_root.expanduser().resolve()
        target = destination.expanduser().resolve()
        if not target.is_relative_to(root) or target.exists():
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan direktori staging tidak valid.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        remote = (remote_root / relative).as_posix()
        await self.run(
            serial,
            ["pull", remote, str(target)],
            operation="agent_stage_pull",
            timeout=timeout,
        )
        if not target.is_dir():
            raise acquisition_error(
                ErrorCategory.ADB_COMMAND_FAILED,
                "ADB tidak menghasilkan direktori staging Android.",
            )

    async def pull_social_debug_mapping(
        self,
        serial: str,
        *,
        agent_package: str,
        session_id: str,
        crawl_id: str,
        target_package: str,
        destination_root: Path,
        timeout: float,
    ) -> int:
        validate_package_name(agent_package)
        validate_package_name(target_package)
        if (
            not SAFE_SERIAL.fullmatch(session_id)
            or not SAFE_SERIAL.fullmatch(crawl_id)
            or timeout <= 0
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Identitas debug mapping Android tidak valid.",
            )
        root = destination_root.expanduser().resolve()
        target = (root / session_id / crawl_id / "mapping" / target_package).resolve()
        if not target.is_relative_to(root):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan debug mapping Android tidak valid.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        remote = PurePosixPath(
            "/sdcard/Android/data",
            agent_package,
            "files/social_crawl_debug",
            session_id,
            crawl_id,
            target_package,
        ).as_posix()
        result = await self.run(
            serial,
            ["pull", f"{remote}/.", str(temporary)],
            operation="social_debug_mapping_pull",
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            shutil.rmtree(temporary, ignore_errors=True)
            return 0
        files = list(temporary.iterdir())
        allowed_image = re.compile(r"^[0-9]{3}__[a-z0-9_-]{1,64}\.png$")
        total_bytes = 0
        for item in files:
            if (
                item.is_symlink()
                or not item.is_file()
                or (item.name != "mapping.json" and not allowed_image.fullmatch(item.name))
            ):
                shutil.rmtree(temporary, ignore_errors=True)
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Isi debug mapping Android tidak valid.",
                )
            total_bytes += item.stat().st_size
        if (
            "mapping.json" not in {item.name for item in files}
            or len(files) > 49
            or total_bytes > 256 * 1024 * 1024
        ):
            shutil.rmtree(temporary, ignore_errors=True)
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Batas debug mapping Android terlampaui.",
            )
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
        return len(files)
