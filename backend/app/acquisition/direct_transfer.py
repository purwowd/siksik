from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import time
import uuid
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.agent_client import (
    CrawlCleanupReceiptV1,
    CrawlManifestDescriptorV1,
    CrawlTransferV1,
    InventoryRecordV1,
)
from app.acquisition.contracts import AcquisitionContext, AcquisitionResult, ProviderKind
from app.acquisition.errors import ErrorCategory, acquisition_error
from app.acquisition.social_ocr import (
    SocialSnapshotEnrichment,
    build_social_snapshot_enrichments,
    enrichment_row,
)
from app.core.branding import is_crawl_record_mime, session_id_field
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import SessionStatus
from app.selection.contracts import SelectionRunV1

logger = logging.getLogger("siksik.acquisition.direct_transfer")
SAFE_MIME = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_RECORD_MIME = "application/vnd.siksik.crawl-record+json"
CANONICAL_RECORD_SUFFIX = ".siksik-record.json"
CANONICAL_RECORD_SUFFIXES = (".siksik-record.json", ".satria-record.json", ".siksik-record.json")
REMOTE_TRANSFER_ROOT = PurePosixPath(
    "/sdcard/Android/data/com.siksik.agent/files/siksik_transfer"
)


class StrictTransferModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class ManifestArtifactV1(StrictTransferModel):
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    record_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    source_kind: Literal[
        "media_image",
        "media_video",
        "media_audio",
        "document",
        "sms",
        "contact",
        "visible_ui",
        "notification",
    ]
    role: Literal["canonical_record", "source_binary", "screenshot"]
    attachment_id: str | None = Field(
        pattern=r"^[A-Za-z0-9._:-]{1,128}$",
    )
    relative_path: str = Field(min_length=1, max_length=1024)
    mime_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=1, le=4_294_967_296)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CrawlManifestV1(StrictTransferModel):
    schema_version: Literal[1]
    bundle_format: Literal["direct_manifest_files_v1"]
    stage_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    siksik_session_id: str = session_id_field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    crawl_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    selection_revision: int = Field(ge=1)
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0, le=10_000)
    artifact_count: int = Field(ge=0, le=30_000)
    total_bytes: int = Field(ge=0, le=17_179_869_184)
    created_at_epoch_ms: int = Field(ge=1)
    artifacts: list[ManifestArtifactV1] = Field(max_length=30_000)


class TransferAgentClient(Protocol):
    async def start_transfer(self, *args, **kwargs): ...
    async def transfer_status(self, *args, **kwargs): ...
    async def transfer_manifest(self, *args, **kwargs): ...
    async def cleanup_transfer(self, *args, **kwargs): ...


