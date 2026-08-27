from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import xml.etree.ElementTree as ElementTree
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.acquisition.errors import ErrorCategory, acquisition_error
from app.acquisition.process import ProcessResult, run_process, sanitized_environment

MetadataRunner = Callable[..., Awaitable[ProcessResult]]


@dataclass(frozen=True, slots=True)
class ApkMetadataConfig:
    android_home: Path | None = None
    java_home: Path | None = None
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class ApkMetadata:
    path: Path
    package_name: str
    version_code: int
    version_name: str
    signer_sha256: str
    apk_sha256: str
    size_bytes: int
    uses_shared_user_id: bool = False


class ApkMetadataInspector:
    def __init__(
        self,
        config: ApkMetadataConfig,
        *,
        runner: MetadataRunner = run_process,
    ) -> None:
        if config.timeout_seconds <= 0:
            raise ValueError("APK inspection timeout must be positive")
        self._config = config
        self._runner = runner
        self._cache: dict[tuple[Path, int, int], ApkMetadata] = {}

    async def inspect(self, apk_path: Path) -> ApkMetadata:
        path = apk_path.expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Artifact APK tidak valid.")
        stat = path.stat()
        size = stat.st_size
        if not 0 < size <= 250 * 1024 * 1024:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Ukuran APK tidak valid.")
        cache_key = (path, size, stat.st_mtime_ns)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        apkanalyzer, apksigner = self._resolve_tools()
        environment = self._environment()
        application_id, version_code_raw, version_name, certificate, manifest = await asyncio.gather(
            self._tool_output(
                [str(apkanalyzer), "manifest", "application-id", str(path)],
                "apk_application_id_probe",
                environment,
            ),
            self._tool_output(
                [str(apkanalyzer), "manifest", "version-code", str(path)],
                "apk_version_code_probe",
                environment,
            ),
            self._tool_output(
                [str(apkanalyzer), "manifest", "version-name", str(path)],
                "apk_version_name_probe",
                environment,
            ),
            self._tool_output(
                [str(apksigner), "verify", "--print-certs", str(path)],
                "apk_signature_probe",
                environment,
            ),
            self._tool_output(
                [str(apkanalyzer), "manifest", "print", str(path)],
                "apk_manifest_probe",
                environment,
            ),
        )
        package_name = application_id.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{1,254}", package_name):
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Application ID APK agent tidak valid.",
            )
        raw_code = version_code_raw.strip()
        if not raw_code.isdigit() or int(raw_code) <= 0:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Version code APK agent tidak valid.",
            )
        signer = self._parse_signer(certificate)
        uses_shared_user = self._uses_shared_user_id(manifest)
        result = ApkMetadata(
            path=path,
            package_name=package_name,
            version_code=int(raw_code),
            version_name=version_name.strip(),
            signer_sha256=signer,
            apk_sha256=await asyncio.to_thread(self._sha256, path),
            size_bytes=size,
            uses_shared_user_id=uses_shared_user,
        )
        self._cache[cache_key] = result
        return result

    async def _tool_output(
        self,
        argv: Sequence[str],
        operation: str,
        environment: Mapping[str, str],
    ) -> str:
        result = await self._runner(
            argv,
            timeout=self._config.timeout_seconds,
            env=environment,
            check=False,
            output_limit_bytes=256 * 1024,
            not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
            timeout_category=ErrorCategory.AGENT_BUILD_TIMEOUT,
            failure_category=ErrorCategory.AGENT_INSTALL_FAILED,
            operation=operation,
        )
        if result.returncode != 0 or result.output_truncated:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Metadata APK agent tidak dapat diverifikasi.",
                dependency_exit_code=result.returncode,
            )
        return result.stdout

    def _resolve_tools(self) -> tuple[Path, Path]:
        android_home = self._android_home()
        analyzer_candidates = (
            android_home / "cmdline-tools" / "latest" / "bin" / "apkanalyzer",
            android_home / "tools" / "bin" / "apkanalyzer",
        )
        analyzer = next((item for item in analyzer_candidates if self._executable(item)), None)
        build_tools = android_home / "build-tools"
        versions = sorted(
            (item for item in build_tools.iterdir() if item.is_dir()),
            key=lambda item: self._version_key(item.name),
            reverse=True,
        ) if build_tools.is_dir() else []
        signer = next(
            (item / "apksigner" for item in versions if self._executable(item / "apksigner")),
            None,
        )
        if analyzer is None or signer is None:
            raise acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "Tool inspeksi APK Android tidak tersedia.",
            )
        return analyzer, signer

    def _android_home(self) -> Path:
        candidates = (
            self._config.android_home,
            Path(os.environ["ANDROID_HOME"]) if os.environ.get("ANDROID_HOME") else None,
            Path(os.environ["ANDROID_SDK_ROOT"]) if os.environ.get("ANDROID_SDK_ROOT") else None,
            Path.home() / "Android" / "Sdk",
            Path("/usr/lib/android-sdk"),
            Path("/opt/homebrew/share/android-commandlinetools"),
            Path("/usr/local/share/android-commandlinetools"),
            Path.home() / "Library" / "Android" / "sdk",
        )
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                resolved = candidate.expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.is_dir():
                return resolved
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "Android SDK untuk inspeksi APK tidak ditemukan.",
        )

    def _environment(self) -> Mapping[str, str]:
        environment = sanitized_environment()
        java_home = self._config.java_home
        if java_home is None and os.environ.get("JAVA_HOME"):
            java_home = Path(os.environ["JAVA_HOME"])
        if java_home is None:
            for candidate in (
                Path("/usr/lib/jvm/java-17-openjdk-amd64"),
                Path("/usr/lib/jvm/java-17-openjdk"),
                Path("/opt/homebrew/opt/openjdk@17"),
                Path("/usr/local/opt/openjdk@17"),
            ):
                if candidate.is_dir():
                    java_home = candidate
                    break
        if java_home is not None and java_home.expanduser().is_dir():
            environment["JAVA_HOME"] = str(java_home.expanduser().resolve())
        android_home = self._android_home()
        environment["ANDROID_HOME"] = str(android_home)
        environment["ANDROID_SDK_ROOT"] = str(android_home)
        return environment

    @staticmethod
    def _parse_signer(output: str) -> str:
        match = re.search(
            r"Signer #1 certificate SHA-256 digest:\s*([0-9A-Fa-f:]{64,95})",
            output,
        )
        if match is None:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Signature APK agent tidak dapat diverifikasi.",
            )
        digest = match.group(1).replace(":", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Digest signature APK agent tidak valid.",
            )
        return digest

    @staticmethod
    def _uses_shared_user_id(output: str) -> bool:
        try:
            manifest = ElementTree.fromstring(output)
        except ElementTree.ParseError as exc:
            raise acquisition_error(
                ErrorCategory.AGENT_INSTALL_FAILED,
                "Manifest APK agent tidak dapat diverifikasi.",
            ) from exc
        shared_user = manifest.attrib.get(
            "{http://schemas.android.com/apk/res/android}sharedUserId",
        )
        return shared_user is not None and bool(shared_user.strip())

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.-]", value))

    @staticmethod
    def _executable(path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK) and shutil.which(str(path)) is not None
