from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.acquisition.android_recovery.contracts import (
    MediaStoreRow,
    RecoveryManifestV1,
    RecoveryRunResult,
)
from app.acquisition.android_recovery.gateway import (
    DiscoveryResult,
    RecoveryAdbGateway,
    TransferResult,
)
from app.acquisition.android_recovery.parsers import (
    GALLERY_DATA_MAGIC,
    GALLERY_INDEX_MAGIC,
    find_images,
    gallery_cache_records,
    is_control_file,
    is_trash_path,
    parse_gallery_index,
    parse_media_store_rows,
)
from app.acquisition.android_recovery.paths import validate_shared_path
from app.acquisition.android_recovery.service import (
    AndroidRecoveryService,
    cleanup_recovery_staging,
    load_valid_manifest,
)
from app.acquisition.contracts import AcquisitionResult, ProviderKind
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import ProcessResult
from app.core import config
from app.models.schemas import (
    AcquisitionMode,
    DeviceType,
    RecoveryState,
    Scenario,
    SessionProgress,
)
from app.services import acquisition, analysis, nudity
from app.services.reports import report_to_html


def artifact_manifest(
    staging: Path,
    *,
    candidate_id: str = "a" * 32,
    payload: bytes = b"x",
) -> RecoveryManifestV1:
    relative = f"recovered_trash/trash/{candidate_id}.jpg"
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = RecoveryManifestV1.model_validate(
        {
            "schema_version": 1,
            "mode": AcquisitionMode.QUICK,
            "status": "complete",
            "artifacts": [
                {
                    "candidate_id": candidate_id,
                    "relative_path": relative,
                    "source": "filesystem_trash",
                    "classification": "trash_resident",
                    "confidence": "high",
                    "capture_method": "adb_pull",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(payload),
                    "sha256": digest,
                }
            ],
            "stats": {
                "candidates_discovered": 1,
                "payloads_captured": 1,
                "payloads_failed": 0,
                "payloads_skipped": 0,
                "duplicate_payloads": 0,
                "bytes_captured": len(payload),
                "cache_sources_scanned": 0,
                "cache_candidates_recovered": 0,
            },
            "warnings": [],
        }
    )
    control = staging / "_android_recovery"
    control.mkdir(parents=True, exist_ok=True)
    (control / "manifest-v1.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return manifest


def png_bytes(width: int = 2, height: int = 3) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeGateway:
    def __init__(self, payload: bytes = b"bounded-recovery-payload") -> None:
        self.payload = payload
        self.transfer_calls = 0
        self.cache_calls = 0
        self.thumbnail_calls = 0

    async def shared_roots(self, _serial: str):
        return ("/sdcard",)

    async def media_store_rows(self, _serial: str, *, trashed_only: bool, timeout: float):
        assert timeout > 0
        if not trashed_only:
            return [], False, False
        return [
            MediaStoreRow(
                media_id="11",
                path="/sdcard/DCIM/.trashed-1999999999-camera.jpg",
                display_name=".trashed-1999999999-camera.jpg",
                mime_type="image/jpeg",
                size_bytes=len(self.payload),
                expires_epoch_s=1_999_999_999,
                is_trashed=True,
            )
        ], False, False

    async def is_directory(self, _serial: str, path: str, _roots):
        return path == "/sdcard/Android"

    async def discover_trash(self, _serial: str, roots, *, timeout: float):
        assert roots in (["/sdcard/Android"], ["/sdcard"])
        assert timeout > 0
        return DiscoveryResult(
            (
                "/sdcard/Android/data/vendor/.Trash/second.png",
                "/sdcard/Android/data/vendor/.Trash/trash_bin.db",
            ),
            False,
            False,
        )

    async def stat_size(self, _serial: str, path: str, _roots):
        return len(self.payload) if not path.endswith("trash_bin.db") else 100

    async def transfer(
        self,
        _serial: str,
        *,
        remote_path: str | None,
        content_uri: str | None,
        roots,
        destination: Path,
        max_bytes: int,
        timeout: float,
    ):
        assert remote_path or content_uri
        assert roots == ("/sdcard",)
        assert max_bytes >= len(self.payload)
        assert timeout > 0
        self.transfer_calls += 1
        captured = self.payload + str(self.transfer_calls).encode("ascii")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(captured)
        return TransferResult(True, "adb_pull", len(captured))

    async def discover_cache_indexes(self, _serial: str, _roots, *, timeout: float):
        self.cache_calls += 1
        return DiscoveryResult((), False, False)

    async def discover_thumbnails(self, _serial: str, _roots, *, timeout: float):
        self.thumbnail_calls += 1
        return (), (), False

    async def discover_disk_cache_jpegs(self, _serial: str, _roots, *, timeout: float):
        return DiscoveryResult((), False, False)

    async def file_sha256(self, _serial: str, _path: str, _roots):
        return None


@pytest.mark.unit
def test_mediastore_parser_and_trash_path_policy():
    raw = (
        "Row: 0 _id=12, _data=/storage/emulated/0/DCIM/.trashed-2000000000-a.jpg, "
        "_display_name=.trashed-2000000000-a.jpg, date_expires=2000000000, "
        "is_trashed=1, mime_type=image/jpeg, _size=123\n"
    )
    rows = parse_media_store_rows(raw)

    assert len(rows) == 1
    assert rows[0].path == "/sdcard/DCIM/.trashed-2000000000-a.jpg"
    assert rows[0].size_bytes == 123
    assert rows[0].is_trashed is True
    assert is_trash_path(rows[0].path)
    assert is_trash_path("/sdcard/Android/data/vendor/.Trash/photo.png")
    assert is_trash_path(
        "/sdcard/MIUI/Gallery/cloud/.trashBin/{-trash-}abc.jpg"
    )
    assert is_control_file("/sdcard/Android/data/vendor/.Trash/trash_bin.db")
    assert not is_trash_path("/sdcard/DCIM/current.jpg")


@pytest.mark.unit
async def test_disk_cache_jpeg_recovers_when_not_a_live_photo(tmp_path: Path):
    preview = png_bytes()

    class DiskCacheGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__(payload=b"trash-bytes")
            self.preview = preview

        async def media_store_rows(self, serial: str, *, trashed_only: bool, timeout: float):
            if not trashed_only:
                return (
                    [
                        MediaStoreRow(
                            media_id="99",
                            path="/sdcard/DCIM/Camera/live.jpg",
                            display_name="live.jpg",
                            mime_type="image/jpeg",
                            size_bytes=12,
                            expires_epoch_s=None,
                            is_trashed=False,
                        )
                    ],
                    False,
                    False,
                )
            return await super().media_store_rows(
                serial, trashed_only=trashed_only, timeout=timeout
            )

        async def discover_disk_cache_jpegs(self, _serial: str, _roots, *, timeout: float):
            return DiscoveryResult(
                (
                    "/sdcard/Android/data/com.miui.gallery/files/gallery_disk_cache/small_size/abc.0",
                ),
                False,
                False,
            )

        async def file_sha256(self, _serial: str, path: str, _roots):
            if path.endswith("live.jpg"):
                return "ab" * 32
            return None

        async def transfer(
            self,
            serial: str,
            *,
            remote_path: str | None,
            content_uri: str | None,
            roots,
            destination: Path,
            max_bytes: int,
            timeout: float,
        ):
            if remote_path and "gallery_disk_cache" in remote_path:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(self.preview)
                self.transfer_calls += 1
                return TransferResult(True, "adb_pull", len(self.preview))
            return await super().transfer(
                serial,
                remote_path=remote_path,
                content_uri=content_uri,
                roots=roots,
                destination=destination,
                max_bytes=max_bytes,
                timeout=timeout,
            )

    staging = tmp_path / "staging"
    staging.mkdir()

    async def on_progress(*_args, **_kwargs):
        return None

    result = await AndroidRecoveryService(DiskCacheGateway()).recover(  # type: ignore[arg-type]
        session_id="session-disk-cache",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )
    assert any(
        item.classification == "orphan_disk_cache" for item in result.manifest.artifacts
    )


@pytest.mark.unit
async def test_invalid_live_mediastore_path_does_not_abort_xiaomi_trash(tmp_path: Path):
    trash_path = "/sdcard/MIUI/Gallery/cloud/.trashBin/{-trash-}abc.jpg"
    payload = b"xiaomi-gallery-trash"

    class XiaomiGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__(payload=payload)

        async def media_store_rows(self, serial: str, *, trashed_only: bool, timeout: float):
            if not trashed_only:
                return (
                    [
                        MediaStoreRow(
                            media_id="0",
                            path="/storage/emulated",
                            display_name="emulated",
                            mime_type="inode/directory",
                            size_bytes=0,
                            expires_epoch_s=None,
                            is_trashed=False,
                        )
                    ],
                    False,
                    False,
                )
            return [], False, False

        async def is_directory(self, _serial: str, path: str, _roots):
            return path.endswith("/MIUI")

        async def discover_trash(self, _serial: str, _roots, *, timeout: float):
            return DiscoveryResult((trash_path,), False, False)

        async def discover_disk_cache_jpegs(self, _serial: str, _roots, *, timeout: float):
            return DiscoveryResult(
                (
                    "/sdcard/Android/data/com.miui.gallery/files/gallery_disk_cache/small_size/a.0",
                ),
                False,
                False,
            )

        async def file_sha256(self, _serial: str, path: str, _roots):
            if path == "/storage/emulated":
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Path recovery berada di luar shared storage.",
                )
            return None

        async def transfer(
            self,
            serial: str,
            *,
            remote_path: str | None,
            content_uri: str | None,
            roots,
            destination: Path,
            max_bytes: int,
            timeout: float,
        ):
            if remote_path == trash_path:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(self.payload)
                self.transfer_calls += 1
                return TransferResult(True, "adb_pull", len(self.payload))
            return TransferResult(False, "adb_pull", 0, "adb_pull_failed")

    staging = tmp_path / "staging"
    staging.mkdir()

    async def on_progress(*_args, **_kwargs):
        return None

    result = await AndroidRecoveryService(XiaomiGateway()).recover(  # type: ignore[arg-type]
        session_id="session-xiaomi-trash",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )
    assert result.item_count >= 1
    assert result.manifest.stats.candidates_discovered >= 1
    assert any(
        item.classification == "trash_resident" for item in result.manifest.artifacts
    )


