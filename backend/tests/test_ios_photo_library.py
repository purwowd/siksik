from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from app.acquisition import ios_afc
from app.acquisition.process import ProcessResult
from app.core import config
from app.models.schemas import AcquisitionMode
from app.services import acquisition


def _worker_module():
    path = Path(__file__).resolve().parents[2] / "ios-media-puller" / "pull_library_artifacts.py"
    spec = importlib.util.spec_from_file_location("siksik_ios_library_worker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_album_queries_are_time_scoped_and_keep_deleted_separate(tmp_path: Path):
    worker = _worker_module()
    database = tmp_path / "Photos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE ZASSET (
            Z_PK INTEGER PRIMARY KEY,
            ZUUID TEXT,
            ZDIRECTORY TEXT,
            ZFILENAME TEXT,
            ZHIDDEN INTEGER,
            ZTRASHEDSTATE INTEGER,
            ZDATECREATED REAL,
            ZTRASHEDDATE REAL
        )
        """
    )
    cutoff = 1_800_000_000.0
    recent = cutoff - worker.APPLE_EPOCH_OFFSET_S + 100
    old = cutoff - worker.APPLE_EPOCH_OFFSET_S - 100
    connection.executemany(
        "INSERT INTO ZASSET VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "11111111-1111-1111-1111-111111111111", "DCIM/100APPLE", "HIDDEN.HEIC", 1, 0, recent, None),
            (2, "22222222-2222-2222-2222-222222222222", "DCIM/100APPLE", "OLD.JPG", 1, 0, old, None),
            (3, "33333333-3333-3333-3333-333333333333", "DCIM/100APPLE", "DELETED.MOV", 0, 1, old, recent),
            (4, "44444444-4444-4444-4444-444444444444", "DCIM/100APPLE", "BOTH.JPG", 1, 1, recent, recent),
            (5, "55555555-5555-5555-5555-555555555555", "DCIM/100APPLE", "ACTIVE.JPG", 0, 0, recent, None),
        ],
    )
    connection.commit()
    connection.close()

    hidden, hidden_unknown = worker.query_album_assets(
        database,
        album="hidden",
        limit=10,
        not_before_epoch_s=cutoff,
    )
    deleted, deleted_unknown = worker.query_album_assets(
        database,
        album="recently_deleted",
        limit=10,
        not_before_epoch_s=cutoff,
    )

    assert [item.filename for item in hidden] == ["HIDDEN.HEIC"]
    assert {item.filename for item in deleted} == {"DELETED.MOV", "BOTH.JPG"}
    assert hidden_unknown == 0
    assert deleted_unknown == 0


@pytest.mark.unit
def test_purge_evidence_combines_tombstone_and_wal_without_live_assets(tmp_path: Path):
    worker = _worker_module()
    database = tmp_path / "Photos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT)")
    connection.execute(
        "INSERT INTO ZASSET VALUES (1, 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA')"
    )
    connection.execute(
        "CREATE TABLE ACHANGE (ZTOMBSTONE0 TEXT, ZTOMBSTONE2 TEXT, ZTOMBSTONE3 REAL)"
    )
    connection.execute(
        "INSERT INTO ACHANGE VALUES (?, ?, ?)",
        (
            "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
            "expunge",
            800_000_000.0,
        ),
    )
    connection.commit()
    connection.close()
    (tmp_path / "Photos.sqlite-wal").write_bytes(
        b"CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"
        b"DCIM/100APPLEIMG_0001.HEIC"
        b"AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        b"DCIM/100APPLEIMG_0002.JPG"
    )

    evidence = worker.extract_purge_evidence(database)

    assert {item.uuid for item in evidence} == {
        "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
        "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC",
    }
    wal = next(item for item in evidence if item.uuid.startswith("C"))
    assert wal.filename == "IMG_0001.HEIC"
    assert wal.sources == {"photos_sqlite_wal"}


@pytest.mark.unit
def test_cache_preview_requires_complete_jpeg_markers(tmp_path: Path):
    worker = _worker_module()
    valid = tmp_path / "valid.jpg"
    invalid = tmp_path / "invalid.jpg"
    valid.write_bytes(worker.JPEG_SOI + b"bounded-preview" + worker.JPEG_EOI)
    invalid.write_bytes(worker.JPEG_SOI + b"truncated-preview")

    assert worker._is_jpeg(valid) is True
    assert worker._is_jpeg(invalid) is False


@pytest.mark.unit
def test_ios_library_manifest_commit_is_hashed_and_source_preserving(tmp_path: Path):
    work = tmp_path / "work"
    staging = tmp_path / "staging"
    relative = "ios_recently_deleted/0123456789abcdef0123456789abcdef.jpg"
    payload = b"verified-ios-deleted-media"
    target = work / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "not_before_epoch_s": 1_800_000_000.0,
        "artifacts": [
            {
                "relative_path": relative,
                "source": "ios_recently_deleted",
                "classification": "recently_deleted_album",
                "capture_method": "afc_pull",
                "mime_type": "image/jpeg",
                "size_bytes": len(payload),
                "sha256": digest,
                "source_uuid": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
                "original_filename": "IMG_0001.JPG",
                "captured_epoch_s": 1_800_000_100.0,
            }
        ],
        "stats": {
            "captured": 1,
            "bytes_captured": len(payload),
            "by_source": {"ios_recently_deleted": 1},
        },
        "warnings": [],
    }
    (work / "manifest-v1.json").write_text(json.dumps(manifest), encoding="utf-8")

    moved, parsed = ios_afc._commit_ios_library(work, staging)

    assert moved == 1
    assert parsed.artifacts[0].source == "ios_recently_deleted"
    assert (staging / relative).read_bytes() == payload
    metadata = ios_afc.ios_library_metadata(staging)
    assert metadata[relative].sha256 == digest
    (staging / relative).write_bytes(b"tampered-after-commit")
    assert ios_afc.ios_library_metadata(staging) == {}


@pytest.mark.unit
async def test_indexing_accepts_only_manifested_ios_library_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    staging = tmp_path / "staging"
    work = tmp_path / "work"
    relative = "ios_recovered_cache/0123456789abcdef0123456789abcdef.jpg"
    payload = b"cache-preview"
    target = work / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "not_before_epoch_s": 1_800_000_000.0,
        "artifacts": [
            {
                "relative_path": relative,
                "source": "ios_recovered_cache",
                "classification": "photos_thumbnail_cache",
                "capture_method": "afc_pull",
                "mime_type": "image/jpeg",
                "size_bytes": len(payload),
                "sha256": digest,
                "source_uuid": None,
                "original_filename": None,
                "captured_epoch_s": None,
            }
        ],
        "stats": {
            "captured": 1,
            "bytes_captured": len(payload),
            "by_source": {"ios_recovered_cache": 1},
        },
        "warnings": [],
    }
    (work / "manifest-v1.json").write_text(json.dumps(manifest), encoding="utf-8")
    ios_afc._commit_ios_library(work, staging)
    rogue = staging / "ios_recovered_cache" / "rogue.jpg"
    rogue.write_bytes(b"not-manifested")

    class FakeDatabase:
        def __init__(self) -> None:
            self.rows: list[tuple] = []

        async def fetchall(self, _query, _params):
            return []

        async def executemany(self, _query, rows):
            self.rows.extend(rows)

    database = FakeDatabase()
    monkeypatch.setattr("app.acquisition.indexing.db", database)

    async def progress(*_args, **_kwargs):
        return None

    indexed, _ = await acquisition.index_staging("ios-session", staging, progress)

    assert indexed == 1
    row = database.rows[0]
    assert row[2] == "ios_recovered_cache"
    assert row[3] == relative
    metadata = json.loads(row[9])
    assert metadata["acquisition_method"] == "ios_photo_library_recovery_v1"
    assert metadata["ios_library_classification"] == "photos_thumbnail_cache"


@pytest.mark.unit
async def test_ios_media_flow_adds_library_recovery_without_replacing_dcim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_process(argv, **kwargs):
        command = tuple(str(item) for item in argv)
        operation = str(kwargs["operation"])
        calls.append((operation, command))
        if operation == "ios_afc_media":
            output = Path(command[command.index("-o") + 1])
            output.mkdir(parents=True)
            (output / "recent.jpg").write_bytes(b"recent")
        elif operation == "ios_photo_library_recovery":
            output = Path(command[command.index("--output") + 1])
            relative = "ios_hidden/0123456789abcdef0123456789abcdef.jpg"
            payload = b"hidden"
            target = output / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            manifest = {
                "schema_version": 1,
                "status": "complete",
                "not_before_epoch_s": float(
                    command[command.index("--not-before-epoch-s") + 1]
                ),
                "artifacts": [
                    {
                        "relative_path": relative,
                        "source": "ios_hidden",
                        "classification": "hidden_album",
                        "capture_method": "afc_pull",
                        "mime_type": "image/jpeg",
                        "size_bytes": len(payload),
                        "sha256": digest,
                        "source_uuid": None,
                        "original_filename": "HIDDEN.JPG",
                        "captured_epoch_s": None,
                    }
                ],
                "stats": {
                    "captured": 1,
                    "bytes_captured": len(payload),
                    "by_source": {"ios_hidden": 1},
                },
                "warnings": [],
            }
            (output / "manifest-v1.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
        return ProcessResult(command, 0, "", "")

    progress_events: list[dict] = []

    async def progress(*_args, **fields):
        progress_events.append(fields)

    monkeypatch.setattr(ios_afc, "run_process", fake_process)
    monkeypatch.setattr(config.settings, "ios_afc_media_enabled", True)
    monkeypatch.setattr(config.settings, "ios_photo_library_recovery_enabled", True)
    fake_py = tmp_path / "python"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_script = tmp_path / "pull_recent_media.py"
    fake_script.write_text("# stub\n", encoding="utf-8")
    fake_library = tmp_path / "pull_library_artifacts.py"
    fake_library.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(ios_afc, "_venv_python", lambda: fake_py)
    monkeypatch.setattr(ios_afc, "_puller_root", lambda: tmp_path)

    staging = tmp_path / "staging"
    moved = await ios_afc.acquire_ios_afc_media(
        "ios-session",
        "00008101-0008384601D8001E",
        staging,
        AcquisitionMode.QUICK,
        progress,
    )

    assert moved == 2
    assert (staging / "gallery" / "recent.jpg").is_file()
    assert (
        staging / "ios_hidden" / "0123456789abcdef0123456789abcdef.jpg"
    ).is_file()
    assert [item[0] for item in calls] == [
        "ios_afc_media",
        "ios_photo_library_recovery",
    ]
    assert progress_events[-1]["ios_hidden_captured"] == 1
    assert progress_events[-1]["ios_recently_deleted_captured"] == 0
