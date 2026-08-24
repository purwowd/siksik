from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import mmap
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.android_recovery.contracts import (
    MediaIndex,
    RecoveryArtifactV1,
    RecoveryConfidence,
    RecoveryManifestV1,
    RecoveryRunResult,
    RecoverySource,
    RecoveryStatsV1,
    TrashCandidate,
)
from app.acquisition.android_recovery.gateway import RecoveryAdbGateway
from app.acquisition.android_recovery.parsers import (
    find_images,
    gallery_cache_records,
    is_control_file,
    is_trash_path,
    parse_gallery_index,
    trash_expires,
    trash_original_name,
)
from app.acquisition.android_recovery.paths import (
    RECOVERY_ROOT,
    normalized_relative_path,
    safe_extension,
    stable_candidate_id,
    validate_shared_path,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.android_recovery")

CONTROL_DIR = "_android_recovery"
MANIFEST_NAME = "manifest-v1.json"
PAYLOAD_DIR = PurePosixPath(RECOVERY_ROOT, "trash")
PREVIEW_DIR = PurePosixPath(RECOVERY_ROOT, "previews")
MIME_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    max_items: int
    max_bytes: int
    max_file_bytes: int
    scan_timeout_s: float
    query_timeout_s: float
    transfer_timeout_s: float
    recover_cache: bool


@dataclass(slots=True)
class MutableStats:
    candidates_discovered: int = 0
    payloads_captured: int = 0
    payloads_failed: int = 0
    payloads_skipped: int = 0
    duplicate_payloads: int = 0
    bytes_captured: int = 0
    cache_sources_scanned: int = 0
    cache_candidates_recovered: int = 0
    cache_scan_completed: bool = False

    def freeze(self) -> RecoveryStatsV1:
        return RecoveryStatsV1.model_validate(asdict(self))


class AndroidRecoveryService:
    def __init__(self, gateway: RecoveryAdbGateway | None = None) -> None:
        self._gateway = gateway or RecoveryAdbGateway(
            AsyncAdbTransport(
                settings.adb_path,
                timeout_seconds=settings.adb_command_timeout_s,
                output_limit_bytes=settings.android_recovery_output_limit_bytes,
            ),
            output_limit_bytes=settings.android_recovery_output_limit_bytes,
        )

    async def recover(
        self,
        *,
        session_id: str,
        serial: str,
        mode: AcquisitionMode,
        staging: Path,
        on_progress,
        request_id: str | None,
    ) -> RecoveryRunResult:
        started = time.perf_counter()
        root = staging.expanduser().resolve()
        if not root.is_dir():
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Staging recovery Android tidak valid.",
            )
        policy = self._policy(mode)
        existing = await asyncio.to_thread(load_valid_manifest, root)
        if (
            existing is not None
            and existing.mode == mode
            and len(existing.artifacts) <= policy.max_items
            and existing.stats.bytes_captured <= policy.max_bytes
            and all(item.size_bytes <= policy.max_file_bytes for item in existing.artifacts)
            and (not policy.recover_cache or existing.stats.cache_scan_completed)
        ):
            return RecoveryRunResult(root, existing, (time.perf_counter() - started) * 1000)

        control = (root / CONTROL_DIR).resolve()
        payload_root = (root / PAYLOAD_DIR.as_posix()).resolve()
        preview_root = (root / PREVIEW_DIR.as_posix()).resolve()
        for path in (control, payload_root, preview_root):
            if not path.is_relative_to(root):
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Direktori recovery keluar dari staging sesi.",
                )
        await asyncio.to_thread(self._prepare_directories, control, payload_root, preview_root)
        await on_progress(
            SessionStatus.ACQUIRING,
            60.0,
            f"Memindai sampah Android ({mode.value})",
            recovery_state="scanning",
            recovery_mode=mode.value,
        )

        roots = await self._gateway.shared_roots(serial)
        warnings: set[str] = set()
        stats = MutableStats()
        artifacts: list[RecoveryArtifactV1] = []
        known_hashes: set[str] = set()

        candidates = await self._trash_candidates(serial, roots, policy, mode, warnings)
        stats.candidates_discovered = len(candidates)
        for candidate in candidates:
            if len(artifacts) >= policy.max_items or stats.bytes_captured >= policy.max_bytes:
                stats.payloads_skipped += 1
                warnings.add("recovery_budget_truncated")
                continue
            artifact = await self._capture_candidate(
                serial,
                roots,
                root,
                candidate,
                policy,
                stats,
                known_hashes,
            )
            if artifact is not None:
                artifacts.append(artifact)

        if policy.recover_cache:
            await self._recover_cache_previews(
                serial,
                roots,
                root,
                control,
                policy,
                artifacts,
                stats,
                known_hashes,
                warnings,
            )
            stats.cache_scan_completed = True

        partial = bool(warnings or stats.payloads_failed or stats.payloads_skipped)
        manifest = RecoveryManifestV1(
            mode=mode,
            status="partial" if partial else "complete",
            artifacts=artifacts,
            stats=stats.freeze(),
            warnings=sorted(warnings),
        )
        await asyncio.to_thread(self._write_manifest, control / MANIFEST_NAME, manifest)
        duration_ms = (time.perf_counter() - started) * 1000
        await on_progress(
            SessionStatus.ACQUIRING,
            60.0,
            f"Recovery Android selesai ({len(artifacts)} item)",
            recovery_state=manifest.status,
            recovery_mode=mode.value,
            recovery_candidates=stats.candidates_discovered,
            recovery_captured=len(artifacts),
            recovery_bytes=stats.bytes_captured,
            recovery_warning_count=len(warnings),
            recovery_duration_ms=round(duration_ms, 1),
            recovery_cache_sources=stats.cache_sources_scanned,
            recovery_cache_captured=stats.cache_candidates_recovered,
        )
        logger.info(
            "android_recovery_completed",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "phase": mode.value,
                "state": manifest.status,
                "item_count": len(artifacts),
                "byte_count": stats.bytes_captured,
                "duration_ms": round(duration_ms),
                "warning_count": len(warnings),
            },
        )
        return RecoveryRunResult(root, manifest, duration_ms)

    @staticmethod
    def _policy(mode: AcquisitionMode) -> RecoveryPolicy:
        quick = mode == AcquisitionMode.QUICK
        return RecoveryPolicy(
            max_items=(
                settings.android_recovery_quick_max_items
                if quick
                else settings.android_recovery_full_max_items
            ),
            max_bytes=(
                settings.android_recovery_quick_max_bytes
                if quick
                else settings.android_recovery_full_max_bytes
            ),
            max_file_bytes=settings.android_recovery_max_file_bytes,
            scan_timeout_s=(
                settings.android_recovery_quick_scan_timeout_s
                if quick
                else settings.android_recovery_full_scan_timeout_s
            ),
            query_timeout_s=settings.android_recovery_query_timeout_s,
            transfer_timeout_s=settings.android_recovery_transfer_timeout_s,
            recover_cache=True,
        )

    async def _trash_candidates(
        self,
        serial: str,
        roots: Sequence[str],
        policy: RecoveryPolicy,
        mode: AcquisitionMode,
        warnings: set[str],
    ) -> list[TrashCandidate]:
        candidates: dict[str, TrashCandidate] = {}
        rows, truncated, failed = await self._gateway.media_store_rows(
            serial,
            trashed_only=True,
            timeout=policy.query_timeout_s,
        )
        if truncated:
            warnings.add("mediastore_trash_query_truncated")
        if failed:
            warnings.add("mediastore_trash_query_failed")
        for row in rows:
            if not row.is_trashed:
                continue
            path: str | None = None
            if row.path:
                try:
                    path = validate_shared_path(row.path, roots)
                except AcquisitionError:
                    warnings.add("mediastore_path_rejected")
            identity = path or f"media:{row.media_id}"
            candidate = TrashCandidate(
                candidate_id=stable_candidate_id(RecoverySource.MEDIASTORE_TRASH.value, identity),
                source=RecoverySource.MEDIASTORE_TRASH,
                remote_path=path,
                content_uri=f"content://media/external/file/{row.media_id}?includeTrashed=1",
                display_name=row.display_name,
                mime_type=row.mime_type,
                reported_size=row.size_bytes,
                expires_epoch_s=(
                    row.expires_epoch_s
                    if row.expires_epoch_s is not None and row.expires_epoch_s > 0
                    else None
                ),
            )
            candidates[identity] = candidate

        scan_roots = list(roots)
        if mode == AcquisitionMode.QUICK:
            scan_roots = []
            for root in roots:
                for suffix in (
                    "Android",
                    "DCIM",
                    "Pictures",
                    "Movies",
                    "Download",
                    "MIUI",
                ):
                    value = f"{root.rstrip('/')}/{suffix}"
                    if await self._gateway.is_directory(serial, value, roots):
                        scan_roots.append(value)
        discovery = await self._gateway.discover_trash(
            serial,
            scan_roots,
            timeout=policy.scan_timeout_s,
        )
        if discovery.truncated:
            warnings.add("filesystem_trash_scan_truncated")
        if discovery.failed:
            warnings.add("filesystem_trash_scan_partial")
        for raw_path in discovery.paths:
            path = validate_shared_path(raw_path, roots)
            if not is_trash_path(path) or is_control_file(path):
                continue
            if path in candidates:
                continue
            size = await self._gateway.stat_size(serial, path, roots)
            candidates[path] = TrashCandidate(
                candidate_id=stable_candidate_id(RecoverySource.FILESYSTEM_TRASH.value, path),
                source=RecoverySource.FILESYSTEM_TRASH,
                remote_path=path,
                content_uri=None,
                display_name=trash_original_name(path),
                mime_type=mimetypes.guess_type(path)[0],
                reported_size=size,
                expires_epoch_s=trash_expires(path),
            )
        return sorted(
            candidates.values(),
            key=lambda item: (
                -(item.expires_epoch_s or 0),
                item.source.value,
                item.candidate_id,
            ),
        )

    async def _capture_candidate(
        self,
        serial: str,
        roots: Sequence[str],
        staging: Path,
        candidate: TrashCandidate,
        policy: RecoveryPolicy,
        stats: MutableStats,
        known_hashes: set[str],
    ) -> RecoveryArtifactV1 | None:
        remaining = min(policy.max_file_bytes, policy.max_bytes - stats.bytes_captured)
        if candidate.reported_size is not None and (
            candidate.reported_size < 1 or candidate.reported_size > remaining
        ):
            stats.payloads_skipped += 1
            return None
        extension = safe_extension(candidate.display_name, candidate.mime_type)
        relative = (PAYLOAD_DIR / f"{candidate.candidate_id}{extension}").as_posix()
        destination = (staging / relative).resolve()
        if not destination.is_relative_to(staging):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan recovery keluar dari staging sesi.",
            )
        result = await self._gateway.transfer(
            serial,
            remote_path=candidate.remote_path,
            content_uri=candidate.content_uri,
            roots=roots,
            destination=destination,
            max_bytes=remaining,
            timeout=policy.transfer_timeout_s,
        )
        if not result.captured:
            stats.payloads_failed += 1
            return None
        digest, size = await asyncio.to_thread(_sha256_file, destination)
        if digest in known_hashes:
            destination.unlink(missing_ok=True)
            stats.duplicate_payloads += 1
            return None
        known_hashes.add(digest)
        stats.payloads_captured += 1
        stats.bytes_captured += size
        mime = detect_recovery_mime_type(destination, candidate.mime_type)
        return RecoveryArtifactV1(
            candidate_id=candidate.candidate_id,
            relative_path=relative,
            source=candidate.source.value,
            classification="trash_resident",
            confidence=RecoveryConfidence.HIGH.value,
            capture_method=result.method,
            mime_type=mime,
            size_bytes=size,
            sha256=digest,
            expires_epoch_s=candidate.expires_epoch_s,
        )

    async def _recover_cache_previews(
        self,
        serial: str,
        roots: Sequence[str],
        staging: Path,
        control: Path,
        policy: RecoveryPolicy,
        artifacts: list[RecoveryArtifactV1],
        stats: MutableStats,
        known_hashes: set[str],
        warnings: set[str],
    ) -> None:
        rows, truncated, failed = await self._gateway.media_store_rows(
            serial,
            trashed_only=False,
            timeout=policy.query_timeout_s,
        )
        if truncated or failed:
            warnings.add("mediastore_index_truncated_cache_skipped")
            return
        media_index = MediaIndex(
            ids=frozenset(row.media_id for row in rows),
            paths=frozenset(row.path for row in rows if row.path),
        )
        temp_root = control / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            await self._recover_gallery_cache(
                serial,
                roots,
                staging,
                temp_root,
                media_index,
                policy,
                artifacts,
                stats,
                known_hashes,
                warnings,
            )
            await self._recover_thumbnails(
                serial,
                roots,
                staging,
                temp_root,
                media_index,
                policy,
                artifacts,
                stats,
                known_hashes,
                warnings,
            )
            await self._recover_disk_cache_jpegs(
                serial,
                roots,
                staging,
                temp_root,
                media_index,
                policy,
                artifacts,
                stats,
                known_hashes,
                warnings,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_root, True)

    async def _recover_gallery_cache(
        self,
        serial: str,
        roots: Sequence[str],
        staging: Path,
        temp_root: Path,
        media_index: MediaIndex,
        policy: RecoveryPolicy,
        artifacts: list[RecoveryArtifactV1],
        stats: MutableStats,
        known_hashes: set[str],
        warnings: set[str],
    ) -> None:
        discovery = await self._gateway.discover_cache_indexes(
            serial,
            roots,
            timeout=policy.scan_timeout_s,
        )
        if discovery.truncated or discovery.failed:
            warnings.add("gallery_cache_discovery_partial")
        for index_number, index_remote in enumerate(discovery.paths):
            if self._budget_reached(policy, artifacts, stats):
                warnings.add("recovery_budget_truncated")
                return
            stats.cache_sources_scanned += 1
            index_local = temp_root / f"gallery_{index_number}.idx"
            capture = await self._gateway.transfer(
                serial,
                remote_path=index_remote,
                content_uri=None,
                roots=roots,
                destination=index_local,
                max_bytes=settings.android_recovery_max_cache_source_bytes,
                timeout=policy.transfer_timeout_s,
            )
            if not capture.captured:
                warnings.add("gallery_cache_index_unreadable")
                continue
            try:
                header, offsets = await asyncio.to_thread(_read_gallery_index, index_local)
            except (AcquisitionError, OSError):
                warnings.add("gallery_cache_index_invalid")
                continue
            base = index_remote[:-4]
            for region in (0, 1):
                data_remote = f"{base}.{region}"
                if not await self._gateway.is_file(serial, data_remote, roots):
                    continue
                data_local = temp_root / f"gallery_{index_number}_{region}.data"
                data_capture = await self._gateway.transfer(
                    serial,
                    remote_path=data_remote,
                    content_uri=None,
                    roots=roots,
                    destination=data_local,
                    max_bytes=settings.android_recovery_max_cache_source_bytes,
                    timeout=policy.transfer_timeout_s,
                )
                if not data_capture.captured:
                    warnings.add("gallery_cache_data_unreadable")
                    continue
                try:
                    expected = (
                        header["active_bytes"]
                        if region == header["active_region"]
                        else None
                    )
                    records = await asyncio.to_thread(
                        _read_gallery_records,
                        data_local,
                        expected,
                        offsets[region],
                    )
                    for record in records:
                        if self._budget_reached(policy, artifacts, stats):
                            warnings.add("recovery_budget_truncated")
                            return
                        if record.media_id and record.media_id in media_index.ids:
                            continue
                        if record.original_path:
                            if record.original_path in media_index.paths:
                                continue
                            try:
                                if await self._gateway.is_file(
                                    serial, record.original_path, roots
                                ):
                                    continue
                            except AcquisitionError:
                                warnings.add("gallery_reference_probe_failed")
                                continue
                            classification = "source_missing"
                            confidence = (
                                RecoveryConfidence.HIGH
                                if record.media_id
                                else RecoveryConfidence.MEDIUM
                            )
                        elif record.media_id:
                            classification = "orphan_mediastore_id"
                            confidence = RecoveryConfidence.MEDIUM
                        else:
                            continue
                        start = record.blob_offset + 20 + record.image.offset
                        encoded = await asyncio.to_thread(
                            _read_slice,
                            data_local,
                            start,
                            record.image.end - record.image.offset,
                        )
                        identity = f"{index_remote}:{region}:{record.blob_offset}:{record.image.offset}"
                        artifact = await asyncio.to_thread(
                            self._store_preview,
                            staging,
                            stable_candidate_id(RecoverySource.GALLERY_CACHE.value, identity),
                            RecoverySource.GALLERY_CACHE,
                            classification,
                            confidence,
                            encoded,
                            record.image,
                            policy,
                            stats,
                            known_hashes,
                        )
                        if artifact is not None:
                            artifacts.append(artifact)
                except (AcquisitionError, OSError, ValueError):
                    warnings.add("gallery_cache_parse_failed")

    async def _recover_thumbnails(
        self,
        serial: str,
        roots: Sequence[str],
        staging: Path,
        temp_root: Path,
        media_index: MediaIndex,
        policy: RecoveryPolicy,
        artifacts: list[RecoveryArtifactV1],
        stats: MutableStats,
        known_hashes: set[str],
        warnings: set[str],
    ) -> None:
        classic, thumbdata, partial = await self._gateway.discover_thumbnails(
            serial,
            roots,
            timeout=policy.scan_timeout_s,
        )
        if partial:
            warnings.add("thumbnail_discovery_partial")
        for index, (remote, media_id) in enumerate(classic):
            if self._budget_reached(policy, artifacts, stats):
                warnings.add("recovery_budget_truncated")
                return
            if media_id in media_index.ids:
                continue
            stats.cache_sources_scanned += 1
            local = temp_root / f"classic_{index}.bin"
            capture = await self._gateway.transfer(
                serial,
                remote_path=remote,
                content_uri=None,
                roots=roots,
                destination=local,
                max_bytes=settings.android_recovery_max_cache_source_bytes,
                timeout=policy.transfer_timeout_s,
            )
            if not capture.captured:
                continue
            try:
                raw = await asyncio.to_thread(local.read_bytes)
            except OSError:
                warnings.add("classic_thumbnail_read_failed")
                continue
            images = await asyncio.to_thread(_whole_file_images, raw)
            if not images:
                continue
            image = max(images, key=lambda item: item.end - item.offset)
            artifact = await asyncio.to_thread(
                self._store_preview,
                staging,
                stable_candidate_id(RecoverySource.CLASSIC_THUMBNAIL.value, remote),
                RecoverySource.CLASSIC_THUMBNAIL,
                "orphan_mediastore_id",
                RecoveryConfidence.MEDIUM,
                raw[image.offset : image.end],
                image,
                policy,
                stats,
                known_hashes,
            )
            if artifact is not None:
                artifacts.append(artifact)

        active_slots = {int(value) % 5000 for value in media_index.ids if value.isdigit()}
        for index, remote in enumerate(thumbdata):
            if self._budget_reached(policy, artifacts, stats):
                warnings.add("recovery_budget_truncated")
                return
            stats.cache_sources_scanned += 1
            local = temp_root / f"thumbdata_{index}.bin"
            capture = await self._gateway.transfer(
                serial,
                remote_path=remote,
                content_uri=None,
                roots=roots,
                destination=local,
                max_bytes=settings.android_recovery_max_cache_source_bytes,
                timeout=policy.transfer_timeout_s,
            )
            if not capture.captured:
                continue
            try:
                remaining_items = policy.max_items - len(artifacts)
                remaining_bytes = policy.max_bytes - stats.bytes_captured
                carved = await asyncio.to_thread(
                    _read_thumbdata_candidates,
                    local,
                    active_slots,
                    remaining_items,
                    remaining_bytes,
                )
                for image, encoded in carved:
                    identity = f"{remote}:{image.offset}"
                    artifact = await asyncio.to_thread(
                        self._store_preview,
                        staging,
                        stable_candidate_id(RecoverySource.THUMBDATA.value, identity),
                        RecoverySource.THUMBDATA,
                        "unmatched_thumbdata_slot",
                        RecoveryConfidence.LOW,
                        encoded,
                        image,
                        policy,
                        stats,
                        known_hashes,
                    )
                    if artifact is not None:
                        artifacts.append(artifact)
                    if self._budget_reached(policy, artifacts, stats):
                        warnings.add("recovery_budget_truncated")
                        return
            except (OSError, ValueError):
                warnings.add("thumbdata_parse_failed")

    async def _recover_disk_cache_jpegs(
        self,
        serial: str,
        roots: Sequence[str],
        staging: Path,
        temp_root: Path,
        media_index: MediaIndex,
        policy: RecoveryPolicy,
        artifacts: list[RecoveryArtifactV1],
        stats: MutableStats,
        known_hashes: set[str],
        warnings: set[str],
    ) -> None:
        """OEM DiskLruCache JPEGs (Xiaomi gallery_disk_cache) after trash is emptied.

        Infinix/Samsung keep Gallery3D imgcache*.idx; that path is unchanged.
        Skip blobs whose hash matches a live MediaStore file so current photos
        are not re-imported as recovery.
        """
        discovery = await self._gateway.discover_disk_cache_jpegs(
            serial,
            roots,
            timeout=policy.scan_timeout_s,
        )
        if discovery.truncated or discovery.failed:
            warnings.add("disk_cache_discovery_partial")
        if not discovery.paths:
            return
        live_hashes = set(known_hashes)
        for path in media_index.paths:
            try:
                digest = await self._gateway.file_sha256(serial, path, roots)
            except AcquisitionError:
                continue
            if digest:
                live_hashes.add(digest)
        for index, remote in enumerate(discovery.paths):
            if self._budget_reached(policy, artifacts, stats):
                warnings.add("recovery_budget_truncated")
                return
            stats.cache_sources_scanned += 1
            local = temp_root / f"disk_cache_{index}.bin"
            capture = await self._gateway.transfer(
                serial,
                remote_path=remote,
                content_uri=None,
                roots=roots,
                destination=local,
                max_bytes=settings.android_recovery_max_cache_source_bytes,
                timeout=policy.transfer_timeout_s,
            )
            if not capture.captured:
                continue
            try:
                raw = await asyncio.to_thread(local.read_bytes)
            except OSError:
                warnings.add("disk_cache_read_failed")
                continue
            images = await asyncio.to_thread(_whole_file_images, raw)
            if not images:
                continue
            image = max(images, key=lambda item: item.end - item.offset)
            encoded = raw[image.offset : image.end]
            digest = hashlib.sha256(encoded).hexdigest()
            if digest in live_hashes:
                stats.duplicate_payloads += 1
                continue
            artifact = await asyncio.to_thread(
                self._store_preview,
                staging,
                stable_candidate_id(RecoverySource.CLASSIC_THUMBNAIL.value, remote),
                RecoverySource.CLASSIC_THUMBNAIL,
                "orphan_disk_cache",
                RecoveryConfidence.MEDIUM,
                encoded,
                image,
                policy,
                stats,
                known_hashes,
            )
            if artifact is not None:
                artifacts.append(artifact)
                live_hashes.add(digest)

    @staticmethod
    def _store_preview(
        staging: Path,
        candidate_id: str,
        source: RecoverySource,
        classification: str,
        confidence: RecoveryConfidence,
        encoded: bytes,
        image,
        policy: RecoveryPolicy,
        stats: MutableStats,
        known_hashes: set[str],
    ) -> RecoveryArtifactV1 | None:
        size = len(encoded)
        if size < 1 or size > min(policy.max_file_bytes, policy.max_bytes - stats.bytes_captured):
            stats.payloads_skipped += 1
            return None
        digest = hashlib.sha256(encoded).hexdigest()
        if digest in known_hashes:
            stats.duplicate_payloads += 1
            return None
        relative = (PREVIEW_DIR / f"{candidate_id}{image.extension}").as_posix()
        destination = (staging / relative).resolve()
        if not destination.is_relative_to(staging):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Tujuan preview recovery tidak valid.",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.partial")
        try:
            partial.write_bytes(encoded)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
        known_hashes.add(digest)
        stats.payloads_captured += 1
        stats.cache_candidates_recovered += 1
        stats.bytes_captured += size
        return RecoveryArtifactV1(
            candidate_id=candidate_id,
            relative_path=relative,
            source=source.value,
            classification=classification,
            confidence=confidence.value,
            capture_method="cache_carve",
            mime_type={"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[image.format],
            size_bytes=size,
            sha256=digest,
            width=image.width,
            height=image.height,
        )

    @staticmethod
    def _budget_reached(
        policy: RecoveryPolicy,
        artifacts: Sequence[RecoveryArtifactV1],
        stats: MutableStats,
    ) -> bool:
        return len(artifacts) >= policy.max_items or stats.bytes_captured >= policy.max_bytes

    @staticmethod
    def _prepare_directories(control: Path, payload: Path, preview: Path) -> None:
        for path in (control, payload, preview):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_manifest(path: Path, manifest: RecoveryManifestV1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.partial")
        try:
            partial.write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _safe_mime_type(value: str | None, filename: str) -> str:
    candidate = (value or "").strip()
    if MIME_TYPE.fullmatch(candidate):
        return candidate
    guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return guessed if MIME_TYPE.fullmatch(guessed) else "application/octet-stream"


def detect_recovery_mime_type(path: Path, declared: str | None = None) -> str:
    """Resolve recovery payload MIME from bytes when provider metadata is generic.

    Filesystem trash entries commonly have opaque names, so Android reports no
    useful MIME and the payload is staged as ``.bin``.  The manifest must still
    describe the captured bytes accurately; otherwise a valid image is dropped
    by indexing and cannot be previewed.
    """
    candidate = _safe_mime_type(declared, path.name)
    if candidate != "application/octet-stream":
        return candidate
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
    except OSError:
        return candidate
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
        return "video/mp4"
    return candidate


def _read_gallery_index(path: Path) -> tuple[dict[str, int], dict[int, list[int]]]:
    return parse_gallery_index(path.read_bytes())


def _read_gallery_records(path: Path, expected: int | None, offsets: Sequence[int]):
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        return gallery_cache_records(
            mapped,
            expected_bytes=expected,
            salvage_offsets=offsets,
        )


def _read_slice(path: Path, offset: int, length: int) -> bytes:
    if offset < 0 or length < 1:
        raise ValueError("cache slice is invalid")
    with path.open("rb") as handle:
        handle.seek(offset)
        value = handle.read(length)
    if len(value) != length:
        raise ValueError("cache slice is incomplete")
    return value


def _whole_file_images(raw: bytes):
    return [item for item in find_images(raw) if item.offset == 0]


def _read_thumbdata_candidates(
    path: Path,
    active_slots: set[int],
    max_items: int,
    max_bytes: int,
):
    output = []
    captured_bytes = 0
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        for image in find_images(mapped):
            slot = image.offset // 10_000
            if slot >= 5000 or image.offset % 10_000 > 64 or slot in active_slots:
                continue
            size = image.end - image.offset
            if len(output) >= max_items or captured_bytes + size > max_bytes:
                break
            output.append((image, bytes(mapped[image.offset : image.end])))
            captured_bytes += size
    return output


def cleanup_recovery_staging(staging: Path) -> None:
    try:
        root = staging.resolve()
    except (OSError, RuntimeError):
        return
    for relative in (CONTROL_DIR, PAYLOAD_DIR.as_posix(), PREVIEW_DIR.as_posix()):
        unresolved = root / relative
        if unresolved.is_symlink():
            try:
                unresolved.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        try:
            target = unresolved.resolve()
        except (OSError, RuntimeError):
            continue
        if target.is_relative_to(root) and target != root:
            shutil.rmtree(target, ignore_errors=True)


def manifest_path(staging: Path) -> Path:
    return staging / CONTROL_DIR / MANIFEST_NAME


def load_valid_manifest(staging: Path) -> RecoveryManifestV1 | None:
    path = manifest_path(staging)
    if not path.is_file():
        return None
    try:
        manifest = RecoveryManifestV1.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None
    root = staging.resolve()
    for artifact in manifest.artifacts:
        try:
            relative = normalized_relative_path(artifact.relative_path)
        except AcquisitionError:
            return None
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        try:
            digest, size = _sha256_file(target)
        except OSError:
            return None
        if digest != artifact.sha256 or size != artifact.size_bytes:
            return None
    return manifest


def recovery_metadata(staging: Path) -> dict[str, RecoveryArtifactV1]:
    manifest = load_valid_manifest(staging)
    if manifest is None:
        return {}
    return {artifact.relative_path: artifact for artifact in manifest.artifacts}
