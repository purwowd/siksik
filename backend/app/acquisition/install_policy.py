from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path


class InstallFailureKind(str, Enum):
    NONE = "none"
    RUNTIME_GRANT_UNSUPPORTED = "runtime_grant_unsupported"
    STREAMING_UNAVAILABLE = "streaming_unavailable"
    NO_STREAMING_UNSUPPORTED = "no_streaming_unsupported"
    TEST_ONLY_REJECTED = "test_only_rejected"
    UPDATE_INCOMPATIBLE = "update_incompatible"
    UID_INCOMPATIBLE = "uid_incompatible"
    INSUFFICIENT_STORAGE = "insufficient_storage"
    VERSION_DOWNGRADE = "version_downgrade"
    DEVICE_INCOMPATIBLE = "device_incompatible"
    USER_RESTRICTED = "user_restricted"
    DEVICE_POLICY = "device_policy"
    INVALID_APK = "invalid_apk"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InstallAttempt:
    grant_runtime_permissions: bool
    allow_test_packages: bool
    no_streaming: bool = False

    def argv(self, apk_path: Path) -> list[str]:
        args = ["install"]
        if self.no_streaming:
            args.append("--no-streaming")
        args.append("-r")
        if self.grant_runtime_permissions:
            args.append("-g")
        if self.allow_test_packages:
            args.append("-t")
        args.append(str(apk_path))
        return args

    @property
    def name(self) -> str:
        transport = "push" if self.no_streaming else "stream"
        grants = "grant" if self.grant_runtime_permissions else "post_grant"
        package_type = "test" if self.allow_test_packages else "app"
        return f"{transport}_{grants}_{package_type}"


def initial_install_attempt(
    *,
    api_level: int | None,
    grant_runtime_permissions: bool,
    allow_test_packages: bool,
    manufacturer: str | None = None,
    brand: str | None = None,
    prefer_no_streaming: bool = False,
    runtime_grant_supported: bool = True,
) -> InstallAttempt:
    oem = f"{manufacturer or ''} {brand or ''}".casefold()
    oem_prefers_push = any(
        token in oem
        for token in (
            "xiaomi",
            "redmi",
            "poco",
            "oppo",
            "realme",
            "oneplus",
            "vivo",
            "iqoo",
            "infinix",
            "tecno",
            "itel",
            "huawei",
            "honor",
            "samsung",
            "sec",
        )
    )
    use_push = prefer_no_streaming or oem_prefers_push or (
        api_level is not None and api_level <= 28
    ) or True
    use_grant = bool(grant_runtime_permissions and runtime_grant_supported)
    return InstallAttempt(
        grant_runtime_permissions=use_grant,
        allow_test_packages=allow_test_packages,
        no_streaming=use_push,
    )


@dataclass(frozen=True, slots=True)
class InstallEvaluation:
    success: bool
    failure: InstallFailureKind


@dataclass(frozen=True, slots=True)
class ApkInstallOutcome:
    strategy: str
    attempt_count: int
    runtime_granted_during_install: bool


