from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core import config
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode
from app.services.gallery import (
    ACCESS_ALL,
    ACCESS_FAVORITE,
    ACCESS_FREQUENT,
    ACCESS_RECENT,
    _access_sets,
    _load_records,
    _record_from_row,
    album_key,
    album_leaf,
    gallery_meta_from_canonical,
    list_albums,
    list_items,
    looks_favorite,
)


def test_album_leaf_uses_last_path_segment() -> None:
    assert album_leaf("Pictures/Screenshots", "media_image/a.jpg", "media_image") == "Screenshots"
    assert album_leaf("Download", "x.jpg", "gallery") == "Download"
    assert album_leaf("DCIM/Camera", "x.jpg", "gallery") == "Camera"
    assert album_key("Screenshots") == "screenshots"


def test_favorite_tokens_match_directory_hint() -> None:
    assert looks_favorite("Pictures/Favorites", "photo.jpg")
    assert not looks_favorite("DCIM/Camera", "IMG_0001.jpg")


def test_canonical_metadata_marks_favorite_flag() -> None:
    meta = gallery_meta_from_canonical(
        {
            "source_kind": "media_image",
            "metadata": {
                "display_name": "keep.jpg",
                "directory_hint": "Pictures/Screenshots",
                "is_favorite": True,
                "date_added": "2026-08-01T00:00:00Z",
                "date_modified": "2026-08-02T00:00:00Z",
                "date_taken": "2026-08-01T12:00:00Z",
            },
        }
    )
    assert meta["album"] == "Screenshots"
    assert meta["is_favorite"] is True


def _gallery_row(
    *,
    file_id: str,
    path: str,
    source_app: str | None = None,
    role: str | None = None,
    social_scope: str | None = None,
    preview_text: str | None = None,
    is_canonical: bool = False,
    access_count: int = 0,
    last_accessed_at: str | None = None,
) -> dict:
    meta = {
        "album": "Media Sosial" if source_app else "Camera",
        "display_name": path.rsplit("/", 1)[-1],
        "captured_at": "2026-08-01T00:00:00Z",
        "date_added": "2026-08-01T00:00:00Z",
        "date_modified": "2026-08-01T00:00:00Z",
        "date_taken": "2026-08-01T00:00:00Z",
        "access_count": access_count,
    }
    if role:
        meta["crawl_artifact_role"] = role
    if last_accessed_at:
        meta["last_accessed_at"] = last_accessed_at
    return {
        "id": file_id,
        "source": "visible_ui" if source_app else "media_image",
        "path": path,
        "mime": "image/png" if role == "screenshot" else "application/json",
        "sha256": file_id.encode().hex()[:64],
        "meta_json": json.dumps(meta),
        "preview_text": preview_text,
        "crawl_source_app": source_app,
        "crawl_social_scope": social_scope,
        "source_locator": None,
        "is_canonical": int(is_canonical),
        "has_source_binary": 0,
        "has_screenshot": int(source_app == "com.instagram.android" and is_canonical),
        "is_flagged": 0,
    }


def test_gallery_row_mapping_enforces_social_display_contract() -> None:
    instagram = _record_from_row(
        _gallery_row(
            file_id="ig-shot",
            path="visible_ui/artifacts/ig-shot.png",
            source_app="com.instagram.android",
            role="screenshot",
            social_scope="own_posts",
            preview_text="Caption Instagram",
        )
    )
    assert instagram is not None
    assert instagram.album_label == "Instagram"
    assert instagram.presentation == "visual"

    invalid_x_screenshot = _record_from_row(
        _gallery_row(
            file_id="x-shot",
            path="visible_ui/artifacts/x-shot.png",
            source_app="com.twitter.android",
            role="screenshot",
            social_scope="own_tweets",
        )
    )
    assert invalid_x_screenshot is None

    x_record = _record_from_row(
        _gallery_row(
            file_id="x-record",
            path="visible_ui/x-record.siksik-record.json",
            source_app="com.twitter.android",
            role="canonical_record",
            social_scope="own_tweets",
            preview_text="Isi tweet akun",
            is_canonical=True,
        )
    )
    assert x_record is not None
    assert x_record.album_label == "X"
    assert x_record.presentation == "text"
    assert x_record.preview_text == "Isi tweet akun"


