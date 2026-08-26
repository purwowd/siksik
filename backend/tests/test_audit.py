"""Audit trail + report hash for workstation authorize."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.reports import canonical_report_digest
from tests.conftest import wait_session


@pytest.mark.unit
def test_canonical_report_digest_is_stable() -> None:
    payload = {
        "session": {"id": "abc", "recommendation": "LULUS", "participant": {"full_name": "A"}},
        "findings": [{"id": "f1"}],
        "breakdown": {"by_category": {"x": 1}},
        "metrics": {"files": 3, "timing": {"t": 1}, "progress": {"percent": 100}},
    }
    first = canonical_report_digest(payload)
    payload["metrics"]["timing"] = {"t": 99}
    second = canonical_report_digest(payload)
    assert first == second
    assert len(first) == 64


@pytest.mark.api
async def test_session_audit_and_authorize_hash(client: AsyncClient, anon_client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 40,
            "participant": {"full_name": "Peserta Audit", "registration_no": "TEST-AUDIT-001"},
            "label": "Audit trail",
            "force_simulated": True,
        },
    )
    assert res.status_code == 200, res.text
    sid = res.json()["id"]
    await wait_session(client, sid)

    started = (await client.get(f"/api/v1/sessions/{sid}/audit")).json()
    assert any(row["action"] == "session_started" for row in started)

    findings = (await client.get(f"/api/v1/sessions/{sid}/findings?page_size=500")).json()
    for item in findings.get("items") or []:
        if item.get("review_status") == "pending":
            await client.patch(
                f"/api/v1/findings/{item['id']}",
                json={"review_status": "confirmed"},
            )

    login = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "pimpinan", "password": "Pimpinan@2026"},
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    ok = await anon_client.post(
        f"/api/v1/sessions/{sid}/authorize",
        headers=headers,
        json={"note": "Disahkan uji audit"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("report_sha256")
    session = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert session["progress"]["report_sha256"] == ok.json()["report_sha256"]
    trail = (await client.get(f"/api/v1/sessions/{sid}/audit")).json()
    assert any(row["action"] == "report_authorized" for row in trail)

    # Locked session rejects further finding edits.
    pending = [
        item
        for item in (await client.get(f"/api/v1/sessions/{sid}/findings?page_size=500")).json().get(
            "items"
        )
        or []
        if item.get("review_status") == "confirmed"
    ]
    if pending:
        locked = await client.patch(
            f"/api/v1/findings/{pending[0]['id']}",
            json={"review_status": "rejected"},
        )
        assert locked.status_code == 409

