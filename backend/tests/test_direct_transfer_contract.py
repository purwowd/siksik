from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest

from app.acquisition.agent_client import InventoryRecordV1
from app.acquisition.direct_transfer import (
    CANONICAL_RECORD_MIME,
    CrawlManifestV1,
    DirectCrawlTransferService,
    ManifestArtifactV1,
)
from app.acquisition.errors import AcquisitionError
from app.services.analysis import analyze_content, read_preview

SESSION_ID = "session-direct-transfer-001"
CRAWL_ID = "crawl-direct-transfer-001"
STAGE_ID = "stage_direct_transfer_001"
TIMESTAMP = "2026-07-17T10:00:00Z"
POLICY_FINGERPRINT = "c" * 64
SELECTION_FINGERPRINT = "b" * 64
SourceKind = Literal[
    "media_image",
    "media_video",
    "media_audio",
    "document",
    "sms",
    "contact",
    "visible_ui",
    "notification",
]
ArtifactRole = Literal["canonical_record", "source_binary", "screenshot"]


def visible_record(normalized_text: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_id": "record-visible-ui-001",
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_kind": "visible_ui",
        "source_app": "com.instagram.android",
        "source_locator": "visible_ui:opaque",
        "observed_at": TIMESTAMP,
        "source_created_at": TIMESTAMP,
        "source_modified_at": None,
        "normalized_text": normalized_text,
        "metadata": {
            "package_name": "com.instagram.android",
            "social_scope": "own_posts",
            "window_id": -1,
            "activity_context": "FixtureActivity",
            "event_type": 2048,
            "screen_sequence": 1,
            "nodes": [],
            "screenshot_ids": [],
            "warning_codes": ["metadata_narkoba_must_not_be_analyzed"],
        },
        "attachment_ids": [],
        "content_sha256": "a" * 64,
        "preprocessing": None,
        "selection": {
            "policy_version": "siksik-selection-v1",
            "policy_fingerprint": POLICY_FINGERPRINT,
            "revision": 1,
            "selection_fingerprint": SELECTION_FINGERPRINT,
            "score": 0.9,
            "threshold": 0.5,
            "auto_selected": True,
            "selected": True,
            "matched_keywords": [],
            "matched_rules": [],
            "model_signals": [],
            "reasons": [],
            "human_override": "none",
            "operator_id": None,
            "decided_at": TIMESTAMP,
        },
        "provenance": {
            "source_adapter": "accessibility_visible_ui",
            "enumeration_method": "android_uiautomator",
            "agent_version": "0.7.0",
            "original_staged": False,
        },
    }


def media_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_id": "record-media-image-001",
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_kind": "media_image",
        "source_app": "whatsapp",
        "source_locator": "media_store_image:opaque",
        "observed_at": TIMESTAMP,
        "source_created_at": TIMESTAMP,
        "source_modified_at": TIMESTAMP,
        "normalized_text": None,
        "metadata": {
            "display_name": "fixture.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 1024,
            "width": 32,
            "height": 24,
            "duration_ms": None,
            "date_taken": TIMESTAMP,
            "date_added": TIMESTAMP,
            "date_modified": TIMESTAMP,
            "capture_time": TIMESTAMP,
            "capture_time_source": "date_taken",
            "directory_hint": "DCIM/Camera",
            "exif": None,
            "warning_codes": [],
            "thumbnail_available": True,
        },
        "attachment_ids": [],
        "content_sha256": None,
        "preprocessing": None,
        "selection": {
            "policy_version": "siksik-selection-v4",
            "policy_fingerprint": POLICY_FINGERPRINT,
            "revision": 1,
            "selection_fingerprint": SELECTION_FINGERPRINT,
            "score": 1.0,
            "threshold": 0.5,
            "auto_selected": True,
            "selected": True,
            "matched_keywords": [],
            "matched_rules": ["in_window_media"],
            "model_signals": [],
            "reasons": [],
            "human_override": "none",
            "operator_id": None,
            "decided_at": TIMESTAMP,
        },
        "provenance": {
            "source_adapter": "media_store_image",
            "enumeration_method": "android_platform_api",
            "agent_version": "0.7.0",
            "original_staged": False,
        },
    }


def _relative(name: str) -> str:
    return f"{SESSION_ID}/{STAGE_ID}/{name}"


