"""iOS AFC acquisition: gallery/video + shared documents.

Uses in-repo ios-media-puller scripts via its venv (isolated from Android agent).
Does not run a full idevicebackup2 (OOM-prone); that remains an explicit opt-in.

QUICK uses capped counts; FULL with count 0 is uncapped inside its time scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.acquisition.contracts import ProgressCallback
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import run_process
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.ios_afc")
T = TypeVar("T")

PHOTO_EXTS = {
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".dng",
    ".tif",
    ".tiff",
    ".gif",
    ".webp",
}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp"}
IOS_LIBRARY_SOURCES = {
    "ios_hidden",
    "ios_recently_deleted",
    "ios_recovered_cache",
    "ios_deleted_metadata",
}
IOS_LIBRARY_CONTROL = "_ios_library"
IOS_LIBRARY_MANIFEST = "manifest-v1.json"
MAX_IOS_EPOCH_S = 4_102_444_800.0
IOS_MEDIA_MIME_TYPES = {
    "image/gif",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/jxl",
    "image/png",
    "image/tiff",
    "image/webp",
    "image/x-adobe-dng",
    "video/3gpp",
    "video/mp4",
    "video/quicktime",
    "video/x-m4v",
    "video/x-msvideo",
}
IOS_ARTIFACT_NAME_RE = re.compile(
    r"^[0-9a-f]{32}\.(?:3gp|avi|dng|gif|heic|heif|jpeg|jpg|json|jxl|"
    r"m4v|mov|mp4|png|tif|tiff|webp)$"
)


async def _run_blocking(
    operation: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Finish owned filesystem work before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            logger.warning("ios_afc_blocking_operation_failed_after_cancel")
        raise


class _StrictIOSLibraryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IOSLibraryArtifactV1(_StrictIOSLibraryModel):
    relative_path: str = Field(min_length=1, max_length=512)
    source: Literal[
        "ios_hidden",
        "ios_recently_deleted",
        "ios_recovered_cache",
        "ios_deleted_metadata",
    ]
    classification: Literal[
        "hidden_album",
        "recently_deleted_album",
        "photos_thumbnail_cache",
        "ithmb_jpeg_carve",
        "purged_metadata_only",
    ]
    capture_method: Literal["afc_pull", "cache_carve", "photos_database_parse"]
    mime_type: str = Field(min_length=3, max_length=127)
    size_bytes: int = Field(ge=1, le=4_294_967_296)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_uuid: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-"
            r"[0-9A-F]{4}-[0-9A-F]{12}$"
        ),
    )
    original_filename: str | None = Field(default=None, max_length=255)
    captured_epoch_s: float | None = Field(
        default=None,
        ge=946_684_800,
        le=MAX_IOS_EPOCH_S,
    )

    @field_validator("relative_path")
    @classmethod
    def _owned_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 2:
            raise ValueError("iOS library artifact path is invalid")
        if path.parts[0] not in IOS_LIBRARY_SOURCES:
            raise ValueError("iOS library artifact source is invalid")
        if IOS_ARTIFACT_NAME_RE.fullmatch(path.name) is None:
            raise ValueError("iOS library artifact name is invalid")
        return path.as_posix()

    @field_validator("original_filename")
    @classmethod
    def _safe_original_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            PurePosixPath(value).name != value
            or "\\" in value
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("iOS original filename is invalid")
        return value

    @model_validator(mode="after")
    def _source_matches_path(self):
        if PurePosixPath(self.relative_path).parts[0] != self.source:
            raise ValueError("iOS library artifact source does not match its path")
        if self.source in {"ios_hidden", "ios_recently_deleted"}:
            expected = (
                "hidden_album"
                if self.source == "ios_hidden"
                else "recently_deleted_album"
            )
            if (
                self.classification != expected
                or self.capture_method != "afc_pull"
                or self.mime_type not in IOS_MEDIA_MIME_TYPES
                or self.original_filename is None
            ):
                raise ValueError("iOS Photos media provenance is inconsistent")
        elif self.source == "ios_recovered_cache":
            expected_method = (
                "afc_pull"
                if self.classification == "photos_thumbnail_cache"
                else "cache_carve"
            )
            if (
                self.classification
                not in {"photos_thumbnail_cache", "ithmb_jpeg_carve"}
                or self.capture_method != expected_method
                or self.mime_type != "image/jpeg"
            ):
                raise ValueError("iOS Photos cache provenance is inconsistent")
        elif (
            self.classification != "purged_metadata_only"
            or self.capture_method != "photos_database_parse"
            or self.mime_type != "application/json"
            or self.source_uuid is None
        ):
            raise ValueError("iOS Photos purge provenance is inconsistent")
        return self


class IOSLibraryStatsV1(_StrictIOSLibraryModel):
    captured: int = Field(ge=0)
    bytes_captured: int = Field(ge=0)
    by_source: dict[str, int]


class IOSLibraryManifestV1(_StrictIOSLibraryModel):
    schema_version: Literal[1]
    status: Literal["complete", "partial"]
    not_before_epoch_s: float = Field(ge=946_684_800, le=MAX_IOS_EPOCH_S)
    artifacts: list[IOSLibraryArtifactV1] = Field(max_length=20_000)
    stats: IOSLibraryStatsV1
    warnings: list[str] = Field(max_length=128)

    @model_validator(mode="after")
    def _consistent(self):
        if self.stats.captured != len(self.artifacts):
            raise ValueError("iOS library artifact count is inconsistent")
        if self.stats.bytes_captured != sum(item.size_bytes for item in self.artifacts):
            raise ValueError("iOS library byte count is inconsistent")
        counts: dict[str, int] = {}
        for artifact in self.artifacts:
            counts[artifact.source] = counts.get(artifact.source, 0) + 1
        if counts != self.stats.by_source:
            raise ValueError("iOS library source counts are inconsistent")
        if self.warnings != sorted(set(self.warnings)):
            raise ValueError("iOS library warnings must be sorted and unique")
        return self


def _puller_root() -> Path:
    return settings.ios_media_puller_path.resolve()


def _venv_python() -> Path:
    return _puller_root() / ".venv" / "bin" / "python"


def _count_for_mode(mode: AcquisitionMode, quick: int, full: int) -> int:
    selected = quick if mode == AcquisitionMode.QUICK else full
    return max(0, selected)


def _stage_prepare(staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)


def _classify_and_move(src_dir: Path, staging: Path) -> int:
    """Move pulled files into gallery/video/documents under staging."""
    if not src_dir.is_dir():
        return 0
    moved = 0
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in PHOTO_EXTS:
            bucket = "gallery"
        elif ext in VIDEO_EXTS:
            bucket = "video"
        else:
            bucket = "documents"
        dest_dir = staging / bucket
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        if dest.exists():
            dest = dest_dir / f"{moved:04d}_{path.name}"
        try:
            shutil.move(str(path), str(dest))
            moved += 1
        except OSError:
            continue
    return moved


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _load_ios_library_manifest(root: Path) -> IOSLibraryManifestV1:
    path = root / IOS_LIBRARY_MANIFEST
    try:
        return IOSLibraryManifestV1.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Manifest recovery Photos iOS tidak valid.",
        ) from exc


def _verify_ios_library_artifacts(
    root: Path,
    staging: Path,
    manifest: IOSLibraryManifestV1,
) -> list[tuple[Path, Path, bool]]:
    root_resolved = root.resolve()
    staging_resolved = staging.resolve()
    verified: list[tuple[Path, Path, bool]] = []
    for artifact in manifest.artifacts:
        raw_source = root / artifact.relative_path
        source = raw_source.resolve()
        if (
            not source.is_relative_to(root_resolved)
            or raw_source.is_symlink()
            or not source.is_file()
        ):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Artifact recovery Photos iOS tidak valid.",
            )
        digest, size = _sha256_file(source)
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Hash artifact recovery Photos iOS tidak cocok.",
            )
        destination = (staging / artifact.relative_path).resolve()
        if not destination.is_relative_to(staging_resolved):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan artifact recovery Photos iOS tidak valid.",
            )
        destination_exists = destination.exists()
        if destination_exists:
            existing_digest, existing_size = _sha256_file(destination)
            if existing_digest != digest or existing_size != size:
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Artifact recovery Photos iOS bertabrakan dengan file lain.",
                )
        verified.append((source, destination, destination_exists))
    return verified


def _commit_ios_library(root: Path, staging: Path) -> tuple[int, IOSLibraryManifestV1]:
    manifest = _load_ios_library_manifest(root)
    try:
        verified = _verify_ios_library_artifacts(root, staging, manifest)
    except OSError as exc:
        raise acquisition_error(
            ErrorCategory.STORAGE_UNAVAILABLE,
            "Artifact recovery Photos iOS gagal diverifikasi.",
            retryable=True,
        ) from exc

    moved_paths: list[Path] = []
    manifest_partial: Path | None = None
    try:
        for source, destination, destination_exists in verified:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination_exists:
                source.unlink()
                continue
            os.replace(source, destination)
            moved_paths.append(destination)
        control = staging / IOS_LIBRARY_CONTROL
        control.mkdir(parents=True, exist_ok=True)
        manifest_path = control / IOS_LIBRARY_MANIFEST
        manifest_partial = control / f".{IOS_LIBRARY_MANIFEST}.partial"
        manifest_partial.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(manifest_partial, manifest_path)
    except OSError as exc:
        rollback_failed = False
        for destination in reversed(moved_paths):
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        if manifest_partial is not None:
            try:
                manifest_partial.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            logger.warning("ios_photo_library_commit_rollback_failed")
        raise acquisition_error(
            ErrorCategory.STORAGE_UNAVAILABLE,
            "Artifact recovery Photos iOS gagal disimpan.",
            retryable=True,
        ) from exc
    return len(moved_paths), manifest


def ios_library_metadata(staging: Path) -> dict[str, IOSLibraryArtifactV1]:
    control = staging / IOS_LIBRARY_CONTROL / IOS_LIBRARY_MANIFEST
    if not control.is_file():
        return {}
    try:
        manifest = IOSLibraryManifestV1.model_validate_json(control.read_bytes())
    except (OSError, ValueError):
        return {}
    verified: dict[str, IOSLibraryArtifactV1] = {}
    for artifact in manifest.artifacts:
        path = staging / artifact.relative_path
        try:
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(staging.resolve())
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            digest, size = _sha256_file(path)
            if size == artifact.size_bytes and digest == artifact.sha256:
                verified[artifact.relative_path] = artifact
        except (OSError, IsADirectoryError):
            continue
    return verified


def is_ios_library_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return bool(parts and parts[0] in IOS_LIBRARY_SOURCES)


def _library_mode_values(mode: AcquisitionMode) -> dict[str, int]:
    quick = mode == AcquisitionMode.QUICK
    return {
        "hidden": (
            settings.ios_library_quick_hidden_count
            if quick
            else settings.ios_library_full_hidden_count
        ),
        "deleted": (
            settings.ios_library_quick_deleted_count
            if quick
            else settings.ios_library_full_deleted_count
        ),
        "cache": (
            settings.ios_library_quick_cache_count
            if quick
            else settings.ios_library_full_cache_count
        ),
        "metadata": (
            settings.ios_library_quick_metadata_count
            if quick
            else settings.ios_library_full_metadata_count
        ),
        "max_bytes": (
            settings.ios_library_quick_max_bytes
            if quick
            else settings.ios_library_full_max_bytes
        ),
        "cache_entries": (
            settings.ios_library_quick_cache_entry_limit
            if quick
            else settings.ios_library_full_cache_entry_limit
        ),
        "ithmb_sources": (
            settings.ios_library_quick_ithmb_sources
            if quick
            else settings.ios_library_full_ithmb_sources
        ),
    }


async def acquire_ios_afc_media(
    session_id: str,
    device_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress: ProgressCallback,
) -> int:
    """Pull current DCIM plus bounded Hidden/deleted/cache Photos artifacts."""
    if not settings.ios_afc_media_enabled:
        return 0

    python_bin = _venv_python()
    script = _puller_root() / "pull_recent_media.py"
    if not python_bin.is_file() or not script.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "ios-media-puller (AFC media) tidak siap.",
        )

    count = _count_for_mode(
        mode,
        settings.ios_afc_quick_media_count,
        settings.ios_afc_full_media_count,
    )
    work = staging / "_afc_media_work"
    recent_work = work / "recent"
    library_work = work / "library"

    cap_label = "semua" if count == 0 else f"max {count}"
    await on_progress(
        SessionStatus.ACQUIRING,
        18,
        f"iOS AFC media (DCIM, {cap_label})…",
        acquisition_method="ios_afc_media",
    )
    if work.exists():
        await _run_blocking(shutil.rmtree, work)
    work.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "UDID": device_id,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
    }
    from app.acquisition.time_scope import build_time_scope

    scope = build_time_scope(mode)
    recent_error: AcquisitionError | None = None
    recent_succeeded = False
    moved_recent = 0
    try:
        result = await run_process(
            [
                str(python_bin),
                str(script),
                "-n",
                str(count),
                "--type",
                "all",
                "--not-before-epoch-s",
                str(scope.not_before.timestamp()),
                "-o",
                str(recent_work),
            ],
            timeout=settings.ios_afc_timeout_s,
            cwd=_puller_root(),
            env=env,
            check=False,
            output_limit_bytes=256 * 1024,
            operation="ios_afc_media",
            not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
            timeout_category=ErrorCategory.ADB_TIMEOUT,
            failure_category=ErrorCategory.AGENT_UNREACHABLE,
        )
    except AcquisitionError as exc:
        recent_error = exc
    except asyncio.CancelledError:
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        raise
    except Exception:
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        raise
    else:
        recent_succeeded = result.returncode == 0
        if recent_succeeded:
            moved_recent = await _run_blocking(
                _classify_and_move,
                recent_work,
                staging,
            )
        else:
            recent_error = acquisition_error(
                ErrorCategory.AGENT_UNREACHABLE,
                "AFC media iOS gagal menarik DCIM.",
                retryable=True,
                dependency_exit_code=result.returncode,
            )

    moved_library = 0
    library_manifest: IOSLibraryManifestV1 | None = None
    library_error: AcquisitionError | None = None
    if settings.ios_photo_library_recovery_enabled:
        worker = _puller_root() / "pull_library_artifacts.py"
        if not worker.is_file():
            library_error = acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "Worker recovery Photos iOS tidak tersedia.",
            )
        else:
            values = _library_mode_values(mode)
            max_items = (
                values["hidden"]
                + values["deleted"]
                + values["cache"]
                + values["metadata"]
            )
            try:
                await on_progress(
                    SessionStatus.ACQUIRING,
                    23,
                    f"iOS Photos Hidden/deleted/cache ({scope.lookback_months} bulan)…",
                    acquisition_method="ios_photo_library_recovery",
                    ios_library_state="scanning",
                )
            except asyncio.CancelledError:
                await _run_blocking(shutil.rmtree, work, ignore_errors=True)
                raise
            try:
                library_result = await run_process(
                    [
                        str(python_bin),
                        str(worker),
                        "--udid",
                        device_id,
                        "--output",
                        str(library_work),
                        "--not-before-epoch-s",
                        str(scope.not_before.timestamp()),
                        "--hidden-limit",
                        str(values["hidden"]),
                        "--deleted-limit",
                        str(values["deleted"]),
                        "--cache-limit",
                        str(values["cache"]),
                        "--metadata-limit",
                        str(values["metadata"]),
                        "--max-items",
                        str(max_items),
                        "--max-bytes",
                        str(values["max_bytes"]),
                        "--max-file-bytes",
                        str(settings.ios_library_max_file_bytes),
                        "--max-cache-source-bytes",
                        str(settings.ios_library_max_cache_source_bytes),
                        "--cache-entry-limit",
                        str(values["cache_entries"]),
                        "--ithmb-source-limit",
                        str(values["ithmb_sources"]),
                    ],
                    timeout=settings.ios_library_timeout_s,
                    cwd=_puller_root(),
                    env=env,
                    check=False,
                    output_limit_bytes=256 * 1024,
                    operation="ios_photo_library_recovery",
                    not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
                    timeout_category=ErrorCategory.ADB_TIMEOUT,
                    failure_category=ErrorCategory.AGENT_UNREACHABLE,
                )
                if library_result.returncode == 0:
                    moved_library, library_manifest = await _run_blocking(
                        _commit_ios_library,
                        library_work,
                        staging,
                    )
                else:
                    library_error = acquisition_error(
                        ErrorCategory.AGENT_UNREACHABLE,
                        "Recovery Photos iOS gagal.",
                        retryable=True,
                        dependency_exit_code=library_result.returncode,
                    )
            except AcquisitionError as exc:
                library_error = exc
            except asyncio.CancelledError:
                await _run_blocking(shutil.rmtree, work, ignore_errors=True)
                raise
            except Exception:
                await _run_blocking(shutil.rmtree, work, ignore_errors=True)
                raise

    if recent_error is not None:
        logger.info(
            "ios_afc_media_failed",
            extra={
                "session_id": session_id,
                "dependency_exit_code": recent_error.dependency_exit_code,
                "error_category": recent_error.category.value,
            },
        )
    if library_error is not None:
        logger.info(
            "ios_photo_library_recovery_failed",
            extra={
                "session_id": session_id,
                "dependency_exit_code": library_error.dependency_exit_code,
                "error_category": library_error.category.value,
            },
        )

    moved = moved_recent + moved_library
    await _run_blocking(shutil.rmtree, work, ignore_errors=True)
    if not recent_succeeded and library_manifest is None:
        raise recent_error or library_error or acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "Akuisisi media iOS tidak tersedia.",
            retryable=True,
        )

    by_source = library_manifest.stats.by_source if library_manifest is not None else {}
    if library_manifest is not None:
        library_state = library_manifest.status
        library_warning_count = len(library_manifest.warnings)
    elif settings.ios_photo_library_recovery_enabled:
        library_state = "unavailable"
        library_warning_count = int(library_error is not None)
    else:
        library_state = "disabled"
        library_warning_count = 0
    await on_progress(
        SessionStatus.ACQUIRING,
        28,
        f"iOS AFC/Photos selesai ({moved} file)",
        acquisition_method="ios_afc_media",
        files_pulled=moved,
        ios_library_state=library_state,
        ios_hidden_captured=by_source.get("ios_hidden", 0),
        ios_recently_deleted_captured=by_source.get("ios_recently_deleted", 0),
        ios_cache_captured=by_source.get("ios_recovered_cache", 0),
        ios_deleted_metadata_captured=by_source.get("ios_deleted_metadata", 0),
        ios_library_warning_count=library_warning_count,
    )
    return moved


async def acquire_ios_afc_docs(
    session_id: str,
    device_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress: ProgressCallback,
) -> int:
    """Pull shared documents (PDF/Office/text) via AFC allowlisted roots."""
    if not settings.ios_afc_docs_enabled:
        return 0

    python_bin = _venv_python()
    script = _puller_root() / "pull_shared_docs.py"
    if not python_bin.is_file() or not script.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "ios-media-puller (AFC docs) tidak siap.",
        )

    count = _count_for_mode(
        mode,
        settings.ios_afc_quick_docs_count,
        settings.ios_afc_full_docs_count,
    )
    work = staging / "_afc_docs_work"
    if work.exists():
        await _run_blocking(shutil.rmtree, work)
    work.mkdir(parents=True, exist_ok=True)

    cap_label = "semua" if count == 0 else f"max {count}"
    await on_progress(
        SessionStatus.ACQUIRING,
        30,
        f"iOS AFC dokumen ({cap_label})…",
        acquisition_method="ios_afc_docs",
    )
    env = {
        **os.environ,
        "UDID": device_id,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
    }
    from app.acquisition.time_scope import build_time_scope

    scope = build_time_scope(mode)
    try:
        result = await run_process(
            [
                str(python_bin),
                str(script),
                "-n",
                str(count),
                "-o",
                str(work),
                "--not-before-epoch-s",
                str(scope.not_before.timestamp()),
            ],
            timeout=settings.ios_afc_timeout_s,
            cwd=_puller_root(),
            env=env,
            check=False,
            output_limit_bytes=256 * 1024,
            operation="ios_afc_docs",
            not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
            timeout_category=ErrorCategory.ADB_TIMEOUT,
            failure_category=ErrorCategory.AGENT_UNREACHABLE,
        )
    except AcquisitionError:
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        raise
    except asyncio.CancelledError:
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        raise
    except Exception:
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        raise

    # Empty roots → exit 0 with no files is OK (fail-soft).
    if result.returncode not in (0,):
        await _run_blocking(shutil.rmtree, work, ignore_errors=True)
        logger.info(
            "ios_afc_docs_failed",
            extra={
                "session_id": session_id,
                "dependency_exit_code": result.returncode,
                "error_category": ErrorCategory.AGENT_UNREACHABLE.value,
            },
        )
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "AFC dokumen iOS gagal.",
            retryable=True,
            dependency_exit_code=result.returncode,
        )

    moved = await _run_blocking(_classify_and_move, work, staging)
    await _run_blocking(shutil.rmtree, work, ignore_errors=True)
    await on_progress(
        SessionStatus.ACQUIRING,
        36,
        f"iOS AFC dokumen selesai ({moved} file)",
        acquisition_method="ios_afc_docs",
        files_pulled=moved,
    )
    return moved


def ensure_ios_staging(staging: Path) -> None:
    _stage_prepare(staging)