def test_recovery_mapping_separates_trash_recovered_and_normal() -> None:
    def recovery_row(file_id: str, bucket: str, classification: str) -> dict:
        row = _gallery_row(
            file_id=file_id,
            path=f"recovered_trash/{bucket}/{file_id}.jpg",
        )
        row["source"] = "recovered_trash"
        row["mime"] = "image/jpeg"
        meta = json.loads(row["meta_json"])
        meta.update(
            {
                "acquisition_method": "android_recovery_v1",
                "recovery_classification": classification,
            }
        )
        row["meta_json"] = json.dumps(meta)
        return row

    trash = _record_from_row(recovery_row("trash-item", "trash", "trash_resident"))
    recovered = _record_from_row(
        recovery_row("recovered-item", "previews", "orphan_disk_cache")
    )
    normal = _record_from_row(
        _gallery_row(file_id="normal-item", path="media_image/normal.jpg")
    )

    assert trash is not None and trash.recovery_state == "trash"
    assert trash.album_label == "Trash"
    assert recovered is not None and recovered.recovery_state == "recovered_deleted"
    assert recovered.album_label == "Recovered image"
    assert normal is not None and normal.recovery_state == "normal"
    assert normal.album_label == "Camera"


def test_access_sets_are_independent_and_capped_at_ten() -> None:
    records = []
    for index in range(12):
        row = _gallery_row(
            file_id=f"rank-{index:02d}",
            path=f"gallery/rank-{index:02d}.jpg",
            access_count=12 - index,
            last_accessed_at=f"2026-08-{index + 1:02d}T00:00:00Z",
        )
        record = _record_from_row(row)
        assert record is not None
        records.append(record)
    access = _access_sets(records)
    assert [item.file_id for item in access[ACCESS_RECENT]] == [
        f"rank-{index:02d}" for index in range(11, 1, -1)
    ]
    assert [item.file_id for item in access[ACCESS_FREQUENT]] == [
        f"rank-{index:02d}" for index in range(10)
    ]


