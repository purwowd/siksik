from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.acquisition.runtime import AgentRuntimeRecord, AgentRuntimeState, device_ref
from app.core.db import db, utcnow


def ready_record(serial: str = "serial-fixture") -> AgentRuntimeRecord:
    now = "2026-07-16T08:00:00+00:00"
    return AgentRuntimeRecord(
        session_id="session-agent-001",
        device_ref=device_ref(serial),
        state=AgentRuntimeState.READY,
        api_version="1.0",
        agent_version="0.2.0",
        agent_build_sha256="a" * 64,
        artifact_sha256="b" * 64,
        forward_host_port=43210,
        token_expires_at="2026-07-16T08:10:00+00:00",
        token_fingerprint="c" * 64,
        request_id="request-agent-api",
        error_category=None,
        retryable=False,
        details={
            "install_action": "current",
            "runtime_permissions": {"android.permission.READ_MEDIA_IMAGES": "granted"},
            "special_access": {},
            "capabilities": {"schema_version": 1},
        },
        created_at=now,
        updated_at=now,
    )


@pytest.mark.api
async def test_agent_bootstrap_endpoint_returns_only_public_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.acquisition.bootstrap import agent_bootstrap
    from app.services.sessions import sessions

    captured: list[tuple[str, str]] = []

    async def retry(session_id: str, device_id: str) -> AgentRuntimeRecord:
        captured.append((session_id, device_id))
        return ready_record(device_id)

    monkeypatch.setattr(sessions, "retry_agent_bootstrap", retry)
    result = await client.post(
        "/api/v1/agent/bootstrap",
        json={"session_id": "session-agent-001", "device_id": "serial-fixture"},
    )

    assert result.status_code == 200
    body = result.json()
    assert captured == [("session-agent-001", "serial-fixture")]
    assert body["state"] == "ready"
    assert body["ready"] is True
    assert body["device_ref"] == device_ref("serial-fixture")
    serialized = json.dumps(body)
    assert "serial-fixture" not in serialized
    assert "token" not in serialized
    assert "forward_host_port" not in serialized
    assert agent_bootstrap.public_status(ready_record())["ready"] is True


@pytest.mark.api
async def test_agent_status_endpoint_is_typed_and_request_scoped(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.acquisition.bootstrap import agent_bootstrap

    captured: list[tuple[str, str | None]] = []

    async def status(serial: str, request_id: str | None) -> AgentRuntimeRecord:
        captured.append((serial, request_id))
        return ready_record(serial)

    monkeypatch.setattr(agent_bootstrap, "status_for_device", status)
    result = await client.get(
        "/api/v1/agent/status",
        params={"device_id": "serial-fixture"},
        headers={"X-Request-ID": "request-agent-status"},
    )

    assert result.status_code == 200
    assert result.headers["X-Request-ID"] == "request-agent-status"
    assert captured == [("serial-fixture", "request-agent-status")]
    assert result.json()["install_action"] == "current"


@pytest.mark.api
async def test_agent_bootstrap_rejects_unknown_or_non_live_session(client: AsyncClient) -> None:
    missing = await client.post(
        "/api/v1/agent/bootstrap",
        json={"session_id": "missing-session", "device_id": "serial-fixture"},
    )
    assert missing.status_code == 404

    now = utcnow()
    await db.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "session-simulated-001",
            "sim-android-01",
            "android",
            "Simulator fixture",
            "quick",
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            json.dumps({}),
            "LULUS",
            None,
            now,
            now,
        ),
    )
    simulated = await client.post(
        "/api/v1/agent/bootstrap",
        json={"session_id": "session-simulated-001", "device_id": "sim-android-01"},
    )
    assert simulated.status_code == 422


@pytest.mark.api
async def test_agent_endpoints_enforce_auth_and_strict_body(anon_client: AsyncClient) -> None:
    unauthorized = await anon_client.get(
        "/api/v1/agent/status",
        params={"device_id": "serial-fixture"},
    )
    assert unauthorized.status_code == 401

    login = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin@2026"},
    )
    token = login.json()["token"]
    invalid = await anon_client.post(
        "/api/v1/agent/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "session-agent-001",
            "device_id": "serial-fixture",
            "session_token": "must-not-be-accepted",
        },
    )
    assert invalid.status_code == 422
