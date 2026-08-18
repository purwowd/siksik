from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode
from app.services.gallery import (
    ACCESS_ALL,
    ACCESS_FAVORITE,
    ACCESS_FREQUENT,
    ACCESS_RECENT,
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
) -> None:
    stamp = captured_at or datetime.now(timezone.utc).isoformat()
    meta = {
        "album": album,
        "directory_hint": f"Pictures/{album}",
        "display_name": path.rsplit("/", 1)[-1],
        "is_favorite": favorite,
        "captured_at": stamp,
        "date_added": date_added or stamp,
        "date_modified": date_modified or stamp,
        "date_taken": date_taken or stamp,
    }
    await db.execute(
        """
        INSERT INTO files (
            id, session_id, source, path, mime, size_bytes, sha256,
            pull_status, analyzed, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, session_id, source, path, mime, 100, sha256, "pulled", 1, json.dumps(meta)),
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
async def test_gallery_includes_flagged_and_dedupes_sha(client) -> None:
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
    assert access[0].count == 2
    origin = {item.id: item.count for item in albums if item.kind == "album"}
    assert origin["screenshots"] == 1
    assert origin["download"] == 1

    all_items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 10)
    assert all_items.total == 2
    favorite = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_FAVORITE, 1, 10)
    assert favorite.total == 1
    assert favorite.items[0].file_id == "file-b"


@pytest.mark.asyncio
async def test_gallery_respects_quick_time_window(client) -> None:
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
    assert items.total == 1
    assert items.items[0].file_id == "file-new"


@pytest.mark.asyncio
async def test_gallery_keeps_old_favorites_outside_quick_window(client) -> None:
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
async def test_gallery_keeps_or_dates_and_session_reference(client) -> None:
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
    assert {item.file_id for item in items.items} == {"file-or"}


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
    assert albums.json()[0]["count"] == 1

    gallery = await client.get(
        f"/api/v1/sessions/{session_id}/gallery",
        params={"album": "screenshots"},
    )
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 1


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