@pytest.mark.asyncio
async def test_loader_binds_social_canonical_paths_and_artifacts(monkeypatch) -> None:
    def raw_file(row: dict) -> dict:
        return {
            key: row[key]
            for key in ("id", "source", "path", "mime", "sha256", "meta_json")
        }

    ig_canonical = _gallery_row(
        file_id="ig-record",
        path="visible_ui/ig-record.json",
        source_app="com.instagram.android",
        role="canonical_record",
        social_scope="own_posts",
        is_canonical=True,
    )
    ig_shot = _gallery_row(
        file_id="ig-shot",
        path="visible_ui/artifacts/ig-shot.png",
        source_app="com.instagram.android",
        role="screenshot",
        social_scope="own_posts",
    )
    x_canonical = _gallery_row(
        file_id="x-record",
        path="visible_ui/x-record.json",
        source_app="com.twitter.android",
        role="canonical_record",
        social_scope="own_tweets",
        is_canonical=True,
    )
    x_shot = _gallery_row(
        file_id="x-shot",
        path="visible_ui/artifacts/x-shot.png",
        source_app="com.twitter.android",
        role="screenshot",
        social_scope="own_tweets",
    )
    for row, record_id in (
        (ig_canonical, "ig-record"),
        (ig_shot, "ig-record"),
        (x_canonical, "x-record"),
        (x_shot, "x-record"),
    ):
        meta = json.loads(row["meta_json"])
        meta["crawl_record_id"] = record_id
        row["meta_json"] = json.dumps(meta)

    files = [raw_file(row) for row in (ig_canonical, ig_shot, x_canonical, x_shot)]
    crawls = [
        {
            "record_id": "ig-record",
            "crawl_id": "crawl-social",
            "source_app": "com.instagram.android",
            "social_scope": "own_posts",
            "normalized_text": "Caption Instagram",
            "canonical_json": json.dumps({"source_locator": "ig:own_posts"}),
            "canonical_path": "visible_ui/ig-record.json",
            "ingested_at": "2026-08-01T00:00:00Z",
            "ocr_text": None,
        },
        {
            "record_id": "x-record",
            "crawl_id": "crawl-social",
            "source_app": "com.twitter.android",
            "social_scope": "own_tweets",
            "normalized_text": "Isi tweet",
            "canonical_json": json.dumps({"source_locator": "x:own_tweets"}),
            "canonical_path": "visible_ui/x-record.json",
            "ingested_at": "2026-08-01T00:00:01Z",
            "ocr_text": None,
        },
    ]
    artifacts = [
        {
            "record_id": "ig-record",
            "role": "screenshot",
            "mime_type": "image/png",
            "relative_path": ig_shot["path"],
        },
        {
            "record_id": "x-record",
            "role": "screenshot",
            "mime_type": "image/png",
            "relative_path": x_shot["path"],
        },
    ]

    async def fake_fetchall(sql: str, _params):
        if "FROM files" in sql:
            return files
        if "FROM crawl_records" in sql:
            return crawls
        if "FROM crawl_artifacts" in sql:
            return artifacts
        if "FROM findings" in sql:
            return [{"file_id": "ig-record"}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "fetchall", fake_fetchall)
    records = await _load_records("session-social", AcquisitionMode.QUICK)
    assert {item.file_id for item in records} == {
        "ig-record",
        "ig-shot",
        "x-record",
        "x-shot",
    }
    ig_record = next(item for item in records if item.file_id == "ig-record")
    instagram = next(item for item in records if item.file_id == "ig-shot")
    x_record = next(item for item in records if item.file_id == "x-record")
    x_shot_record = next(item for item in records if item.file_id == "x-shot")
    assert instagram.album_label == "Instagram"
    assert instagram.presentation == "visual"
    assert instagram.is_flagged is False
    assert ig_record.preview_path == ig_shot["path"]
    assert ig_record.is_flagged is True
    assert x_record.album_label == "X"
    assert x_record.presentation == "text"
    assert x_record.preview_text == "Isi tweet"
    assert x_record.is_flagged is False
    assert x_shot_record.presentation == "text"
    assert x_shot_record.preview_path == x_canonical["path"]


@pytest.fixture
async def gallery_db(tmp_data_dir):
    if db._conn:
        await db.close()
    db.path = config.settings.db_path
    await db.connect()
    yield
    if db._conn:
        await db.close()


async def _insert_session(
    session_id: str,
    mode: str = "quick",
    created_at: str | None = None,
) -> None:
    now = created_at or utcnow()
    await db.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_by,
            review_candidates, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "android-test",
            "android",
            "Tes galeri",
            mode,
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            json.dumps({}),
            "LULUS",
            None,
            None,
            0,
            now,
            now,
        ),
    )


