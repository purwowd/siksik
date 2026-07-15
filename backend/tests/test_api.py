"""API integration tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import wait_session


@pytest.mark.api
@pytest.mark.acceptance
async def test_health(client: AsyncClient):
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "gpu_available" in body
    assert body["extras"]["worker_concurrency"] >= 1
    assert "toolchain" in body["extras"]


@pytest.mark.api
async def test_devices_include_simulators(client: AsyncClient):
    res = await client.get("/api/v1/devices")
    assert res.status_code == 200
    devices = res.json()
    ids = {d["device_id"] for d in devices}
    assert "sim-android-01" in ids
    assert "sim-iphone-01" in ids


@pytest.mark.api
@pytest.mark.acceptance
async def test_session_tidak_lulus_has_findings(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 200,
            "label": "API TIDAK LULUS",
        },
    )
    assert res.status_code == 200
    sid = res.json()["id"]
    final = await wait_session(client, sid)
    assert final["status"] == "completed"
    assert final["progress"]["findings_count"] > 0
    # Ada temuan pending → belum lulus, menunggu verifikasi analis
    assert final["recommendation"] == "MENUNGGU REVIEW"
    assert final["timing"]["t_total_ms"] > 0

    findings = (await client.get(f"/api/v1/sessions/{sid}/findings?page_size=500")).json()
    assert findings["total"] == final["progress"]["findings_count"]
    assert all("confidence" in f for f in findings["items"])

    fid = findings["items"][0]["id"]
    patched = await client.patch(f"/api/v1/findings/{fid}", json={"review_status": "confirmed"})
    assert patched.status_code == 200
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert after["recommendation"] == "TIDAK LULUS"

@pytest.mark.api
@pytest.mark.acceptance
async def test_session_lulus_zero_findings(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-iphone-01",
            "device_type": "ios",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 200,
            "label": "API LULUS",
        },
    )
    assert res.status_code == 200
    final = await wait_session(client, res.json()["id"])
    assert final["status"] == "completed"
    assert final["recommendation"] == "LULUS"
    assert final["progress"]["findings_count"] == 0


@pytest.mark.api
async def test_reject_concurrent_sessions(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    from app.models.schemas import SessionStatus
    from app.services import acquisition as acq

    async def slow_acquire(*args, **kwargs):
        on_progress = kwargs.get("on_progress") or args[5]
        await on_progress(SessionStatus.ACQUIRING, 15, "slow acquire…", files_listed=10, files_pulled=1)
        await asyncio.sleep(0.8)
        return await original_acquire(*args, **kwargs)

    original_acquire = acq.acquire_simulated
    monkeypatch.setattr(acq, "acquire_simulated", slow_acquire)

    first = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 80,
            "label": "Longish",
        },
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-iphone-01",
            "device_type": "ios",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 80,
            "label": "Should conflict",
        },
    )
    assert second.status_code == 409
    assert "Sesi lain" in second.json()["detail"]
    await wait_session(client, first.json()["id"])


@pytest.mark.api
async def test_review_finding(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 120,
            "label": "Review flow",
        },
    )
    sid = res.json()["id"]
    await wait_session(client, sid)
    findings = (await client.get(f"/api/v1/sessions/{sid}/findings")).json()
    assert findings["items"]
    fid = findings["items"][0]["id"]
    patched = await client.patch(
        f"/api/v1/findings/{fid}",
        json={"review_status": "confirmed"},
    )
    assert patched.status_code == 200
    assert patched.json()["review_status"] == "confirmed"
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert after["recommendation"] == "TIDAK LULUS"
    # Reject confirmed → MENUNGGU REVIEW jika masih ada pending, else LULUS
    await client.patch(f"/api/v1/findings/{fid}", json={"review_status": "rejected"})
    again = (await client.get(f"/api/v1/sessions/{sid}")).json()
    if findings["total"] > 1:
        assert again["recommendation"] == "MENUNGGU REVIEW"
    else:
        assert again["recommendation"] == "LULUS"

@pytest.mark.api
async def test_dashboard_aggregates(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 80,
            "label": "Dash",
            "force_simulated": True,
        },
    )
    await wait_session(client, res.json()["id"])
    dash = (await client.get("/api/v1/dashboard")).json()
    assert dash["total_sessions"] >= 1
    assert dash["completed_sessions"] >= 1
    assert "avg_total_ms" in dash
    assert "findings_by_category" in dash
    assert "toolchain" in dash
    assert "adb" in dash["toolchain"]


@pytest.mark.api
async def test_session_report(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 100,
            "label": "Report",
            "force_simulated": True,
        },
    )
    sid = res.json()["id"]
    await wait_session(client, sid)
    js = await client.get(f"/api/v1/sessions/{sid}/report?format=json")
    assert js.status_code == 200
    body = js.json()
    assert body["session"]["id"] == sid
    assert "findings" in body
    html = await client.get(f"/api/v1/sessions/{sid}/report?format=html")
    assert html.status_code == 200
    assert "SADT" in html.text


@pytest.mark.api
async def test_recompute_recommendations_migrates_pending(client: AsyncClient):
    """Admin endpoint: LULUS lama + temuan pending → MENUNGGU REVIEW."""
    from app.core.db import db

    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 120,
            "label": "Recompute migrate",
            "force_simulated": True,
        },
    )
    sid = res.json()["id"]
    final = await wait_session(client, sid)
    assert final["recommendation"] == "MENUNGGU REVIEW"
    # Simulasikan data lama (pre-migration) yang sempat tertulis LULUS
    await db.execute(
        "UPDATE sessions SET recommendation = 'LULUS' WHERE id = ?",
        (sid,),
    )
    out = (await client.post("/api/v1/admin/recompute-recommendations")).json()
    assert out["scanned"] >= 1
    assert any(c["session_id"] == sid and c["to"] == "MENUNGGU REVIEW" for c in out["changes"])
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert after["recommendation"] == "MENUNGGU REVIEW"
