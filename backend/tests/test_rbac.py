"""RBAC tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.api
@pytest.mark.acceptance
async def test_login_admin(client: AsyncClient):
    # client fixture already logged in as admin
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "admin"
    assert "sessions:start" in body["permissions"]


@pytest.mark.api
async def test_login_fail(anon_client: AsyncClient):
    res = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert res.status_code == 401


@pytest.mark.api
async def test_operator_cannot_dashboard(anon_client: AsyncClient):
    login = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "Ops@2026"},
    )
    assert login.status_code == 200
    token = login.json()["token"]
    res = await anon_client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.api
async def test_analis_can_review_not_start(anon_client: AsyncClient):
    login = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "analis", "password": "Analis@2026"},
    )
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    start = await anon_client.post(
        "/api/v1/sessions",
        headers=headers,
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 50,
            "force_simulated": True,
        },
    )
    assert start.status_code == 403
    dash = await anon_client.get("/api/v1/dashboard", headers=headers)
    assert dash.status_code == 200


@pytest.mark.api
async def test_unauthenticated_blocked(anon_client: AsyncClient):
    res = await anon_client.get("/api/v1/devices")
    assert res.status_code == 401


@pytest.mark.api
async def test_roles_catalog_public(anon_client: AsyncClient):
    res = await anon_client.get("/api/v1/auth/roles")
    assert res.status_code == 200
    roles = {r["role"] for r in res.json()["roles"]}
    assert {"operator", "analis", "pimpinan", "admin"} <= roles
