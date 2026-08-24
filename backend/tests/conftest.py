"""Fixtures: isolasi DB/staging per test + auth admin."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import config
from app.core.config import ensure_dirs
from app.core.db import db
from app.main import app
from app.services.auth import ensure_auth_schema, reset_login_rate_limits
from app.services.sessions import sessions


async def cancel_session_tasks() -> None:
    tasks = list(sessions._tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    sessions._tasks.clear()
    sessions._active_device = None
    sessions._lock = asyncio.Lock()
    sessions._update_lock = asyncio.Lock()


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    staging = data / "staging"
    synthetic = data / "synthetic"
    db_path = data / "test.db"
    data.mkdir()
    staging.mkdir()
    synthetic.mkdir()

    monkeypatch.setattr(config.settings, "data_dir", data)
    monkeypatch.setattr(config.settings, "staging_dir", staging)
    monkeypatch.setattr(config.settings, "synthetic_dir", synthetic)
    monkeypatch.setattr(config.settings, "db_path", db_path)
    # Tes acceptance memakai perangkat simulator
    monkeypatch.setattr(config.settings, "lab_demo_mode", True)
    return data


@pytest.fixture
async def client(tmp_data_dir: Path) -> AsyncIterator[AsyncClient]:
    reset_login_rate_limits()
    await cancel_session_tasks()
    if db._conn:
        await db.close()
    db.path = config.settings.db_path
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Admin@2026"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        ac.headers["Authorization"] = f"Bearer {token}"
        yield ac

    await cancel_session_tasks()
    reset_login_rate_limits()
    if db._conn:
        await db.close()


@pytest.fixture
async def anon_client(tmp_data_dir: Path) -> AsyncIterator[AsyncClient]:
    """Client tanpa token (untuk uji 401)."""
    reset_login_rate_limits()
    await cancel_session_tasks()
    if db._conn:
        await db.close()
    db.path = config.settings.db_path
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await cancel_session_tasks()
    reset_login_rate_limits()
    if db._conn:
        await db.close()


async def wait_session(
    client: AsyncClient,
    session_id: str,
    *,
    timeout_s: float = 180.0,
    poll_s: float = 0.05,
) -> dict:
    """Poll until session terminal state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict = {}
    while loop.time() < deadline:
        res = await client.get(f"/api/v1/sessions/{session_id}")
        res.raise_for_status()
        last = res.json()
        if last["status"] in ("completed", "failed", "cancelled"):
            return last
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"Session {session_id} did not finish: {last.get('status')}")


def participant_payload(**overrides: object) -> dict:
    """Identitas peserta minimal untuk start session di tes."""
    import uuid

    body: dict = {
        "full_name": "Peserta Tes",
        "registration_no": f"TEST-{uuid.uuid4().hex[:10].upper()}",
    }
    body.update(overrides)
    return body


def session_start_json(**overrides: object) -> dict:
    """Payload POST /sessions dengan participant wajib."""
    body: dict = {
        "device_id": "sim-android-01",
        "device_type": "android",
        "mode": "quick",
        "scenario": "lulus",
        "file_count": 100,
        "participant": participant_payload(),
    }
    body.update(overrides)
    if "participant" in overrides and isinstance(overrides["participant"], dict):
        body["participant"] = participant_payload(**overrides["participant"])  # type: ignore[arg-type]
    return body