@pytest.mark.unit
def test_shared_path_validation_rejects_escape():
    assert validate_shared_path("/storage/emulated/0/DCIM/a.jpg", ["/sdcard"]) == "/sdcard/DCIM/a.jpg"
    with pytest.raises(AcquisitionError):
        validate_shared_path("/data/user/0/private.db", ["/sdcard"])


@pytest.mark.unit
def test_validated_image_carving_and_gallery_cache_parser():
    image = png_bytes()
    spans = find_images(b"prefix" + image + b"suffix")
    assert len(spans) == 1
    assert (spans[0].width, spans[0].height, spans[0].validation) == (2, 3, "png_chunks_crc")

    reference = "model/image/item/99+/sdcard/DCIM/deleted.png+123+1".encode("utf-16le")
    payload = reference + image
    blob = struct.pack(
        "<QIII",
        7,
        zlib.adler32(payload) & 0xFFFFFFFF,
        4,
        len(payload),
    ) + payload
    data = struct.pack("<I", GALLERY_DATA_MAGIC) + blob
    header_values = [GALLERY_INDEX_MAGIC, 1, len(data), 0, 1, len(data), 1, 0]
    header_values[7] = zlib.adler32(struct.pack("<7I", *header_values[:7])) & 0xFFFFFFFF
    index = struct.pack("<8I", *header_values) + struct.pack("<QI", 7, 4) + struct.pack("<QI", 0, 0)

    parsed_header, offsets = parse_gallery_index(index)
    records = gallery_cache_records(
        data,
        expected_bytes=parsed_header["active_bytes"],
        salvage_offsets=offsets[0],
    )

    assert len(records) == 1
    assert records[0].media_id == "99"
    assert records[0].original_path == "/sdcard/DCIM/deleted.png"
    assert records[0].image.format == "png"