class DirectCrawlTransferService:
    def __init__(self, adb: AsyncAdbTransport | None = None) -> None:
        self._adb = adb or AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=settings.adb_command_timeout_s,
        )

    async def ingest(
        self,
        context: AcquisitionContext,
        client: TransferAgentClient,
        selection: SelectionRunV1,
    ) -> AcquisitionResult:
        started = time.perf_counter()
        fingerprint = selection.selection_fingerprint
        if selection.state != "confirmed" or fingerprint is None:
            raise acquisition_error(
                ErrorCategory.CONFLICT,
                "Selection belum siap untuk transfer.",
            )
        stage_digest = hashlib.sha256(
            f"{context.session_id}\x1f{selection.crawl_id}\x1f{fingerprint}".encode()
        ).hexdigest()
        stage_id = f"stage_{stage_digest[:40]}"
        transfer_key = f"transfer_{stage_digest[:48]}"
        cleanup_key = f"cleanup_{stage_digest[:48]}"
        existing = await self._committed_transfer(
            context.session_id,
            selection.crawl_id,
            fingerprint,
        )
        target = settings.staging_dir / context.session_id
        if existing is not None and target.is_dir():
            return AcquisitionResult(
                staging=target,
                item_count=int(existing["artifact_count"]),
                duration_ms=(time.perf_counter() - started) * 1000,
                method="android_agent_direct_manifest_resumed",
                provider=ProviderKind.ANDROID_AGENT,
            )

        transfer = (
            await client.start_transfer(
                context.session_id,
                selection.crawl_id,
                stage_id=stage_id,
                selection_revision=selection.revision,
                selection_fingerprint=fingerprint,
                idempotency_key=transfer_key,
                request_id=context.request_id,
            )
        ).body
        transfer = await self._wait_until_ready(
            context,
            client,
            transfer,
            selection,
            stage_id,
        )
        descriptor = (
            await client.transfer_manifest(
                context.session_id,
                selection.crawl_id,
                stage_id,
                request_id=context.request_id,
            )
        ).body
        self._validate_descriptor(descriptor, selection, stage_id)
        await self._adb.select_device(context.device_id)
        control_root = settings.staging_dir / f".{context.session_id}.android-transfer"
        await asyncio.to_thread(self._reset_control_root, control_root)
        download_root = control_root / "downloads"
        data_root = control_root / "data"
        download_root.mkdir(parents=True, exist_ok=True)
        data_root.mkdir(parents=True, exist_ok=True)

        manifest_remote = self._validated_remote_path(
            descriptor.manifest_relative_path,
            descriptor.stage_id,
            selection.siksik_session_id,
        )
        stage_relative = PurePosixPath(*manifest_remote.parts[:2]).as_posix()
        bulk_download = download_root / "stage"
        await self._adb.pull_staged_directory(
            context.device_id,
            remote_root=REMOTE_TRANSFER_ROOT,
            relative_path=stage_relative,
            destination_root=control_root,
            destination=bulk_download,
            timeout=self._bulk_pull_timeout(transfer.total_bytes),
        )
        local_stage = self._resolve_downloaded_stage_root(
            bulk_download,
            descriptor.manifest_relative_path,
            descriptor.stage_id,
            selection.siksik_session_id,
        )
        manifest_download = self._local_stage_file(
            local_stage,
            descriptor.manifest_relative_path,
            descriptor.stage_id,
            selection.siksik_session_id,
        )
        await asyncio.to_thread(
            self._verify_file,
            manifest_download,
            descriptor.manifest_size_bytes,
            descriptor.manifest_sha256,
        )
        manifest = await asyncio.to_thread(self._read_manifest, manifest_download)
        self._validate_manifest(manifest, descriptor, selection, transfer)
        await context.on_progress(
            SessionStatus.ACQUIRING,
            51.0,
            "Manifest transfer Android terverifikasi",
            crawl_id=selection.crawl_id,
            crawl_state="transferring",
            transfer_state="pulling",
            transfer_records=manifest.record_count,
            transfer_artifacts=manifest.artifact_count,
        )

        downloaded: dict[str, Path] = {}
        progress_interval = max(50, (manifest.artifact_count + 99) // 100)
        for index, artifact in enumerate(manifest.artifacts, start=1):
            destination = self._local_stage_file(
                local_stage,
                artifact.relative_path,
                manifest.stage_id,
                manifest.siksik_session_id,
            )
            await asyncio.to_thread(
                self._verify_file,
                destination,
                artifact.size_bytes,
                artifact.sha256,
            )
            downloaded[artifact.artifact_id] = destination
            if index % progress_interval == 0 or index == manifest.artifact_count:
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    min(58.0, 51.0 + (index / max(manifest.artifact_count, 1)) * 7.0),
                    "Menarik artifact Android terpilih",
                    crawl_id=selection.crawl_id,
                    crawl_state="transferring",
                    transfer_state="pulling",
                    transfer_completed=index,
                    transfer_artifacts=manifest.artifact_count,
                )

        local_paths = await asyncio.to_thread(
            self._materialize,
            data_root,
            manifest.artifacts,
            downloaded,
        )
        canonical_records = await asyncio.to_thread(
            self._validate_canonical_records,
            manifest,
            local_paths,
        )
        social_enrichments = await asyncio.to_thread(
            build_social_snapshot_enrichments,
            session_id=context.session_id,
            crawl_id=selection.crawl_id,
            records=canonical_records,
            artifacts=manifest.artifacts,
            local_paths=local_paths,
        )
        await context.on_progress(
            SessionStatus.ACQUIRING,
            59.0,
            "OCR snapshot sosial diproses di backend",
            crawl_id=selection.crawl_id,
            crawl_state="transferring",
            transfer_state="host_social_ocr",
            social_ocr_records=len(social_enrichments),
        )
        materialized_relative = {
            artifact_id: path.relative_to(data_root)
            for artifact_id, path in local_paths.items()
        }
        indexed_files = await db.fetchall(
            "SELECT id, meta_json FROM files WHERE session_id = ?",
            (context.session_id,),
        )
        if any(not self._is_live_selection_file(row["meta_json"]) for row in indexed_files):
            raise acquisition_error(
                ErrorCategory.CONFLICT,
                "Staging sesi sudah memasuki tahap indexing.",
            )
        await asyncio.to_thread(self._commit_directory, data_root, target)
        local_paths = {
            artifact_id: target.resolve() / relative
            for artifact_id, relative in materialized_relative.items()
        }
        receipt_id = f"transfer_{uuid.uuid4()}"
        await self._commit_database(
            context.session_id,
            selection,
            manifest,
            descriptor,
            canonical_records,
            local_paths,
            social_enrichments,
            receipt_id,
        )
        cleanup: CrawlCleanupReceiptV1 | None = None
        try:
            cleanup = (
                await client.cleanup_transfer(
                    context.session_id,
                    selection.crawl_id,
                    stage_id,
                    idempotency_key=cleanup_key,
                    request_id=context.request_id,
                )
            ).body
            if cleanup.stage_id != stage_id or cleanup.crawl_id != selection.crawl_id:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Receipt cleanup Android tidak konsisten.",
                )
            await db.execute(
                "UPDATE crawl_transfers SET cleanup_receipt_id = ?, updated_at = ? "
                "WHERE stage_id = ?",
                (cleanup.receipt_id, utcnow(), stage_id),
            )
        except Exception as exc:
            logger.warning(
                "android_transfer_cleanup_deferred",
                extra={
                    "request_id": context.request_id,
                    "session_id": context.session_id,
                    "crawl_id": selection.crawl_id,
                    "error_category": type(exc).__name__,
                },
            )
        await asyncio.to_thread(shutil.rmtree, control_root, True)
        await context.on_progress(
            SessionStatus.ACQUIRING,
            60.0,
            "Transfer Android terverifikasi selesai",
            crawl_id=selection.crawl_id,
            crawl_state="transfer_committed",
            transfer_state="committed",
            transfer_receipt_id=receipt_id,
            transfer_cleanup_state="completed" if cleanup is not None else "deferred",
            transfer_records=manifest.record_count,
            transfer_artifacts=manifest.artifact_count,
        )
        return AcquisitionResult(
            staging=target,
            item_count=manifest.artifact_count,
            duration_ms=(time.perf_counter() - started) * 1000,
            method="android_agent_direct_manifest",
            provider=ProviderKind.ANDROID_AGENT,
        )

    async def _wait_until_ready(
        self,
        context: AcquisitionContext,
        client: TransferAgentClient,
        transfer: CrawlTransferV1,
        selection: SelectionRunV1,
        stage_id: str,
    ) -> CrawlTransferV1:
        deadline = time.monotonic() + TRANSFER_PREPARATION_TIMEOUT_SECONDS
        while transfer.state in {"queued", "copying", "finalizing"}:
            self._validate_transfer(transfer, selection, stage_id)
            if time.monotonic() >= deadline:
                raise acquisition_error(
                    ErrorCategory.ADB_TIMEOUT,
                    "Staging transfer Android melewati batas waktu.",
                    retryable=True,
                )
            await context.on_progress(
                SessionStatus.ACQUIRING,
                min(
                    50.0,
                    49.0
                    + transfer.completed_records / max(transfer.total_records, 1),
                ),
                "Menyiapkan hand-off langsung Android",
                crawl_id=transfer.crawl_id,
                crawl_state="staging",
                transfer_state=transfer.state,
                transfer_completed=transfer.completed_records,
                transfer_records=transfer.total_records,
            )
            await asyncio.sleep(TRANSFER_POLL_SECONDS)
            transfer = (
                await client.transfer_status(
                    context.session_id,
                    transfer.crawl_id,
                    transfer.stage_id,
                    request_id=context.request_id,
                )
            ).body
        self._validate_transfer(transfer, selection, stage_id)
        if transfer.state == "cancelled":
            raise asyncio.CancelledError
        if transfer.state != "completed":
            detail = transfer.error_category
            message = (
                f"Staging transfer Android gagal ({detail})."
                if detail
                else "Staging transfer Android gagal."
            )
            raise acquisition_error(
                ErrorCategory.AGENT_UNAVAILABLE,
                message,
                retryable=True,
            )
        if transfer.completed_records != transfer.total_records:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Jumlah record staging Android tidak konsisten.",
            )
        return transfer

    @staticmethod
    def _validate_transfer(
        transfer: CrawlTransferV1,
        selection: SelectionRunV1,
        stage_id: str,
    ) -> None:
        if (
            transfer.stage_id != stage_id
            or transfer.crawl_id != selection.crawl_id
            or transfer.selection_revision != selection.revision
            or transfer.selection_fingerprint != selection.selection_fingerprint
            or transfer.total_records != selection.totals.selected
            or transfer.completed_records > transfer.total_records
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Status transfer Android tidak konsisten.",
            )

    @staticmethod
    def _validate_descriptor(
        descriptor: CrawlManifestDescriptorV1,
        selection: SelectionRunV1,
        stage_id: str,
    ) -> None:
        if (
            descriptor.stage_id != stage_id
            or descriptor.crawl_id != selection.crawl_id
            or descriptor.selection_revision != selection.revision
            or descriptor.selection_fingerprint != selection.selection_fingerprint
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Descriptor manifest Android tidak konsisten.",
            )
        DirectCrawlTransferService._validated_remote_path(
            descriptor.manifest_relative_path,
            descriptor.stage_id,
            selection.siksik_session_id,
        )

    @staticmethod
    def _read_manifest(path: Path) -> CrawlManifestV1:
        try:
            return CrawlManifestV1.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Manifest transfer Android tidak valid.",
            ) from exc

    @staticmethod
    def _validate_manifest(
        manifest: CrawlManifestV1,
        descriptor: CrawlManifestDescriptorV1,
        selection: SelectionRunV1,
        transfer: CrawlTransferV1,
    ) -> None:
        if (
            manifest.stage_id != descriptor.stage_id
            or manifest.crawl_id != selection.crawl_id
            or manifest.siksik_session_id != selection.siksik_session_id
            or manifest.selection_revision != selection.revision
            or manifest.selection_fingerprint != selection.selection_fingerprint
            or manifest.policy_fingerprint != selection.policy_fingerprint
            or manifest.record_count != transfer.total_records
            or manifest.artifact_count != transfer.artifact_count
            or manifest.total_bytes != transfer.total_bytes
            or manifest.artifact_count != len(manifest.artifacts)
            or manifest.total_bytes != sum(item.size_bytes for item in manifest.artifacts)
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Isi manifest Android tidak konsisten.",
            )
        artifact_ids = {item.artifact_id for item in manifest.artifacts}
        paths = {item.relative_path for item in manifest.artifacts}
        canonical = [item for item in manifest.artifacts if item.role == "canonical_record"]
        record_ids = {item.record_id for item in canonical}
        canonical_by_record = {item.record_id: item for item in canonical}
        if (
            len(artifact_ids) != len(manifest.artifacts)
            or len(paths) != len(manifest.artifacts)
            or len(record_ids) != manifest.record_count
            or len(canonical) != manifest.record_count
            or any(item.record_id not in record_ids for item in manifest.artifacts)
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Relasi artifact manifest Android tidak valid.",
            )
        for item in manifest.artifacts:
            DirectCrawlTransferService._validated_remote_path(
                item.relative_path,
                manifest.stage_id,
                manifest.siksik_session_id,
            )
            if not SAFE_MIME.fullmatch(item.mime_type):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "MIME artifact Android tidak valid.",
                )
            if item.source_kind != canonical_by_record[item.record_id].source_kind:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Source artifact Android tidak konsisten.",
                )
            if item.role == "canonical_record" and (
                not is_crawl_record_mime(item.mime_type)
                or item.attachment_id is not None
                or not item.relative_path.endswith(CANONICAL_RECORD_SUFFIXES)
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "MIME record canonical Android tidak valid.",
                )
            if item.role == "screenshot" and (
                item.source_kind != "visible_ui"
                or item.mime_type != "image/png"
                or item.attachment_id is None
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Screenshot Android tidak terikat visible UI.",
                )
            if item.role == "source_binary" and (
                item.source_kind not in BINARY_SOURCE_KINDS
                or item.attachment_id is not None
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Binary Android tidak terikat source record.",
                )

    @staticmethod
    def _validated_remote_path(
        relative_path: str,
        stage_id: str,
        session_id: str,
    ) -> PurePosixPath:
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\x00" in relative_path
            or path.is_absolute()
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
            or len(path.parts) < 3
            or path.parts[0] != session_id
            or path.parts[1] != stage_id
            or path.suffix.casefold() == ".zip"
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Path artifact manifest Android tidak valid.",
            )
        return path

    @staticmethod
    def _resolve_downloaded_stage_root(
        bulk_download: Path,
        manifest_relative_path: str,
        stage_id: str,
        session_id: str,
    ) -> Path:
        remote = DirectCrawlTransferService._validated_remote_path(
            manifest_relative_path,
            stage_id,
            session_id,
        )
        suffix = Path(*remote.parts[2:])
        candidates = (
            bulk_download,
            bulk_download / stage_id,
            bulk_download / session_id / stage_id,
        )
        roots: list[Path] = []
        for candidate in candidates:
            root = candidate.resolve()
            manifest_path = root / suffix
            manifest = manifest_path.resolve()
            if (
                manifest_path.is_file()
                and not manifest_path.is_symlink()
                and manifest.is_relative_to(root)
                and root not in roots
            ):
                roots.append(root)
        if len(roots) != 1:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Layout staging Android hasil ADB tidak valid.",
            )
        return roots[0]

    @staticmethod
    def _local_stage_file(
        stage_root: Path,
        relative_path: str,
        stage_id: str,
        session_id: str,
    ) -> Path:
        remote = DirectCrawlTransferService._validated_remote_path(
            relative_path,
            stage_id,
            session_id,
        )
        root = stage_root.resolve()
        target_path = root / Path(*remote.parts[2:])
        target = target_path.resolve()
        if (
            not target.is_relative_to(root)
            or not target_path.is_file()
            or target_path.is_symlink()
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Artifact Android tidak ditemukan di staging terverifikasi.",
            )
        return target

    @staticmethod
    def _bulk_pull_timeout(total_bytes: int) -> float:
        estimated = (
            TRANSFER_PULL_OVERHEAD_SECONDS
            + total_bytes / TRANSFER_MIN_BYTES_PER_SECOND
        )
        return min(
            TRANSFER_MAX_PULL_TIMEOUT_SECONDS,
            max(float(settings.adb_pull_timeout_s), estimated),
        )

    @staticmethod
    def _verify_file(path: Path, size_bytes: int, sha256: str) -> None:
        if not path.is_file() or path.stat().st_size != size_bytes or not SHA256.fullmatch(sha256):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Ukuran artifact Android tidak sesuai manifest.",
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            path.unlink(missing_ok=True)
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Hash artifact Android tidak sesuai manifest.",
            )

    @staticmethod
    def _materialize(
        data_root: Path,
        artifacts: Sequence[ManifestArtifactV1],
        downloaded: dict[str, Path],
    ) -> dict[str, Path]:
        output: dict[str, Path] = {}
        for item in artifacts:
            source = downloaded.get(item.artifact_id)
            if source is None or not source.is_file():
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Artifact Android belum lengkap.",
                )
            directory = data_root / item.source_kind
            directory.mkdir(parents=True, exist_ok=True)
            if item.role == "canonical_record":
                filename = f"{item.record_id}{CANONICAL_RECORD_SUFFIX}"
            elif item.role == "screenshot":
                filename = f"{item.record_id}__{item.artifact_id}.png"
            else:
                remote = PurePosixPath(item.relative_path)
                suffix = remote.suffix if SAFE_SUFFIX.fullmatch(remote.suffix) else ""
                if not suffix:
                    suffix = mimetypes.guess_extension(item.mime_type) or ".bin"
                if not SAFE_SUFFIX.fullmatch(suffix):
                    suffix = ".bin"
                # A binary-bearing record is contractually limited to one
                # source_binary artifact. Keep its host path stable per logical
                # record; artifact UUIDs are transfer internals and previously
                # leaked as duplicate-looking gallery paths.
                filename = f"{item.record_id}{suffix.lower()}"
            target = (directory / filename).resolve()
            if not target.is_relative_to(data_root.resolve()):
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Tujuan artifact Android tidak valid.",
                )
            os.replace(source, target)
            output[item.artifact_id] = target
        return output

    @staticmethod
    def _validate_canonical_records(
        manifest: CrawlManifestV1,
        local_paths: dict[str, Path],
    ) -> dict[str, tuple[InventoryRecordV1, str]]:
        output: dict[str, tuple[InventoryRecordV1, str]] = {}
        artifacts_by_record: dict[str, list[ManifestArtifactV1]] = defaultdict(list)
        for artifact in manifest.artifacts:
            artifacts_by_record[artifact.record_id].append(artifact)
            if artifact.role != "canonical_record":
                continue
            path = local_paths[artifact.artifact_id]
            try:
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                record = InventoryRecordV1.model_validate(payload)
            except (OSError, UnicodeError, ValueError) as exc:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Record canonical Android tidak valid.",
                ) from exc
            if (
                record.record_id != artifact.record_id
                or record.crawl_id != manifest.crawl_id
                or record.siksik_session_id != manifest.siksik_session_id
                or record.source_kind != artifact.source_kind
                or record.selection is None
                or record.selection.selected is not True
                or record.selection.revision != manifest.selection_revision
                or record.selection.selection_fingerprint != manifest.selection_fingerprint
                or record.selection.policy_fingerprint != manifest.policy_fingerprint
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Binding record canonical Android tidak valid.",
                )
            output[record.record_id] = (record, raw)
        if len(output) != manifest.record_count:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Jumlah record canonical Android tidak sesuai.",
            )
        for record_id, (record, _) in output.items():
            related = artifacts_by_record[record_id]
            binaries = [item for item in related if item.role == "source_binary"]
            screenshots = [item for item in related if item.role == "screenshot"]
            if record.source_kind == "visible_ui":
                screenshot_ids = {item.attachment_id for item in screenshots}
                if binaries or not screenshot_ids.issubset(set(record.attachment_ids)):
                    raise acquisition_error(
                        ErrorCategory.AGENT_INVALID_RESPONSE,
                        "Attachment visible UI Android tidak lengkap.",
                    )
            elif record.source_kind in BINARY_SOURCE_KINDS:
                if len(binaries) > 1 or screenshots:
                    raise acquisition_error(
                        ErrorCategory.AGENT_INVALID_RESPONSE,
                        "Binary source Android tidak lengkap.",
                    )
            elif binaries or screenshots:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Record terstruktur Android memiliki artifact asing.",
                )
        return output

    @staticmethod
    def _commit_directory(data_root: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        os.replace(data_root, target)

    @staticmethod
    def _reset_control_root(control_root: Path) -> None:
        if control_root.exists():
            shutil.rmtree(control_root)
        control_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _is_live_selection_file(raw_metadata: str | None) -> bool:
        try:
            value = json.loads(raw_metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        return value.get("live_selection") is True

    async def _commit_database(
        self,
        session_id: str,
        selection: SelectionRunV1,
        manifest: CrawlManifestV1,
        descriptor: CrawlManifestDescriptorV1,
        records: dict[str, tuple[InventoryRecordV1, str]],
        local_paths: dict[str, Path],
        social_enrichments: Sequence[SocialSnapshotEnrichment],
        receipt_id: str,
    ) -> None:
        now = utcnow()
        staging = (settings.staging_dir / session_id).resolve()
        canonical_by_record = {
            artifact.record_id: artifact
            for artifact in manifest.artifacts
            if artifact.role == "canonical_record"
        }
        record_rows: list[tuple[object, ...]] = []
        for record_id, (record, raw) in records.items():
            canonical_artifact = canonical_by_record[record_id]
            canonical_path = str(
                local_paths[canonical_artifact.artifact_id].relative_to(staging)
            )
            social_scope = (
                record.metadata.social_scope
                if record.source_kind == "visible_ui"
                else None
            )
            record_rows.append(
                (
                    record.record_id,
                    manifest.crawl_id,
                    session_id,
                    record.source_kind,
                    record.source_app,
                    social_scope,
                    record.normalized_text,
                    record.content_sha256,
                    manifest.selection_revision,
                    manifest.selection_fingerprint,
                    raw,
                    canonical_path,
                    now,
                )
            )
        artifact_rows = [
            (
                artifact.artifact_id,
                manifest.crawl_id,
                session_id,
                artifact.record_id,
                artifact.source_kind,
                artifact.role,
                artifact.mime_type,
                str(local_paths[artifact.artifact_id].relative_to(staging)),
                artifact.size_bytes,
                artifact.sha256,
                now,
            )
            for artifact in manifest.artifacts
        ]
        social_enrichment_rows = [
            enrichment_row(session_id, manifest.crawl_id, value, now)
            for value in social_enrichments
        ]
        async with db.transaction(immediate=True) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO crawl_transfers (
                    stage_id, crawl_id, session_id, state, selection_revision,
                    selection_fingerprint, manifest_sha256, record_count,
                    artifact_count, total_bytes, receipt_id, cleanup_receipt_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'committed', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    manifest.stage_id,
                    manifest.crawl_id,
                    session_id,
                    manifest.selection_revision,
                    manifest.selection_fingerprint,
                    descriptor.manifest_sha256,
                    manifest.record_count,
                    manifest.artifact_count,
                    manifest.total_bytes,
                    receipt_id,
                    now,
                    now,
                ),
            )
            if record_rows:
                await conn.executemany(
                    """
                    INSERT OR REPLACE INTO crawl_records (
                        record_id, crawl_id, session_id, source_kind, source_app,
                        social_scope, normalized_text, content_sha256,
                        selection_revision, selection_fingerprint, canonical_json,
                        canonical_path, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record_rows,
                )
            if artifact_rows:
                await conn.executemany(
                    """
                    INSERT OR REPLACE INTO crawl_artifacts (
                        artifact_id, crawl_id, session_id, record_id, source_kind,
                        role, mime_type, relative_path, size_bytes, sha256,
                        verified, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    artifact_rows,
                )
            if social_enrichment_rows:
                await conn.executemany(
                    """
                    INSERT OR REPLACE INTO social_snapshot_enrichments (
                        crawl_id, record_id, session_id, source_app, social_scope,
                        artifact_ids_json, debug_paths_json, ocr_text, ocr_backend,
                        ocr_confidence, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    social_enrichment_rows,
                )
            await conn.execute(
                "INSERT INTO crawl_events (crawl_id, session_id, event_type, details_json, created_at) "
                "VALUES (?, ?, 'transfer_committed', ?, ?)",
                (
                    manifest.crawl_id,
                    session_id,
                    json.dumps(
                        {
                            "stage_id": manifest.stage_id,
                            "record_count": manifest.record_count,
                            "artifact_count": manifest.artifact_count,
                            "social_ocr_records": len(social_enrichment_rows),
                            "receipt_id": receipt_id,
                        },
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )

    @staticmethod
    async def _committed_transfer(
        session_id: str,
        crawl_id: str,
        fingerprint: str,
    ):
        return await db.fetchone(
            "SELECT artifact_count FROM crawl_transfers "
            "WHERE session_id = ? AND crawl_id = ? AND selection_fingerprint = ? "
            "AND state = 'committed'",
            (session_id, crawl_id, fingerprint),
        )


TRANSFER_POLL_SECONDS = 0.25
TRANSFER_PREPARATION_TIMEOUT_SECONDS = 15 * 60
TRANSFER_PULL_OVERHEAD_SECONDS = 120.0
TRANSFER_MIN_BYTES_PER_SECOND = 2 * 1024 * 1024
TRANSFER_MAX_PULL_TIMEOUT_SECONDS = 4 * 60 * 60.0
BINARY_SOURCE_KINDS = frozenset(
    {"media_image", "media_video", "media_audio", "document"}
)
direct_crawl_transfer = DirectCrawlTransferService()