def _artifact(
    *,
    artifact_id: str,
    record_id: str,
    source_kind: SourceKind,
    role: ArtifactRole,
    relative_path: str,
    mime_type: str,
    raw: bytes,
    attachment_id: str | None = None,
) -> ManifestArtifactV1:
    return ManifestArtifactV1(
        artifact_id=artifact_id,
        record_id=record_id,
        source_kind=source_kind,
        role=role,
        attachment_id=attachment_id,
        relative_path=relative_path,
        mime_type=mime_type,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _manifest(artifacts: list[ManifestArtifactV1], record_count: int) -> CrawlManifestV1:
    return CrawlManifestV1(
        schema_version=1,
        bundle_format="direct_manifest_files_v1",
        stage_id=STAGE_ID,
        siksik_session_id=SESSION_ID,
        crawl_id=CRAWL_ID,
        selection_revision=1,
        selection_fingerprint=SELECTION_FINGERPRINT,
        policy_fingerprint=POLICY_FINGERPRINT,
        record_count=record_count,
        artifact_count=len(artifacts),
        total_bytes=sum(item.size_bytes for item in artifacts),
        created_at_epoch_ms=1,
        artifacts=artifacts,
    )


def _write_canonical(tmp_path: Path, payload: dict[str, object]) -> tuple[ManifestArtifactV1, dict[str, Path]]:
    raw = json.dumps(payload).encode("utf-8")
    record_id = str(payload["record_id"])
    source_kind = payload["source_kind"]
    assert source_kind in {
        "media_image",
        "media_video",
        "media_audio",
        "document",
        "sms",
        "contact",
        "visible_ui",
        "notification",
    }
    artifact_id = f"artifact_{record_id}"
    relative = _relative(f"records/{record_id}.siksik-record.json")
    path = tmp_path / artifact_id
    path.write_bytes(raw)
    artifact = _artifact(
        artifact_id=artifact_id,
        record_id=record_id,
        source_kind=source_kind,
        role="canonical_record",
        relative_path=relative,
        mime_type=CANONICAL_RECORD_MIME,
        raw=raw,
    )
    return artifact, {artifact_id: path}


@pytest.mark.unit
def test_visible_ui_requires_an_owned_account_scope() -> None:
    payload = visible_record("fixture")
    record = InventoryRecordV1.model_validate(payload)
    assert record.metadata.social_scope == "own_posts"

    payload["metadata"]["social_scope"] = "home_feed"
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)

    payload = visible_record("fixture")
    payload["metadata"]["social_scope"] = "own_tweets"
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)

    payload = visible_record("fixture")
    payload["metadata"]["screenshot_ids"] = ["shot_fixture"]
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)


@pytest.mark.unit
async def test_canonical_analyzer_reads_only_normalized_text(tmp_path: Path) -> None:
    path = tmp_path / "record-visible-ui-001.siksik-record.json"
    path.write_text(json.dumps(visible_record("konten aman")), encoding="utf-8")

    preview = await read_preview(path, CANONICAL_RECORD_MIME)
    findings = analyze_content(
        path,
        CANONICAL_RECORD_MIME,
        "visible_ui",
        preview,
        ["narkoba"],
    )

    assert preview == "konten aman"
    assert findings == []


@pytest.mark.unit
async def test_canonical_social_text_uses_existing_siksik_lexicon(tmp_path: Path) -> None:
    path = tmp_path / "record-visible-ui-001.siksik-record.json"
    path.write_text(json.dumps(visible_record("postingan narkoba")), encoding="utf-8")

    preview = await read_preview(path, CANONICAL_RECORD_MIME)
    findings = analyze_content(
        path,
        CANONICAL_RECORD_MIME,
        "visible_ui",
        preview,
        ["narkoba"],
    )

    assert findings
    assert all(item["evidence"] in preview for item in findings)


@pytest.mark.unit
def test_android_transfer_source_does_not_create_archive_output() -> None:
    roots = [
        Path(__file__).parents[2] / "android-agent" / "app" / "src" / "main",
        Path(__file__).parents[1] / "app" / "acquisition",
    ]
    forbidden = ("ZipOutputStream", "zipfile", "make_archive")
    transfer_sources = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".kt", ".py"}
        and "transfer" in path.name.casefold()
    ]
    assert transfer_sources
    for path in transfer_sources:
        value = path.read_text(encoding="utf-8")
        assert not any(marker in value for marker in forbidden)


@pytest.mark.unit
def test_failed_android_transfer_message_includes_error_category() -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "acquisition"
        / "direct_transfer.py"
    ).read_text(encoding="utf-8")
    assert "Staging transfer Android gagal ({detail})" in source
    assert "transfer.error_category" in source


@pytest.mark.unit
def test_binary_source_allows_canonical_without_binary(tmp_path: Path) -> None:
    artifact, local_paths = _write_canonical(tmp_path, media_record())
    records = DirectCrawlTransferService._validate_canonical_records(
        _manifest([artifact], 1),
        local_paths,
    )
    assert "record-media-image-001" in records


@pytest.mark.unit
def test_binary_source_rejects_two_binaries(tmp_path: Path) -> None:
    canonical, local_paths = _write_canonical(tmp_path, media_record())
    extras: list[ManifestArtifactV1] = []
    for index in (1, 2):
        raw = b"jpeg-fixture" + bytes([index])
        artifact_id = f"artifact_binary_{index}"
        path = tmp_path / artifact_id
        path.write_bytes(raw)
        local_paths[artifact_id] = path
        extras.append(
            _artifact(
                artifact_id=artifact_id,
                record_id="record-media-image-001",
                source_kind="media_image",
                role="source_binary",
                relative_path=_relative(f"artifacts/{artifact_id}.jpg"),
                mime_type="image/jpeg",
                raw=raw,
            )
        )
    with pytest.raises(AcquisitionError, match="Binary source Android tidak lengkap"):
        DirectCrawlTransferService._validate_canonical_records(
            _manifest([canonical, *extras], 1),
            local_paths,
        )


@pytest.mark.unit
def test_visible_ui_allows_missing_screenshot(tmp_path: Path) -> None:
    payload = visible_record("fixture")
    payload["attachment_ids"] = ["shot_fixture"]
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    metadata["screenshot_ids"] = ["shot_fixture"]
    artifact, local_paths = _write_canonical(tmp_path, payload)
    records = DirectCrawlTransferService._validate_canonical_records(
        _manifest([artifact], 1),
        local_paths,
    )
    assert "record-visible-ui-001" in records
