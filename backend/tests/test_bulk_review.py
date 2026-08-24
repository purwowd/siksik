"""Bulk review findings + audit trail."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import participant_payload, wait_session


@pytest.mark.api
async def test_bulk_review_updates_all_pending(client: AsyncClient):
    start = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 40,
            "participant": participant_payload(registration_no="BULK-001"),
        },
    )
    assert start.status_code == 200
    sid = start.json()["id"]
    await wait_session(client, sid)

    pending = await client.get(
        f"/api/v1/sessions/{sid}/findings?review_status=pending&page_size=500",
    )
    assert pending.status_code == 200
    body = pending.json()
    if body["total"] == 0:
        pytest.skip("sim scenario produced no pending findings")

    bulk = await client.post(
        f"/api/v1/sessions/{sid}/findings/bulk-review",
        json={"review_status": "rejected"},
    )
    assert bulk.status_code == 200, bulk.text
    result = bulk.json()
    assert result["updated"] == body["total"]
    assert result["reviewed_by"]

    after = await client.get(
        f"/api/v1/sessions/{sid}/findings?review_status=pending&page_size=1",
    )
    assert after.json()["total"] == 0

    session = await client.get(f"/api/v1/sessions/{sid}")
    assert session.json()["recommendation"] == "LULUS"
