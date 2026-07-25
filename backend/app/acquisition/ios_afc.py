"""iOS AFC acquisition: gallery/video + shared documents.

Uses in-repo ios-media-puller scripts via its venv (isolated from Android agent).
Does not run a full idevicebackup2 (OOM-prone); that remains an explicit opt-in.

QUICK uses capped counts; FULL with count 0 means uncapped (pull all found).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from app.acquisition.contracts import ProgressCallback
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import run_process
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.ios_afc")

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


def _puller_root() -> Path:
    return settings.ios_media_puller_path.resolve()


def _venv_python() -> Path:
    return _puller_root() / ".venv" / "bin" / "python"


def _count_for_mode(mode: AcquisitionMode, quick: int, full: int) -> int:
    """QUICK uses quick cap; FULL with full<=0 means uncapped (0 passed to puller)."""
    if mode == AcquisitionMode.QUICK:
        return max(1, quick)
    return max(0, full)


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


async def acquire_ios_afc_media(
    session_id: str,
    device_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress: ProgressCallback,
) -> int:
    """Pull DCIM media via AFC. Returns number of staged files."""
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
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    cap_label = "semua" if count == 0 else f"max {count}"
    await on_progress(
        SessionStatus.ACQUIRING,
        18,
        f"iOS AFC media (DCIM, {cap_label})…",
        acquisition_method="ios_afc_media",
    )
    env = {
        **os.environ,
        "UDID": device_id,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
    }
    try:
        result = await run_process(
            [
                str(python_bin),
                str(script),
                "-n",
                str(count),
                "--type",
                "all",
                "-o",
                str(work),
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
    except AcquisitionError:
        shutil.rmtree(work, ignore_errors=True)
        raise

    if result.returncode not in (0,):
        shutil.rmtree(work, ignore_errors=True)
        logger.info(
            "ios_afc_media_failed",
            extra={
                "session_id": session_id,
                "dependency_exit_code": result.returncode,
                "error_category": ErrorCategory.AGENT_UNREACHABLE.value,
            },
        )
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "AFC media iOS gagal menarik DCIM.",
            retryable=True,
            dependency_exit_code=result.returncode,
        )

    moved = _classify_and_move(work, staging)
    shutil.rmtree(work, ignore_errors=True)
    await on_progress(
        SessionStatus.ACQUIRING,
        28,
        f"iOS AFC media selesai ({moved} file)",
        acquisition_method="ios_afc_media",
        files_pulled=moved,
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
        shutil.rmtree(work)
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
    try:
        result = await run_process(
            [
                str(python_bin),
                str(script),
                "-n",
                str(count),
                "-o",
                str(work),
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
        shutil.rmtree(work, ignore_errors=True)
        raise

    # Empty roots → exit 0 with no files is OK (fail-soft).
    if result.returncode not in (0,):
        shutil.rmtree(work, ignore_errors=True)
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

    moved = _classify_and_move(work, staging)
    shutil.rmtree(work, ignore_errors=True)
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