async def _insert_file(
    *,
    file_id: str,
    session_id: str,
    path: str,
    sha256: str,
    source: str = "media_image",
    mime: str = "image/jpeg",
    album: str = "Screenshots",
    favorite: bool = False,
    captured_at: str | None = None,
    date_taken: str | None = None,
    date_added: str | None = None,
    date_modified: str | None = None,
    directory_hint: str | None = None,
    display_name: str | None = None,
    role: str | None = None,
    crawl_record_id: str | None = None,
    source_app: str | None = None,
    social_scope: str | None = None,
    access_count: int | None = None,
    last_accessed_at: str | None = None,
) -> None:
    stamp = captured_at or datetime.now(timezone.utc).isoformat()
    meta = {
        "album": album,
        "directory_hint": directory_hint or f"Pictures/{album}",
        "display_name": display_name or path.rsplit("/", 1)[-1],
        "is_favorite": favorite,
        "captured_at": stamp,
        "date_added": date_added or stamp,
        "date_modified": date_modified or stamp,
        "date_taken": date_taken or stamp,
    }
    if role:
        meta["crawl_artifact_role"] = role
    if crawl_record_id:
        meta["crawl_record_id"] = crawl_record_id
    if source_app:
        meta["source_app"] = source_app
    if social_scope:
        meta["social_scope"] = social_scope
    if access_count is not None:
        meta["access_count"] = access_count
    if last_accessed_at:
        meta["last_accessed_at"] = last_accessed_at
    await db.execute(
        """
        INSERT INTO files (
            id, session_id, source, path, mime, size_bytes, sha256,
            pull_status, analyzed, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, session_id, source, path, mime, 100, sha256, "pulled", 1, json.dumps(meta)),
    )


async def _insert_crawl_record(
    *,
    session_id: str,
    record_id: str,
    source_app: str,
    social_scope: str,
    text: str,
    canonical_path: str,
) -> None:
    crawl_id = f"crawl-{session_id}"
    canonical = {
        "record_id": record_id,
        "source_kind": "visible_ui",
        "source_app": source_app,
        "source_locator": f"social:{source_app}:{social_scope}:{record_id}",
        "normalized_text": text,
        "metadata": {
            "package_name": source_app,
            "social_scope": social_scope,
        },
    }
    await db.execute(
        """
        INSERT INTO crawl_records (
            record_id, crawl_id, session_id, source_kind, source_app,
            social_scope, normalized_text, content_sha256, selection_revision,
            selection_fingerprint, canonical_json, canonical_path, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            crawl_id,
            session_id,
            "visible_ui",
            source_app,
            social_scope,
            text,
            (record_id.encode().hex() + "0" * 64)[:64],
            1,
            "gallery-social",
            json.dumps(canonical),
            canonical_path,
            utcnow(),
        ),
    )


async def _insert_crawl_artifact(
    *,
    session_id: str,
    record_id: str,
    artifact_id: str,
    path: str,
) -> None:
    await db.execute(
        """
        INSERT INTO crawl_artifacts (
            artifact_id, crawl_id, session_id, record_id, source_kind, role,
            mime_type, relative_path, size_bytes, sha256, verified, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            f"crawl-{session_id}",
            session_id,
            record_id,
            "visible_ui",
            "screenshot",
            "image/png",
            path,
            100,
            (artifact_id.encode().hex() + "0" * 64)[:64],
            1,
            utcnow(),
        ),
    )


async def _insert_finding(
    *,
    finding_id: str,
    session_id: str,
    file_id: str,
    path: str,
    label: str = "indikasi",
    review_status: str = "pending",
    confidence: float = 0.9,
) -> None:
    await db.execute(
        """
        INSERT INTO findings (
            id, session_id, file_id, source, path, category, label,
            confidence, layer_origin, evidence, review_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding_id,
            session_id,
            file_id,
            "media_image",
            path,
            "konten_visual",
            label,
            confidence,
            "L3",
            "bukti",
            review_status,
            utcnow(),
        ),
    )


@pytest.mark.asyncio
async def test_gallery_includes_flagged_unflagged_and_duplicate_paths(gallery_db) -> None:
    session_id = "session-gallery-001"
    await _insert_session(session_id)
    await _insert_file(
        file_id="file-a",
        session_id=session_id,
        path="media_image/a.jpg",
        sha256="a" * 64,
        album="Screenshots",
    )
    await _insert_file(
        file_id="file-a-dup",
        session_id=session_id,
        path="gallery/a-copy.jpg",
        sha256="a" * 64,
        album="Screenshots",
    )
    await _insert_file(
        file_id="file-b",
        session_id=session_id,
        path="media_image/b.jpg",
        sha256="b" * 64,
        album="Download",
        favorite=True,
    )
    await _insert_finding(
        finding_id="finding-a",
        session_id=session_id,
        file_id="file-a",
        path="media_image/a.jpg",
    )

    albums = await list_albums(session_id, AcquisitionMode.QUICK)
    access = [item for item in albums if item.kind == "access"]
    assert [item.id for item in access] == [
        ACCESS_ALL,
        ACCESS_FREQUENT,
        ACCESS_RECENT,
        ACCESS_FAVORITE,
    ]
    assert access[0].count == 3
    assert access[1].count == 3
    assert access[2].count == 3
    origin = {item.id: item.count for item in albums if item.kind == "album"}
    assert origin["screenshots"] == 2
    assert origin["download"] == 1

    all_items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 10)
    assert all_items.total == 3
    by_id = {item.file_id: item for item in all_items.items}
    assert by_id["file-a"].flagged is True
    assert by_id["file-a-dup"].flagged is False
    favorite = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_FAVORITE, 1, 10)
    assert favorite.total == 1
    assert favorite.items[0].file_id == "file-b"


