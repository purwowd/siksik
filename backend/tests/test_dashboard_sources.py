from __future__ import annotations

import json

import pytest

from app.core.db import db, utcnow


@pytest.mark.api
@pytest.mark.asyncio
async def test_dashboard_reports_indexed_and_analyzed_sources_without_findings(client) -> None:
    session_id = "dashboard-source-session"
    now = utcnow()
    await db.execute(
        "INSERT INTO sessions (id, device_id, device_type, label, mode, scenario, "
        "status, progress_json, timing_json, recommendation, error, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            "android-dashboard",
            "android",
            "Dashboard source counts",
            "quick",
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            "{}",
            "LULUS",
            None,
            now,
            now,
        ),
    )
    other_session_id = "dashboard-other-session"
    await db.execute(
        "INSERT INTO sessions (id, device_id, device_type, label, mode, scenario, "
        "status, progress_json, timing_json, recommendation, error, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            other_session_id,
            "android-other",
            "android",
            "Dashboard other session",
            "quick",
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            "{}",
            "MENUNGGU REVIEW",
            None,
            now,
            now,
        ),
    )
    rows = []
    for source, count in (("email", 2), ("browser_history_full", 3), ("notes", 1)):
        for index in range(count):
            rows.append(
                (
                    f"{source}-{index}",
                    session_id,
                    source,
                    f"{source}/{index}.json",
                    "application/json",
                    10,
                    f"{index:064x}",
                    "pulled",
                    1,
                    "{}",
                )
            )
    await db.executemany(
        "INSERT INTO files (id, session_id, source, path, mime, size_bytes, sha256, "
        "pull_status, analyzed, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    await db.execute(
        "INSERT INTO files (id, session_id, source, path, mime, size_bytes, sha256, "
        "pull_status, analyzed, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "other-email-file",
            other_session_id,
            "email",
            "email/other.json",
            "application/json",
            10,
            "f" * 64,
            "pulled",
            1,
            "{}",
        ),
    )
    await db.execute(
        "INSERT INTO findings (id, session_id, file_id, source, path, category, "
        "label, confidence, layer_origin, evidence, review_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "other-email-finding",
            other_session_id,
            "other-email-file",
            "email",
            "email/other.json",
            "konten_teks",
            "Temuan sesi lain",
            0.8,
            "L1",
            "bukti",
            "pending",
            now,
        ),
    )
    response = await client.get(f"/api/v1/dashboard?session_id={session_id}")
    assert response.status_code == 200
    payload = response.json()
    indexed = {item["name"]: item["count"] for item in payload["files_by_source"]}
    analyzed = {
        item["name"]: item["count"]
        for item in payload["analyzed_files_by_source"]
    }
    assert indexed == {"browser_history_full": 3, "email": 2, "notes": 1}
    assert analyzed == indexed
    assert payload["findings_by_source"] == []
