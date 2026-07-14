"""Lab demo / simulator visibility."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core import config


@pytest.mark.api
async def test_devices_hide_simulators_when_lab_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "lab_demo_mode", False)
    res = await client.get("/api/v1/devices")
    assert res.status_code == 200
    assert all(not d["simulated"] for d in res.json())


@pytest.mark.api
async def test_devices_show_simulators_when_lab_on(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "lab_demo_mode", True)
    res = await client.get("/api/v1/devices")
    assert res.status_code == 200
    ids = {d["device_id"] for d in res.json()}
    assert "sim-android-01" in ids


@pytest.mark.api
async def test_reject_simulated_session_when_lab_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "lab_demo_mode", False)
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 50,
            "force_simulated": True,
        },
    )
    assert res.status_code == 403
    assert "lab" in res.json()["detail"].lower() or "simulator" in res.json()["detail"].lower()