@pytest.mark.asyncio
async def test_gallery_keeps_every_transferred_record_without_refiltering_time(gallery_db) -> None:
    session_id = "session-gallery-window"
    await _insert_session(session_id, mode="quick")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    await _insert_file(
        file_id="file-old",
        session_id=session_id,
        path="media_image/old.jpg",
        sha256="c" * 64,
        album="Camera",
        captured_at=old,
    )
    await _insert_file(
        file_id="file-new",
        session_id=session_id,
        path="media_image/new.jpg",
        sha256="d" * 64,
        album="Camera",
    )
    items = await list_items(session_id, AcquisitionMode.QUICK, "camera", 1, 10)
    assert items.total == 2
    assert {item.file_id for item in items.items} == {"file-old", "file-new"}


@pytest.mark.asyncio
async def test_gallery_keeps_old_favorites_outside_quick_window(gallery_db) -> None:
    session_id = "session-gallery-old-favorite"
    await _insert_session(session_id, mode="quick")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    await _insert_file(
        file_id="file-old-fav",
        session_id=session_id,
        path="media_image/old-fav.jpg",
        sha256="f" * 64,
        album="Camera",
        favorite=True,
        captured_at=old,
        date_taken=old,
        date_added=old,
        date_modified=old,
    )
    items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 10)
    favorite = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_FAVORITE, 1, 10)
    assert items.total == 1
    assert items.items[0].file_id == "file-old-fav"
    assert favorite.total == 1
    assert favorite.items[0].favorite is True


@pytest.mark.asyncio
async def test_gallery_keeps_or_dates_and_session_reference(gallery_db) -> None:
    session_id = "session-gallery-or-window"
    created = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc).isoformat()
    await _insert_session(session_id, mode="quick", created_at=created)
    await _insert_file(
        file_id="file-or",
        session_id=session_id,
        path="media_image/or.jpg",
        sha256="1" * 64,
        album="Camera",
        date_taken=(datetime(2020, 1, 1, tzinfo=timezone.utc)).isoformat(),
        date_added=(datetime(2026, 2, 1, tzinfo=timezone.utc)).isoformat(),
        date_modified=(datetime(2020, 1, 2, tzinfo=timezone.utc)).isoformat(),
    )
    await _insert_file(
        file_id="file-old-all",
        session_id=session_id,
        path="media_image/old-all.jpg",
        sha256="2" * 64,
        album="Camera",
        captured_at=(datetime(2020, 1, 1, tzinfo=timezone.utc)).isoformat(),
    )
    items = await list_items(session_id, AcquisitionMode.QUICK, "camera", 1, 10)
    assert {item.file_id for item in items.items} == {"file-or", "file-old-all"}


@pytest.mark.asyncio
async def test_gallery_api_and_finding_dedup(client) -> None:
    session_id = "session-gallery-api"
    await _insert_session(session_id)
    await _insert_file(
        file_id="file-same-1",
        session_id=session_id,
        path="media_image/one.jpg",
        sha256="e" * 64,
        album="Screenshots",
    )
    await _insert_file(
        file_id="file-same-2",
        session_id=session_id,
        path="gallery/one-again.jpg",
        sha256="e" * 64,
        album="Screenshots",
    )
    await _insert_finding(
        finding_id="finding-1",
        session_id=session_id,
        file_id="file-same-1",
        path="media_image/one.jpg",
        confidence=0.4,
    )
    await _insert_finding(
        finding_id="finding-2",
        session_id=session_id,
        file_id="file-same-2",
        path="gallery/one-again.jpg",
        confidence=0.9,
    )

    findings = await client.get(f"/api/v1/sessions/{session_id}/findings")
    assert findings.status_code == 200
    body = findings.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "finding-2"

    albums = await client.get(f"/api/v1/sessions/{session_id}/gallery/albums")
    assert albums.status_code == 200
    assert albums.json()[0]["id"] == ACCESS_ALL
    assert albums.json()[0]["count"] == 2

    gallery = await client.get(
        f"/api/v1/sessions/{session_id}/gallery",
        params={"album": "screenshots"},
    )
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 2