@pytest.mark.unit
async def test_quick_recovery_is_bounded_hashed_and_idempotent(tmp_path: Path):
    gateway = FakeGateway()
    staging = tmp_path / "staging"
    staging.mkdir()
    progress: list[dict] = []

    async def on_progress(_phase, _percent, _message, **fields):
        progress.append(fields)

    service = AndroidRecoveryService(gateway)  # type: ignore[arg-type]
    result = await service.recover(
        session_id="session-1",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id="request-1",
    )

    assert result.item_count == 2
    assert gateway.transfer_calls == 2
    assert gateway.cache_calls == 1
    assert gateway.thumbnail_calls == 1
    assert result.manifest.stats.cache_scan_completed is True
    assert result.manifest.stats.candidates_discovered == 2
    assert result.manifest.stats.payloads_captured == 2
    assert all(item.relative_path.startswith("recovered_trash/trash/") for item in result.manifest.artifacts)
    manifest_text = (staging / "_android_recovery/manifest-v1.json").read_text()
    assert "/sdcard" not in manifest_text
    assert "content://" not in manifest_text
    assert "camera.jpg" not in manifest_text
    assert progress[-1]["recovery_captured"] == 2
    assert progress[-1]["recovery_cache_captured"] == 0

    resumed = await service.recover(
        session_id="session-1",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id="request-2",
    )
    assert resumed.item_count == 2
    assert gateway.transfer_calls == 2
    assert load_valid_manifest(staging) is not None


