from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import run_process, sanitized_environment

logger = logging.getLogger("siksik.acquisition.agent_artifact")
BUILD_ROOT_FILES = frozenset(
    {
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
        "gradlew",
        "gradlew.bat",
        "settings.gradle",
        "settings.gradle.kts",
    }
)
IGNORED_PARTS = frozenset({".git", ".gradle", ".idea", "build", "captures"})


@dataclass(frozen=True, slots=True)
class AgentArtifactConfig:
    project_path: Path
    apk_path: Path
    build_timeout_seconds: float = 600.0
    max_apk_bytes: int = 250 * 1024 * 1024
    java_home: Path | None = None
    android_home: Path | None = None
    required_output_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    path: Path
    input_sha256: str
    apk_sha256: str
    size_bytes: int
    reused: bool


class AgentArtifactService:
    def __init__(self, config: AgentArtifactConfig) -> None:
        self._config = config
        self._build_lock = asyncio.Lock()

    async def build_debug_apk(self, request_id: str | None = None) -> AgentArtifact:
        if self._build_lock.locked():
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_CONFLICT,
                "Build APK agent sedang berjalan.",
                retryable=True,
            )
        async with self._build_lock:
            return await self._build(request_id)

    async def _build(self, request_id: str | None) -> AgentArtifact:
        project = self._config.project_path.expanduser().resolve()
        wrapper = project / "gradlew"
        apk = self._config.apk_path.expanduser().resolve()
        if not project.is_dir() or not wrapper.is_file() or not os.access(wrapper, os.X_OK):
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_FAILED,
                "Project atau Gradle wrapper Android agent tidak tersedia.",
            )
        input_sha256 = await asyncio.to_thread(self._input_digest, project)
        started = time.monotonic()
        reused = await asyncio.to_thread(self._reusable_artifact, apk, input_sha256)
        if reused is not None:
            logger.info(
                "agent_build_reused",
                extra={
                    "request_id": request_id,
                    "artifact_bytes": reused.size_bytes,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "input_sha256": input_sha256[:12],
                },
            )
            return reused

        logger.info(
            "agent_build_started",
            extra={
                "request_id": request_id,
                "timeout_ms": round(self._config.build_timeout_seconds * 1000),
            },
        )
        try:
            result = await run_process(
                [
                    str(wrapper),
                    ":app:assembleDebug",
                    ":automation:assembleDebug",
                    "--parallel",
                    "--build-cache",
                ],
                timeout=self._config.build_timeout_seconds,
                cwd=project,
                env=self._build_environment(input_sha256),
                check=False,
                output_limit_bytes=256 * 1024,
                not_found_category=ErrorCategory.AGENT_BUILD_FAILED,
                timeout_category=ErrorCategory.AGENT_BUILD_TIMEOUT,
                failure_category=ErrorCategory.AGENT_BUILD_FAILED,
                operation="agent_build",
            )
        except AcquisitionError as exc:
            logger.warning(
                "agent_build_failed",
                extra={
                    "request_id": request_id,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error_category": exc.category.value,
                    "retryable": exc.retryable,
                },
            )
            raise
        if result.returncode != 0:
            logger.warning(
                "agent_build_failed",
                extra={
                    "request_id": request_id,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error_category": ErrorCategory.AGENT_BUILD_FAILED.value,
                    "dependency_exit_code": result.returncode,
                },
            )
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_FAILED,
                "Build APK agent gagal.",
                dependency_exit_code=result.returncode,
            )
        artifact = await asyncio.to_thread(self._validate_artifact, apk, input_sha256, False)
        await asyncio.to_thread(self._write_stamp, artifact)
        logger.info(
            "agent_build_completed",
            extra={
                "request_id": request_id,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "artifact_bytes": artifact.size_bytes,
            },
        )
        return artifact

    def _validate_artifact(
        self,
        apk: Path,
        input_sha256: str,
        reused: bool,
    ) -> AgentArtifact:
        if not apk.is_file() or apk.suffix.lower() != ".apk":
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_FAILED,
                "Build tidak menghasilkan APK agent yang valid.",
            )
        size = apk.stat().st_size
        if not 0 < size <= self._config.max_apk_bytes:
            raise acquisition_error(
                ErrorCategory.AGENT_BUILD_FAILED,
                "Ukuran APK agent hasil build tidak valid.",
            )
        return AgentArtifact(
            path=apk,
            input_sha256=input_sha256,
            apk_sha256=self._file_sha256(apk),
            size_bytes=size,
            reused=reused,
        )

    def invalidate_cache(self) -> None:
        """Remove stamp file so next build is forced fresh."""
        apk = self._config.apk_path.expanduser().resolve()
        stamp = self._stamp_path(apk)
        try:
            stamp.unlink(missing_ok=True)
        except OSError:
            pass

    def _reusable_artifact(self, apk: Path, input_sha256: str) -> AgentArtifact | None:
        if any(
            not path.expanduser().resolve().is_file()
            for path in self._config.required_output_paths
        ):
            return None
        try:
            payload = json.loads(self._stamp_path(apk).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        expected_keys = {
            "schema_version",
            "input_sha256",
            "apk_sha256",
            "size_bytes",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            return None
        if payload.get("schema_version") != 1 or payload.get("input_sha256") != input_sha256:
            return None
        try:
            artifact = self._validate_artifact(apk, input_sha256, True)
        except AcquisitionError:
            return None
        if (
            payload.get("apk_sha256") != artifact.apk_sha256
            or payload.get("size_bytes") != artifact.size_bytes
        ):
            return None
        return artifact

    @staticmethod
    def _input_digest(project: Path) -> str:
        candidates: list[Path] = []
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(project)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if (
                relative.parts[0] in {"app", "automation", "buildSrc", "gradle"}
                or relative.as_posix() in BUILD_ROOT_FILES
                or path.name in BUILD_ROOT_FILES
            ):
                candidates.append(path)
        digest = hashlib.sha256()
        for path in sorted(set(candidates), key=lambda item: item.relative_to(project).as_posix()):
            relative = path.relative_to(project).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_stamp(self, artifact: AgentArtifact) -> None:
        stamp = self._stamp_path(artifact.path)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        partial = stamp.with_name(f"{stamp.name}.partial")
        payload = {
            "schema_version": 1,
            "input_sha256": artifact.input_sha256,
            "apk_sha256": artifact.apk_sha256,
            "size_bytes": artifact.size_bytes,
        }
        partial.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        partial.chmod(0o600)
        os.replace(partial, stamp)
        stamp.chmod(0o600)

    @staticmethod
    def _stamp_path(apk: Path) -> Path:
        return apk.with_suffix(f"{apk.suffix}.build.json")

    def _build_environment(self, input_sha256: str) -> Mapping[str, str]:
        if not len(input_sha256) == 64 or any(ch not in "0123456789abcdef" for ch in input_sha256):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Digest input build agent tidak valid.",
            )
        environment = sanitized_environment()
        environment["SIKSIK_AGENT_BUILD_SHA256"] = input_sha256
        java_home = self._first_directory(
            self._config.java_home,
            Path(environment["JAVA_HOME"]) if environment.get("JAVA_HOME") else None,
            Path("/usr/lib/jvm/java-17-openjdk-amd64"),
            Path("/usr/lib/jvm/java-17-openjdk"),
            Path("/opt/homebrew/opt/openjdk@17"),
            Path("/usr/local/opt/openjdk@17"),
        )
        android_home = self._first_directory(
            self._config.android_home,
            Path(environment["ANDROID_HOME"]) if environment.get("ANDROID_HOME") else None,
            Path(environment["ANDROID_SDK_ROOT"])
            if environment.get("ANDROID_SDK_ROOT")
            else None,
            Path.home() / "Android" / "Sdk",
            Path("/usr/lib/android-sdk"),
            Path("/opt/homebrew/share/android-commandlinetools"),
            Path("/usr/local/share/android-commandlinetools"),
            Path.home() / "Library/Android/sdk",
        )
        if java_home is not None:
            environment["JAVA_HOME"] = str(java_home)
        if android_home is not None:
            environment["ANDROID_HOME"] = str(android_home)
            environment["ANDROID_SDK_ROOT"] = str(android_home)
        return environment

    @staticmethod
    def _first_directory(*candidates: Path | None) -> Path | None:
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                resolved = candidate.expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.is_dir():
                return resolved
        return None
