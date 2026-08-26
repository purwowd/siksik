from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from app.acquisition.adb import AsyncAdbTransport, validate_serial
from app.acquisition.android_recovery.contracts import MediaStoreRow
from app.acquisition.android_recovery.parsers import MEDIA_COLUMNS, parse_media_store_rows
from app.acquisition.android_recovery.paths import (
    STORAGE_UUID,
    canonical_shared_path,
    device_shared_path,
    validate_shared_path,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error

MEDIASTORE_URI = "content://media/external/file?includeTrashed=1"
CONTENT_URI = re.compile(r"^content://media/[A-Za-z0-9_./?=&%+-]{1,512}$")
GALLERY_CACHE_NAME = re.compile(r"(?:imgcache[^/]*|micro|mini|nano)\.idx$", re.IGNORECASE)
CLASSIC_THUMBNAIL_NAME = re.compile(
    r"^(?:thumb[-_.]?)?(\d{1,19})\.(?:jpe?g|png|webp)$",
    re.IGNORECASE,
)
THUMBDATA_NAME = re.compile(r"^\.thumbdata", re.IGNORECASE)

TRASH_FIND_SCRIPT = r"""
find "$1" -type f \( \
  -name '.trashed-*' -o \
  -ipath '*/.Trash/*' -o -ipath '*/.trash/*' -o -ipath '*/Trash/*' -o \
  -ipath '*/.RecycleBin/*' -o -ipath '*/RecycleBin/*' -o -ipath '*/Recycle Bin/*' -o \
  -ipath '*/.trashBin/*' -o -ipath '*/.trashBin_File/*' -o \
  -ipath '*/.FilesByGoogleTrash/*' -o -ipath '*/.FileManagerRecycler/*' -o \
  -ipath '*/Recently Deleted/*' -o -ipath '*/RecentlyDeleted/*' -o \
  -ipath '*/.RecentlyDeleted/*' \
\) -print0 2>/dev/null
""".strip()

CACHE_FIND_SCRIPT = r"""
find "$1" -type f \( \
  -name 'imgcache*.idx' -o -name 'micro.idx' -o -name 'mini.idx' -o -name 'nano.idx' \
\) -print0 2>/dev/null
""".strip()

THUMBNAIL_FIND_SCRIPT = r"""
find "$1" -type f \( -ipath '*/.thumbnails/*' -o -ipath '*/.thumbnail/*' \) \
  -print0 2>/dev/null
""".strip()

DISK_CACHE_FIND_SCRIPT = r"""
find "$1" -type d -iname 'gallery_disk_cache' -print0 2>/dev/null |
while IFS= read -r -d '' dir; do
  find "$dir" -type f ! -name 'journal' ! -name '*.tmp' -print0 2>/dev/null
done
""".strip()

# Keep the path in $1. Passing names containing " - " directly to
# sha256sum can make some Android toybox builds treat '-' as stdin and hang.
PATH_SHA256_SCRIPT = r'sha256sum -- "$1"'
PATH_IS_FILE_SCRIPT = r'test -f "$1"'
PATH_IS_DIR_SCRIPT = r'test -d "$1"'
PATH_STAT_SIZE_SCRIPT = r'stat -c %s "$1"'


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    paths: tuple[str, ...]
    truncated: bool
    failed: bool


@dataclass(frozen=True, slots=True)
class TransferResult:
    captured: bool
    method: str
    size_bytes: int
    reason: str | None = None


class RecoveryAdbGateway:
    def __init__(
        self,
        transport: AsyncAdbTransport,
        *,
        output_limit_bytes: int,
    ) -> None:
        if output_limit_bytes < 1:
            raise ValueError("recovery output limit must be positive")
        self._transport = transport
        self._output_limit_bytes = output_limit_bytes

    async def shared_roots(self, serial: str) -> tuple[str, ...]:
        await self._transport.select_device(serial)
        roots: list[str] = []
        primary = await self._transport.run(
            serial,
            ["shell", "test", "-r", "/sdcard"],
            operation="recovery_primary_storage_probe",
            check=False,
        )
        if primary.returncode == 0:
            roots.append("/sdcard")
        volumes = await self._transport.run(
            serial,
            ["shell", "ls", "-1", "/storage"],
            operation="recovery_volume_discovery",
            check=False,
        )
        if volumes.returncode == 0 and not volumes.output_truncated:
            for name in volumes.stdout.splitlines():
                value = name.strip()
                if not STORAGE_UUID.fullmatch(value):
                    continue
                remote = f"/storage/{value}"
                probe = await self._transport.run(
                    serial,
                    ["shell", "test", "-r", remote],
                    operation="recovery_volume_probe",
                    check=False,
                )
                if probe.returncode == 0:
                    roots.append(remote)
        if not roots:
            raise acquisition_error(
                ErrorCategory.STORAGE_UNAVAILABLE,
                "Shared storage Android tidak dapat dibaca untuk recovery.",
            )
        return tuple(dict.fromkeys(roots))

    async def media_store_rows(
        self,
        serial: str,
        *,
        trashed_only: bool,
        timeout: float,
    ) -> tuple[list[MediaStoreRow], bool, bool]:
        args = [
            "shell",
            "content",
            "query",
            "--user",
            "0",
            "--uri",
            MEDIASTORE_URI,
            "--projection",
            ":".join(MEDIA_COLUMNS),
        ]
        if trashed_only:
            args.extend(["--where", "is_trashed=1"])
        result = await self._transport.run(
            serial,
            args,
            operation="recovery_mediastore_query",
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return [], result.output_truncated, True
        return parse_media_store_rows(result.stdout), result.output_truncated, False

    async def discover_trash(
        self,
        serial: str,
        roots: Sequence[str],
        *,
        timeout: float,
    ) -> DiscoveryResult:
        return await self._discover(serial, roots, TRASH_FIND_SCRIPT, timeout, "recovery_trash_scan")

    async def discover_cache_indexes(
        self,
        serial: str,
        roots: Sequence[str],
        *,
        timeout: float,
    ) -> DiscoveryResult:
        result = await self._discover(
            serial,
            roots,
            CACHE_FIND_SCRIPT,
            timeout,
            "recovery_cache_discovery",
        )
        return DiscoveryResult(
            tuple(path for path in result.paths if GALLERY_CACHE_NAME.search(path)),
            result.truncated,
            result.failed,
        )

    async def discover_thumbnails(
        self,
        serial: str,
        roots: Sequence[str],
        *,
        timeout: float,
    ) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...], bool]:
        result = await self._discover(
            serial,
            roots,
            THUMBNAIL_FIND_SCRIPT,
            timeout,
            "recovery_thumbnail_discovery",
        )
        classic: list[tuple[str, str]] = []
        thumbdata: list[str] = []
        for path in result.paths:
            name = PurePosixPath(path).name
            match = CLASSIC_THUMBNAIL_NAME.fullmatch(name)
            if match:
                classic.append((path, match.group(1)))
            elif THUMBDATA_NAME.match(name):
                thumbdata.append(path)
        return tuple(classic), tuple(thumbdata), result.truncated or result.failed

    async def discover_disk_cache_jpegs(
        self,
        serial: str,
        roots: Sequence[str],
        *,
        timeout: float,
    ) -> DiscoveryResult:
        return await self._discover(
            serial,
            roots,
            DISK_CACHE_FIND_SCRIPT,
            timeout,
            "recovery_disk_cache_discovery",
        )

    async def file_sha256(
        self,
        serial: str,
        path: str,
        roots: Sequence[str],
    ) -> str | None:
        try:
            result = await self._run_path_script(
                serial,
                path,
                roots,
                PATH_SHA256_SCRIPT,
                "recovery_file_sha256",
                timeout=20.0,
            )
        except AcquisitionError:
            return None
        if result.returncode != 0 or not result.stdout:
            return None
        token = result.stdout.strip().split(None, 1)[0].lower()
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            return None
        return token

    async def _discover(
        self,
        serial: str,
        roots: Sequence[str],
        script: str,
        timeout: float,
        operation: str,
    ) -> DiscoveryResult:
        found: set[str] = set()
        truncated = False
        failed = False
        for root in roots:
            validated = validate_shared_path(root, roots)
            result = await self._transport.run(
                serial,
                [
                    "exec-out",
                    "sh",
                    "-c",
                    script,
                    "siksik-recovery",
                    device_shared_path(validated),
                ],
                operation=operation,
                timeout=timeout,
                check=False,
            )
            truncated = truncated or result.output_truncated
            failed = failed or result.returncode != 0
            raw_paths = result.stdout.split("\x00")
            if result.output_truncated and not result.stdout.endswith("\x00"):
                raw_paths = raw_paths[:-1]
            for raw in raw_paths:
                if not raw:
                    continue
                try:
                    found.add(validate_shared_path(canonical_shared_path(raw), roots))
                except AcquisitionError:
                    failed = True
        return DiscoveryResult(tuple(sorted(found)), truncated, failed)

    async def _run_path_script(
        self,
        serial: str,
        path: str,
        roots: Sequence[str],
        script: str,
        operation: str,
        *,
        timeout: float | None = None,
    ):
        validated = validate_shared_path(path, roots)
        return await self._transport.run(
            serial,
            [
                "exec-out",
                "sh",
                "-c",
                script,
                "siksik-recovery",
                device_shared_path(validated),
            ],
            operation=operation,
            timeout=timeout,
            check=False,
        )

    async def is_file(self, serial: str, path: str, roots: Sequence[str]) -> bool:
        result = await self._run_path_script(
            serial,
            path,
            roots,
            PATH_IS_FILE_SCRIPT,
            "recovery_path_probe",
        )
        return result.returncode == 0

    async def is_directory(self, serial: str, path: str, roots: Sequence[str]) -> bool:
        result = await self._run_path_script(
            serial,
            path,
            roots,
            PATH_IS_DIR_SCRIPT,
            "recovery_directory_probe",
        )
        return result.returncode == 0

    async def stat_size(self, serial: str, path: str, roots: Sequence[str]) -> int | None:
        result = await self._run_path_script(
            serial,
            path,
            roots,
            PATH_STAT_SIZE_SCRIPT,
            "recovery_size_probe",
        )
        value = result.stdout.strip()
        return int(value) if result.returncode == 0 and value.isdigit() else None

    async def transfer(
        self,
        serial: str,
        *,
        remote_path: str | None,
        content_uri: str | None,
        roots: Sequence[str],
        destination: Path,
        max_bytes: int,
        timeout: float,
    ) -> TransferResult:
        if max_bytes < 1:
            return TransferResult(False, "none", 0, "byte_budget_exhausted")
        if remote_path:
            result = await self._pull_path(
                serial,
                remote_path,
                roots,
                destination,
                max_bytes,
                timeout,
            )
            if result.captured:
                return result
        if content_uri:
            return await self._stream_content(
                serial,
                content_uri,
                destination,
                max_bytes,
                timeout,
            )
        return TransferResult(False, "none", 0, "source_unreadable")

    async def _pull_path(
        self,
        serial: str,
        remote_path: str,
        roots: Sequence[str],
        destination: Path,
        max_bytes: int,
        timeout: float,
    ) -> TransferResult:
        validated = validate_shared_path(remote_path, roots)
        size = await self.stat_size(serial, validated, roots)
        if size is not None and (size < 1 or size > max_bytes):
            return TransferResult(False, "adb_pull", 0, "source_size_out_of_bounds")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.partial")
        partial.unlink(missing_ok=True)
        try:
            result = await self._transport.run(
                serial,
                ["pull", device_shared_path(validated), str(partial)],
                operation="recovery_file_pull",
                timeout=timeout,
                check=False,
            )
            if result.returncode != 0 or not partial.is_file():
                return TransferResult(False, "adb_pull", 0, "adb_pull_failed")
            actual = partial.stat().st_size
            if actual < 1 or actual > max_bytes:
                return TransferResult(False, "adb_pull", 0, "captured_size_out_of_bounds")
            os.replace(partial, destination)
            return TransferResult(True, "adb_pull", actual)
        finally:
            partial.unlink(missing_ok=True)

    async def _stream_content(
        self,
        serial: str,
        uri: str,
        destination: Path,
        max_bytes: int,
        timeout: float,
    ) -> TransferResult:
        if not CONTENT_URI.fullmatch(uri):
            return TransferResult(False, "mediastore_content_read", 0, "content_uri_invalid")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.partial")
        partial.unlink(missing_ok=True)
        command = (
            str(self._transport.executable),
            "-s",
            validate_serial(serial),
            "exec-out",
            "content",
            "read",
            "--user",
            "0",
            "--uri",
            uri,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise acquisition_error(
                ErrorCategory.ADB_NOT_FOUND,
                "ADB tidak dapat dijalankan untuk recovery.",
            ) from exc
        stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
        total = 0
        completed = False
        try:
            async with asyncio.timeout(timeout):
                assert process.stdout is not None
                with partial.open("wb") as handle:
                    while True:
                        block = await process.stdout.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            process.kill()
                            await process.wait()
                            return TransferResult(
                                False,
                                "mediastore_content_read",
                                0,
                                "content_size_out_of_bounds",
                            )
                        await asyncio.to_thread(handle.write, block)
                await process.wait()
                completed = True
        except TimeoutError:
            return TransferResult(False, "mediastore_content_read", 0, "content_read_timeout")
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            raise acquisition_error(
                ErrorCategory.STORAGE_UNAVAILABLE,
                "Artifact recovery tidak dapat ditulis ke staging.",
            ) from exc
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)
            if not completed:
                partial.unlink(missing_ok=True)
        if process.returncode != 0 or total < 1:
            partial.unlink(missing_ok=True)
            return TransferResult(False, "mediastore_content_read", 0, "content_read_failed")
        try:
            os.replace(partial, destination)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise acquisition_error(
                ErrorCategory.STORAGE_UNAVAILABLE,
                "Artifact recovery tidak dapat dipindahkan ke staging.",
            ) from exc
        return TransferResult(True, "mediastore_content_read", total)

    async def _drain_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        chunk_size = min(64 * 1024, self._output_limit_bytes)
        while True:
            block = await stream.read(chunk_size)
            if not block:
                return
