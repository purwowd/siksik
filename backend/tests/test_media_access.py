from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import config
from app.core.db import db, utcnow


async def _insert_session(session_id: str) -> None:
    now = utcnow()
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
            "android-media",
            "android",
            "Tes streaming",
            "quick",
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            "{}",
            "LULUS",
            None,
            None,
            0,
            now,
            now,
        ),
    )


@pytest.mark.asyncio
async def test_media_ticket_is_scoped_and_supports_range_streaming(client) -> None:
    session_id = "session-media-ticket"
    await _insert_session(session_id)
    staging = config.settings.staging_dir / session_id / "video"
    staging.mkdir(parents=True)
    first = staging / "first.mp4"
    second = staging / "second.mp4"
    first.write_bytes(b"0123456789abcdef")
    second.write_bytes(b"other-video")

    issued = await client.post(
        f"/api/v1/sessions/{session_id}/media-ticket",
        json={"path": "video/first.mp4"},
    )
    assert issued.status_code == 200, issued.text
    ticket = issued.json()["ticket"]

    ranged = await client.get(
        f"/api/v1/sessions/{session_id}/media",
        params={"path": "video/first.mp4", "ticket": ticket},
        headers={"Authorization": "", "Range": "bytes=0-3"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"0123"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"].startswith("bytes 0-3/")

    wrong_path = await client.get(
        f"/api/v1/sessions/{session_id}/media",
        params={"path": "video/second.mp4", "ticket": ticket},
        headers={"Authorization": ""},
    )
    assert wrong_path.status_code == 401

    bearer = await client.get(
        f"/api/v1/sessions/{session_id}/media",
        params={"path": "video/second.mp4"},
    )
    assert bearer.status_code == 200


@pytest.mark.asyncio
async def test_video_thumbnail_returns_jpeg_and_rejects_non_video(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "session-media-thumb"
    await _insert_session(session_id)
    staging = config.settings.staging_dir / session_id
    video_dir = staging / "video"
    video_dir.mkdir(parents=True)
    clip = video_dir / "clip.mp4"
    clip.write_bytes(b"not-a-real-mp4")
    photo = staging / "photo.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"\xff\xd8\xff\xd9")

    jpeg = tmp_path / "poster.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(
        "app.services.video_poster.extract_video_poster",
        lambda _path: jpeg,
    )

    ok = await client.get(
        f"/api/v1/sessions/{session_id}/media",
        params={"path": "video/clip.mp4", "thumb": "1"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"].startswith("image/jpeg")
    assert ok.content == jpeg.read_bytes()

    bad = await client.get(
        f"/api/v1/sessions/{session_id}/media",
        params={"path": "photo.jpg", "thumb": "1"},
    )
    assert bad.status_code == 400