@pytest.mark.asyncio
async def test_rescan_does_not_copy_previous_review(client) -> None:
    first = "session-gallery-first"
    second = "session-gallery-second"
    digest = "f" * 64
    await _insert_session(first)
    await _insert_session(second)
    await _insert_file(
        file_id="file-first",
        session_id=first,
        path="media_image/same.jpg",
        sha256=digest,
    )
    await _insert_file(
        file_id="file-second",
        session_id=second,
        path="media_image/same.jpg",
        sha256=digest,
    )
    await _insert_finding(
        finding_id="finding-first",
        session_id=first,
        file_id="file-first",
        path="media_image/same.jpg",
        review_status="confirmed",
    )
    await _insert_finding(
        finding_id="finding-second",
        session_id=second,
        file_id="file-second",
        path="media_image/same.jpg",
        review_status="pending",
    )
    first_body = (await client.get(f"/api/v1/sessions/{first}/findings")).json()
    second_body = (await client.get(f"/api/v1/sessions/{second}/findings")).json()
    assert first_body["items"][0]["review_status"] == "confirmed"
    assert second_body["items"][0]["review_status"] == "pending"
    assert first_body["items"][0]["id"] != second_body["items"][0]["id"]


@pytest.mark.asyncio
async def test_gallery_includes_documents_audio_and_structured_text(gallery_db) -> None:
    session_id = "session-gallery-all-types"
    created = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc).isoformat()
    await _insert_session(session_id, created_at=created)
    await _insert_file(
        file_id="file-document",
        session_id=session_id,
        path="document/record_document.bin",
        sha256="3" * 64,
        source="document",
        mime="application/pdf",
        album="Subfolder",
        directory_hint="/storage/emulated/0/Download/Subfolder",
        display_name="laporan.pdf",
        role="source_binary",
    )
    await _insert_file(
        file_id="file-audio",
        session_id=session_id,
        path="media_audio/rekaman.mp3",
        sha256="4" * 64,
        source="media_audio",
        mime="audio/mpeg",
        album="Audio",
        directory_hint="Music/Voice",
        display_name="rekaman.mp3",
        role="source_binary",
    )
    record_id = "record_sms_gallery"
    await db.execute(
        """
        INSERT INTO crawl_records (
            record_id, crawl_id, session_id, source_kind, source_app,
            social_scope, normalized_text, content_sha256, selection_revision,
            selection_fingerprint, canonical_json, canonical_path, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            "crawl_sms_gallery",
            session_id,
            "sms",
            "com.android.providers.telephony",
            None,
            "Pesan singkat yang mudah dibaca di preview",
            "5" * 64,
            1,
            "selection-gallery",
            "{}",
            "sms/record.json",
            created,
        ),
    )
    await _insert_file(
        file_id="file-sms",
        session_id=session_id,
        path="sms/record.siksik-record.json",
        sha256="5" * 64,
        source="sms",
        mime="application/vnd.siksik.crawl-record+json",
        album="Pesan",
        display_name="Percakapan SMS",
        role="canonical_record",
        crawl_record_id=record_id,
    )
    await _insert_file(
        file_id="file-image-canonical",
        session_id=session_id,
        path="media_image/record.siksik-record.json",
        sha256="6" * 64,
        source="media_image",
        mime="application/vnd.siksik.crawl-record+json",
        role="canonical_record",
    )
    await _insert_file(
        file_id="file-email-json",
        session_id=session_id,
        path="email/email_metadata.json",
        sha256="7" * 64,
        source="email",
        mime="application/json",
        album="Email",
    )

    items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 20)
    assert {item.file_id for item in items.items} == {
        "file-document",
        "file-audio",
        "file-sms",
        "file-image-canonical",
        "file-email-json",
    }
    document = next(item for item in items.items if item.file_id == "file-document")
    assert document.album == "Download"
    assert document.path == "document/record_document.bin"
    assert document.source_path == "/storage/emulated/0/Download/Subfolder/laporan.pdf"
    assert document.preview_path == "document/record_document.bin"
    sms = next(item for item in items.items if item.file_id == "file-sms")
    assert sms.preview_text == "Pesan singkat yang mudah dibaca di preview"


@pytest.mark.asyncio
async def test_gallery_mode_does_not_hide_records_already_transferred(gallery_db) -> None:
    reference = datetime(2026, 8, 31, 9, 30, tzinfo=timezone.utc).isoformat()
    quick_id = "session-gallery-calendar-quick"
    full_id = "session-gallery-calendar-full"
    await _insert_session(quick_id, mode="quick", created_at=reference)
    await _insert_session(full_id, mode="full", created_at=reference)
    values = [
        ("quick-edge", datetime(2026, 5, 31, 9, 30, tzinfo=timezone.utc), "document", "text/plain"),
        ("quick-old", datetime(2026, 5, 30, 9, 30, tzinfo=timezone.utc), "media_audio", "audio/mpeg"),
        ("full-edge", datetime(2026, 2, 28, 9, 30, tzinfo=timezone.utc), "document", "application/pdf"),
        ("full-old", datetime(2026, 2, 27, 9, 30, tzinfo=timezone.utc), "media_video", "video/mp4"),
    ]
    for session_id in (quick_id, full_id):
        for index, (name, stamp, source, mime) in enumerate(values):
            await _insert_file(
                file_id=f"{session_id}-{name}",
                session_id=session_id,
                path=f"documents/{name}.txt",
                sha256=f"{index + 8:x}" * 64,
                source=source,
                mime=mime,
                album="Documents",
                captured_at=stamp.isoformat(),
                date_taken=stamp.isoformat(),
                date_added=stamp.isoformat(),
                date_modified=stamp.isoformat(),
            )

    quick = await list_items(quick_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 20)
    full = await list_items(full_id, AcquisitionMode.FULL, ACCESS_ALL, 1, 20)
    expected = {
        "quick-edge.txt",
        "quick-old.txt",
        "full-edge.txt",
        "full-old.txt",
    }
    assert {item.label for item in quick.items} == expected
    assert {item.label for item in full.items} == expected


@pytest.mark.asyncio
async def test_gallery_caps_recent_and_frequent_to_independent_top_ten(gallery_db) -> None:
    session_id = "session-gallery-top-ten"
    await _insert_session(session_id)
    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    for index in range(12):
        accessed = (base + timedelta(days=index)).isoformat()
        await _insert_file(
            file_id=f"file-rank-{index:02d}",
            session_id=session_id,
            path=f"gallery/rank-{index:02d}.jpg",
            sha256=f"{index + 20:064x}",
            album="Camera",
            captured_at=base.isoformat(),
            date_taken=base.isoformat(),
            date_added=base.isoformat(),
            date_modified=base.isoformat(),
            access_count=12 - index,
            last_accessed_at=accessed,
        )

    albums = await list_albums(session_id, AcquisitionMode.QUICK)
    counts = {item.id: item.count for item in albums if item.kind == "access"}
    assert counts[ACCESS_ALL] == 12
    assert counts[ACCESS_RECENT] == 10
    assert counts[ACCESS_FREQUENT] == 10

    recent = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_RECENT, 1, 20)
    frequent = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_FREQUENT, 1, 20)
    assert [item.file_id for item in recent.items] == [
        f"file-rank-{index:02d}" for index in range(11, 1, -1)
    ]
    assert [item.file_id for item in frequent.items] == [
        f"file-rank-{index:02d}" for index in range(10)
    ]
    assert frequent.items[0].access_count == 12


@pytest.mark.asyncio
async def test_gallery_maps_social_sources_and_enforces_presentation_rules(gallery_db) -> None:
    session_id = "session-gallery-social"
    await _insert_session(session_id)
    social = [
        (
            "ig-record",
            "com.instagram.android",
            "own_posts",
            "Caption posting Instagram",
            "visible_ui/ig-record.siksik-record.json",
        ),
        (
            "x-record",
            "com.twitter.android",
            "own_tweets",
            "Isi tweet akun yang harus tampil sebagai teks",
            "visible_ui/x-record.siksik-record.json",
        ),
        (
            "fb-record",
            "com.facebook.katana",
            "own_comments",
            "Komentar Facebook yang harus tampil sebagai teks",
            "visible_ui/fb-record.siksik-record.json",
        ),
    ]
    for record_id, source_app, scope, text, canonical_path in social:
        await _insert_crawl_record(
            session_id=session_id,
            record_id=record_id,
            source_app=source_app,
            social_scope=scope,
            text=text,
            canonical_path=canonical_path,
        )
        await _insert_file(
            file_id=f"file-{record_id}",
            session_id=session_id,
            path=canonical_path,
            sha256=f"{len(record_id):064x}",
            source="visible_ui",
            mime="application/vnd.siksik.crawl-record+json",
            album="Media Sosial",
            role="canonical_record",
            crawl_record_id=record_id,
            source_app=source_app,
            social_scope=scope,
        )

    screenshot_rows = [
        ("ig-record", "ig-shot-1", "visible_ui/artifacts/ig-shot-1.png", "com.instagram.android"),
        ("ig-record", "ig-shot-2", "visible_ui/artifacts/ig-shot-2.png", "com.instagram.android"),
        ("x-record", "x-shot-invalid", "visible_ui/artifacts/x-shot-invalid.png", "com.twitter.android"),
    ]
    for record_id, artifact_id, path, source_app in screenshot_rows:
        await _insert_crawl_artifact(
            session_id=session_id,
            record_id=record_id,
            artifact_id=artifact_id,
            path=path,
        )
        await _insert_file(
            file_id=f"file-{artifact_id}",
            session_id=session_id,
            path=path,
            sha256=f"{len(artifact_id) + 100:064x}",
            source="visible_ui",
            mime="image/png",
            album="Media Sosial",
            role="screenshot",
            crawl_record_id=record_id,
            source_app=source_app,
        )

    albums = await list_albums(session_id, AcquisitionMode.QUICK)
    origins = {item.id: item.count for item in albums if item.kind == "album"}
    assert origins == {"instagram": 2, "x": 1, "facebook": 1}

    all_items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 20)
    assert all_items.total == 4
    instagram = await list_items(session_id, AcquisitionMode.QUICK, "instagram", 1, 20)
    assert instagram.total == 2
    assert {item.preview_path for item in instagram.items} == {
        "visible_ui/artifacts/ig-shot-1.png",
        "visible_ui/artifacts/ig-shot-2.png",
    }
    assert all(item.presentation == "visual" for item in instagram.items)
    assert all(item.source_app == "com.instagram.android" for item in instagram.items)

    x_items = await list_items(session_id, AcquisitionMode.QUICK, "x", 1, 20)
    assert x_items.total == 1
    assert all(item.presentation == "text" for item in x_items.items)
    assert all(
        item.preview_path == "visible_ui/x-record.siksik-record.json"
        for item in x_items.items
    )
    assert all(
        item.preview_text == "Isi tweet akun yang harus tampil sebagai teks"
        for item in x_items.items
    )

    facebook = await list_items(session_id, AcquisitionMode.QUICK, "facebook", 1, 20)
    assert facebook.total == 1
    assert facebook.items[0].presentation == "text"
    assert facebook.items[0].social_scope == "own_comments"
    assert facebook.items[0].source_path == (
        "social:com.facebook.katana:own_comments:fb-record"
    )