@pytest.mark.unit
async def test_full_mode_enables_cache_sources(tmp_path: Path):
    gateway = FakeGateway()
    staging = tmp_path / "staging"
    staging.mkdir()

    async def on_progress(*_args, **_kwargs):
        return None

    result = await AndroidRecoveryService(gateway).recover(  # type: ignore[arg-type]
        session_id="session-full",
        serial="device-1",
        mode=AcquisitionMode.FULL,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )

    assert result.manifest.mode == AcquisitionMode.FULL
    assert gateway.cache_calls == 1
    assert gateway.thumbnail_calls == 1


@pytest.mark.unit
async def test_quick_item_budget_truncates_without_extra_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config.settings, "android_recovery_quick_max_items", 1)
    gateway = FakeGateway()
    staging = tmp_path / "staging"
    staging.mkdir()

    async def on_progress(*_args, **_kwargs):
        return None

    result = await AndroidRecoveryService(gateway).recover(  # type: ignore[arg-type]
        session_id="session-budget",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )

    assert result.item_count == 1
    assert gateway.transfer_calls == 1
    assert result.manifest.status == "partial"
    assert result.manifest.stats.cache_scan_completed is True
    assert gateway.cache_calls == 1
    assert "recovery_budget_truncated" in result.manifest.warnings


@pytest.mark.unit
async def test_resume_reapplies_tightened_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    gateway = FakeGateway()
    staging = tmp_path / "staging"
    staging.mkdir()

    async def on_progress(*_args, **_kwargs):
        return None

    service = AndroidRecoveryService(gateway)  # type: ignore[arg-type]
    initial = await service.recover(
        session_id="session-policy",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )
    assert initial.item_count == 2

    monkeypatch.setattr(config.settings, "android_recovery_quick_max_items", 1)
    tightened = await service.recover(
        session_id="session-policy",
        serial="device-1",
        mode=AcquisitionMode.QUICK,
        staging=staging,
        on_progress=on_progress,
        request_id=None,
    )

    assert tightened.item_count == 1
    assert gateway.transfer_calls == 3
    assert tightened.manifest.status == "partial"
    assert "recovery_budget_truncated" in tightened.manifest.warnings


@pytest.mark.unit
def test_cleanup_only_removes_owned_recovery_directories(tmp_path: Path):
    staging = tmp_path / "staging"
    keep = staging / "gallery/keep.jpg"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"keep")
    for path in (
        staging / "_android_recovery/manifest-v1.json",
        staging / "recovered_trash/trash/a.jpg",
        staging / "recovered_trash/previews/b.jpg",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned")

    cleanup_recovery_staging(staging)

    assert keep.read_bytes() == b"keep"
    assert not (staging / "_android_recovery").exists()
    assert not (staging / "recovered_trash/trash").exists()
    assert not (staging / "recovered_trash/previews").exists()


@pytest.mark.unit
async def test_acquire_dispatch_adds_recovery_without_replacing_provider_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = artifact_manifest(staging)

    async def fake_acquire(_self, _context):
        return AcquisitionResult(staging, 3, 100.0, "base_android", ProviderKind.ANDROID_LEGACY)

    async def fake_recover(_self, **_kwargs):
        return RecoveryRunResult(staging, manifest, 25.0)

    monkeypatch.setattr("app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire", fake_acquire)
    monkeypatch.setattr(AndroidRecoveryService, "recover", fake_recover)
    monkeypatch.setattr(config.settings, "android_recovery_enabled", True)
    monkeypatch.setattr(config.settings, "browser_history_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", False)

    async def skip_whatsapp(self, **_kwargs):
        del self
        return None

    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.WhatsAppBackupAcquisitionService.acquire",
        skip_whatsapp,
    )

    async def on_progress(*_args, **_kwargs):
        return None

    result = await acquisition.acquire_dispatch(
        session_id="session-dispatch",
        device_id="device-1",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )

    assert result[1] == 4
    assert result[2] == 125.0
    assert result[3] == "base_android+android_recovery_quick_complete"


