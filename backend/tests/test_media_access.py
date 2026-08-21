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
