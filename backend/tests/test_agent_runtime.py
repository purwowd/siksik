from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.acquisition.runtime import (
    AgentRuntimeRepository,
    AgentRuntimeState,
    device_ref,
)
from app.core.db import MIGRATIONS, Database, utcnow
from app.core.logging import StructuredJsonFormatter


async def create_session(database: Database, session_id: str) -> None:
    now = utcnow()
    await database.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "fixture-device",
            "android",
            "Fixture",
            "quick",
            "lulus",
            "pending",
            "{}",
            "{}",
            None,
            None,
            now,
            now,
        ),
    )


@pytest.mark.unit
async def test_migrations_are_idempotent_and_runtime_is_redacted(tmp_path: Path) -> None:
    database = Database(tmp_path / "runtime.db")
    await database.connect()
    await create_session(database, "session-runtime-001")
    repository = AgentRuntimeRepository(database)
    token = "secret-runtime-token-" + "x" * 32
    record = await repository.upsert(
        session_id="session-runtime-001",
        serial="device-serial-001",
        state=AgentRuntimeState.ACTIVE,
        api_version="1.0",
        agent_version="0.1.0",
        forward_host_port=41234,
        token=token,
        token_expires_at="2026-07-16T12:00:00+00:00",
        request_id="request-runtime-001",
    )

    assert record.device_ref == device_ref("device-serial-001")
    assert record.forward_host_port == 41234
    raw = await database.fetchone(
        "SELECT * FROM agent_runtimes WHERE session_id = ?",
        (record.session_id,),
    )
    assert raw is not None
    serialized = json.dumps(dict(raw))
    assert "device-serial-001" not in serialized
    assert token not in serialized
    versions = await database.fetchall("SELECT version FROM schema_migrations ORDER BY version")
    expected_versions = [version for version, _name, _handler in MIGRATIONS]
    assert [row["version"] for row in versions] == expected_versions

    await database.close()
    await database.connect()
    versions = await database.fetchall("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in versions] == expected_versions
    await database.close()


@pytest.mark.unit
async def test_runtime_close_clears_connection_secrets(tmp_path: Path) -> None:
    database = Database(tmp_path / "runtime-close.db")
    await database.connect()
    await create_session(database, "session-runtime-002")
    repository = AgentRuntimeRepository(database)
    await repository.upsert(
        session_id="session-runtime-002",
        serial="device-serial-002",
        state=AgentRuntimeState.ACTIVE,
        forward_host_port=41235,
        token="t" * 40,
        token_expires_at="2026-07-16T12:00:00+00:00",
    )
    closed = await repository.close("session-runtime-002")
    assert closed.state == AgentRuntimeState.CLOSED
    assert closed.forward_host_port is None
    assert closed.token_fingerprint is None
    assert closed.token_expires_at is None
    await database.close()


@pytest.mark.unit
def test_structured_logging_omits_sensitive_fields() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "siksik.acquisition.test",
        logging.INFO,
        __file__,
        1,
        "agent_handshake_completed",
        (),
        None,
    )
    record.request_id = "request-log-001"
    record.device_ref = "android:abc"
    record.serial = "raw-device-serial"
    record.token = "secret-token"
    record.content = "private-content"
    payload = json.loads(formatter.format(record))
    assert payload["event"] == "agent_handshake_completed"
    assert payload["device_ref"] == "android:abc"
    assert "serial" not in payload
    assert "token" not in payload
    assert "content" not in payload


@pytest.mark.api
async def test_request_id_is_echoed_and_invalid_value_is_replaced(anon_client) -> None:
    accepted = await anon_client.get(
        "/api/v1/auth/roles",
        headers={"X-Request-ID": "request-http-001"},
    )
    assert accepted.headers["X-Request-ID"] == "request-http-001"

    replaced = await anon_client.get(
        "/api/v1/auth/roles",
        headers={"X-Request-ID": "invalid request id"},
    )
    assert replaced.headers["X-Request-ID"] != "invalid request id"
    assert replaced.headers["X-Request-ID"]
