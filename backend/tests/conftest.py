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
    if db._conn:
        await db.close()
    db.path = config.settings.db_path
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()

    for task in list(sessions._tasks.values()):
        task.cancel()
    sessions._tasks.clear()
    sessions._active_device = None

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

    for task in list(sessions._tasks.values()):
        task.cancel()
    sessions._tasks.clear()
    sessions._active_device = None
    reset_login_rate_limits()
    if db._conn:
        await db.close()


@pytest.fixture
async def anon_client(tmp_data_dir: Path) -> AsyncIterator[AsyncClient]:
    """Client tanpa token (untuk uji 401)."""
    reset_login_rate_limits()
    if db._conn:
        await db.close()
    db.path = config.settings.db_path
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
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