def evaluate_install_result(returncode: int, stdout: str, stderr: str) -> InstallEvaluation:
    output = f"{stdout}\n{stderr}".casefold()
    if (
        returncode == 0
        and "failure [" not in output
        and "exception occurred" not in output
    ):
        return InstallEvaluation(True, InstallFailureKind.NONE)
    if any(
        marker in output
        for marker in (
            "unknown option --no-streaming",
            "unrecognized option '--no-streaming'",
            "unknown option: --no-streaming",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.NO_STREAMING_UNSUPPORTED)
    if "securityexception" in output and "install_grant_runtime_permissions" in output:
        return InstallEvaluation(False, InstallFailureKind.RUNTIME_GRANT_UNSUPPORTED)
    if "install_failed_test_only" in output:
        return InstallEvaluation(False, InstallFailureKind.TEST_ONLY_REJECTED)
    if "install_failed_update_incompatible" in output:
        return InstallEvaluation(False, InstallFailureKind.UPDATE_INCOMPATIBLE)
    if any(
        marker in output
        for marker in (
            "install_failed_shared_user_incompatible",
            "install_failed_uid_changed",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.UID_INCOMPATIBLE)
    if "install_failed_insufficient_storage" in output:
        return InstallEvaluation(False, InstallFailureKind.INSUFFICIENT_STORAGE)
    if "install_failed_version_downgrade" in output:
        return InstallEvaluation(False, InstallFailureKind.VERSION_DOWNGRADE)
    if any(
        marker in output
        for marker in (
            "install_failed_older_sdk",
            "install_failed_no_matching_abis",
            "install_failed_cpu_abi_incompatible",
            "install_failed_missing_shared_library",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.DEVICE_INCOMPATIBLE)
    if any(
        marker in output
        for marker in (
            "install_failed_user_restricted",
            "install canceled by user",
            "install cancelled by user",
            "user rejected",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.USER_RESTRICTED)
    if any(
        marker in output
        for marker in (
            "install_failed_verification_failure",
            "install_failed_blocked_by_device_policy",
            "blocked by device policy",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.DEVICE_POLICY)
    if any(
        marker in output
        for marker in (
            "install_failed_invalid_apk",
            "install_parse_failed",
            "failed to parse",
        )
    ):
        return InstallEvaluation(False, InstallFailureKind.INVALID_APK)
    if _is_streaming_transport_failure(output):
        return InstallEvaluation(False, InstallFailureKind.STREAMING_UNAVAILABLE)
    return InstallEvaluation(False, InstallFailureKind.UNKNOWN)


def next_install_attempt(
    attempt: InstallAttempt,
    failure: InstallFailureKind,
) -> InstallAttempt | None:
    if failure == InstallFailureKind.NO_STREAMING_UNSUPPORTED:
        if attempt.no_streaming:
            return replace(attempt, no_streaming=False)
        return None
    if failure == InstallFailureKind.RUNTIME_GRANT_UNSUPPORTED:
        if attempt.grant_runtime_permissions:
            return replace(attempt, grant_runtime_permissions=False)
        return None
    if failure == InstallFailureKind.TEST_ONLY_REJECTED:
        if not attempt.allow_test_packages:
            return replace(attempt, allow_test_packages=True)
        return None
    if failure == InstallFailureKind.STREAMING_UNAVAILABLE:
        if not attempt.no_streaming:
            return replace(attempt, no_streaming=True)
        return None
    return None


def oem_install_guidance(*, manufacturer: str | None = None, brand: str | None = None) -> str:
    blob = f"{manufacturer or ''} {brand or ''}".casefold()
    if any(token in blob for token in ("xiaomi", "redmi", "poco")):
        return (
            "Xiaomi/MIUI: buka kunci layar, lalu Developer options → aktifkan "
            "'Install via USB' dan 'USB debugging (Security settings)', "
            "kemudian setujui dialog instalasi di HP."
        )
    if any(token in blob for token in ("oppo", "realme", "oneplus", "oplus")):
        return (
            "OPPO/Realme/OnePlus: buka kunci layar, aktifkan 'Install via USB' "
            "di Developer options, lalu setujui dialog instalasi di HP."
        )
    if any(token in blob for token in ("vivo", "iqoo")):
        return (
            "vivo/iQOO: buka kunci layar, aktifkan instalasi via USB di "
            "Developer options, lalu setujui dialog instalasi di HP."
        )
    if any(token in blob for token in ("infinix", "tecno", "itel", "transsion")):
        return (
            "Infinix/Tecno/Itel: buka kunci layar, izinkan instalasi via USB, "
            "lalu setujui dialog instalasi di HP."
        )
    if any(token in blob for token in ("samsung",)):
        return (
            "Samsung: buka kunci layar dan setujui dialog instalasi USB bila muncul."
        )
    return (
        "Buka kunci layar perangkat, aktifkan kebijakan instalasi via USB OEM "
        "bila ada, lalu setujui dialog instalasi di HP."
    )


def _is_streaming_transport_failure(output: str) -> bool:
    if any(
        marker in output
        for marker in (
            "unauthorized",
            "device offline",
            "device not found",
            "no devices",
            "failure [install_failed",
        )
    ):
        return False
    streaming_context = "performing streamed install" in output or "streaming" in output
    if not streaming_context:
        return False
    return any(
        marker in output
        for marker in (
            "broken pipe",
            "connection reset",
            "failed to read",
            "failed to write",
            "protocol fault",
            "streamed install failed",
            "unexpected eof",
            "connection closed",
        )
    )