@pytest.mark.unit
def test_manifest_rejects_unowned_path_and_inconsistent_stats(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = artifact_manifest(staging)

    unowned = manifest.model_dump(mode="python")
    unowned["artifacts"][0]["relative_path"] = "gallery/" + ("a" * 32) + ".jpg"
    with pytest.raises(ValidationError):
        RecoveryManifestV1.model_validate(unowned)

    inconsistent = manifest.model_dump(mode="python")
    inconsistent["stats"]["bytes_captured"] = 2
    with pytest.raises(ValidationError):
        RecoveryManifestV1.model_validate(inconsistent)


@pytest.mark.unit
def test_manifest_resume_rejects_modified_payload(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = artifact_manifest(staging)
    target = staging / manifest.artifacts[0].relative_path

    assert load_valid_manifest(staging) == manifest
    target.write_bytes(b"tampered")
    assert load_valid_manifest(staging) is None


@pytest.mark.unit
async def test_truncated_discovery_drops_incomplete_path_fragment():
    class TruncatedTransport:
        async def run(self, _serial, args, **_kwargs):
            output = (
                "/storage/emulated/0/DCIM/.Trash/complete.jpg\x00"
                "/storage/emulated/0/DCIM/.Trash/incompl"
            )
            return ProcessResult(tuple(args), 0, output, "", True)

    gateway = RecoveryAdbGateway(  # type: ignore[arg-type]
        TruncatedTransport(),
        output_limit_bytes=1024,
    )
    result = await gateway.discover_trash("device-1", ["/sdcard"], timeout=1.0)

    assert result.paths == ("/sdcard/DCIM/.Trash/complete.jpg",)
    assert result.truncated is True


@pytest.mark.unit
async def test_recovery_failure_keeps_primary_acquisition_and_cleans_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    staging = tmp_path / "staging"
    staging.mkdir()
    keep = staging / "gallery" / "keep.jpg"
    keep.parent.mkdir()
    keep.write_bytes(b"keep")

    async def fake_acquire(_self, _context):
        return AcquisitionResult(staging, 3, 100.0, "base_android", ProviderKind.ANDROID_LEGACY)

    async def fake_recover(_self, **_kwargs):
        partial = staging / "recovered_trash" / "trash" / "partial.jpg"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        raise acquisition_error(
            ErrorCategory.ADB_TIMEOUT,
            "recovery timeout",
            retryable=True,
        )

    monkeypatch.setattr("app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire", fake_acquire)
    monkeypatch.setattr(AndroidRecoveryService, "recover", fake_recover)
    monkeypatch.setattr(config.settings, "android_recovery_enabled", True)
    monkeypatch.setattr(config.settings, "browser_history_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", False)

    async def skip_whatsapp(self, **_kwargs):
        del self
        return None

    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.WhatsAppBackupAcquisitionService.acquire",
        skip_whatsapp,
    )
    events: list[dict] = []

    async def on_progress(*_args, **fields):
        events.append(fields)

    result = await acquisition.acquire_dispatch(
        session_id="session-failure",
        device_id="device-1",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )

    assert result == (staging, 3, 100.0, "base_android")
    assert keep.read_bytes() == b"keep"
    assert not (staging / "recovered_trash" / "trash").exists()
    assert events[-1]["recovery_state"] == "unavailable"
    assert events[-1]["recovery_error_category"] == ErrorCategory.ADB_TIMEOUT.value


@pytest.mark.unit
async def test_recovery_cancellation_cleans_owned_files_and_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_acquire(_self, _context):
        return AcquisitionResult(staging, 1, 1.0, "base_android", ProviderKind.ANDROID_LEGACY)

    async def fake_recover(_self, **_kwargs):
        partial = staging / "_android_recovery" / "tmp" / "partial.bin"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr("app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire", fake_acquire)
    monkeypatch.setattr(AndroidRecoveryService, "recover", fake_recover)
    monkeypatch.setattr(config.settings, "android_recovery_enabled", True)
    monkeypatch.setattr(config.settings, "browser_history_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", False)

    async def skip_whatsapp(self, **_kwargs):
        del self
        return None

    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.WhatsAppBackupAcquisitionService.acquire",
        skip_whatsapp,
    )

    async def on_progress(*_args, **_kwargs):
        return None

    with pytest.raises(asyncio.CancelledError):
        await acquisition.acquire_dispatch(
            session_id="session-cancelled",
            device_id="device-1",
            device_type=DeviceType.ANDROID,
            simulated=False,
            mode=AcquisitionMode.FULL,
            scenario=Scenario.LULUS,
            file_count=0,
            on_progress=on_progress,
        )

    assert not (staging / "_android_recovery").exists()


@pytest.mark.unit
async def test_indexing_accepts_only_verified_recovery_manifest_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = artifact_manifest(staging, payload=b"verified")
    rogue = staging / "recovered_trash" / "previews" / f"{'b' * 32}.jpg"
    rogue.parent.mkdir(parents=True)
    rogue.write_bytes(b"not-in-manifest")

    class FakeDatabase:
        def __init__(self) -> None:
            self.rows: list[tuple] = []

        async def fetchall(self, _query, _params):
            return []

        async def executemany(self, _query, rows):
            self.rows.extend(rows)

    database = FakeDatabase()
    monkeypatch.setattr(acquisition, "db", database)

    async def on_progress(*_args, **_kwargs):
        return None

    indexed, _duration = await acquisition.index_staging(
        "session-index",
        staging,
        on_progress,
    )

    assert indexed == 1
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row[2] == "recovered_trash"
    assert row[3] == manifest.artifacts[0].relative_path
    assert row[4] == "image/jpeg"
    assert row[6] == manifest.artifacts[0].sha256
    metadata = json.loads(row[9])
    assert metadata["acquisition_method"] == "android_recovery_v1"
    assert metadata["recovery_source"] == "filesystem_trash"
    assert metadata["recovery_confidence"] == "high"


@pytest.mark.unit
def test_recovery_progress_uses_public_session_contract():
    progress = SessionProgress.model_validate(
        {
            "recovery_state": "partial",
            "recovery_mode": "full",
            "recovery_candidates": 4,
            "recovery_captured": 2,
            "recovery_bytes": 4096,
            "recovery_warning_count": 1,
            "recovery_duration_ms": 12.5,
        }
    )

    payload = progress.model_dump(mode="json")

    assert progress.recovery_state == RecoveryState.PARTIAL
    assert payload["recovery_state"] == "partial"
    assert payload["recovery_mode"] == "full"
    assert payload["recovery_captured"] == 2


@pytest.mark.unit
def test_recovered_image_uses_existing_finding_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "recovered_trash" / "trash" / f"{'c' * 32}.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"detector-is-injected")
    expected = {
        "category": "ketelanjangan",
        "label": "Ketelanjangan terdeteksi pada gambar: test",
        "confidence": 0.91,
        "layer_origin": "L3",
        "evidence": "detektor lokal",
    }
    monkeypatch.setattr(
        nudity,
        "analyze_image_result",
        lambda _path: nudity.NudityAnalysisResult((expected,), True),
    )
    monkeypatch.setattr("app.services.vision.analyze_image_file", lambda _path, **_kw: [])

    findings = analysis.analyze_content(
        target,
        "image/jpeg",
        "recovered_trash",
        "",
        [],
    )

    assert findings == [expected]


@pytest.mark.unit
def test_recovery_uses_existing_human_readable_report_layout():
    report = {
        "generated_at": "2026-08-14T00:00:00+00:00",
        "session": {
            "id": "session-recovery",
            "label": "Android",
            "device_id": "device",
            "device_type": "android",
            "mode": "full",
            "acquisition_method": (
                "android_agent_inventory_complete+android_agent_direct_manifest"
                "+android_recovery_full_complete"
            ),
            "recommendation": "MENUNGGU REVIEW",
        },
        "metrics": {
            "files": 1,
            "bytes": 1024,
            "findings": 1,
            "timing": {
                "t_acquire_ms": 10,
                "t_analyze_ms": 20,
                "t_total_ms": 30,
            },
            "progress": {
                "recovery_state": "complete",
                "recovery_captured": 1,
                "recovery_bytes": 1024,
                "recovery_warning_count": 0,
            },
        },
        "breakdown": {
            "by_category": {"ketelanjangan": 1},
            "by_layer": {"L3": 1},
            "by_source": {"recovered_trash": 1},
        },
        "findings": [
            {
                "label": "Ketelanjangan terdeteksi pada gambar: test",
                "category": "ketelanjangan",
                "source": "recovered_trash",
                "path": f"recovered_trash/trash/{'d' * 32}.jpg",
                "confidence": 0.91,
                "layer": "L3",
            }
        ],
    }

    html = report_to_html(report)

    assert "Sampah / media terhapus" in html
    assert "Ketelanjangan / konten eksplisit" in html
    assert "Recovery sampah Android (Penuh)" in html
    assert "Recovery sampah Android: 1 item · 1024 bytes · selesai" in html
