from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from app.acquisition.adb import validate_serial
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.core.db import Database, db, utcnow


class AgentRuntimeState(str, Enum):
    PREPARING = "preparing"
    ACTIVE = "active"
    DEGRADED = "degraded"
    CLOSED = "closed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DETECT_DEVICE = "detect_device"
    VALIDATE_DEVICE = "validate_device"
    RESOLVE_OR_BUILD_AGENT = "resolve_or_build_agent"
    INSPECT_INSTALLED_PACKAGE = "inspect_installed_package"
    INSTALL_OR_UPDATE = "install_or_update"
    INSTALL_AUTOMATION = "install_automation"
    AWAITING_INSTALL_APPROVAL = "awaiting_install_approval"
    APPLY_RUNTIME_PERMISSIONS = "apply_runtime_permissions"
    AWAITING_RUNTIME_PERMISSION = "awaiting_runtime_permission"
    VERIFY_SPECIAL_ACCESS = "verify_special_access"
    AWAITING_ACCESS = "awaiting_access"
    START_AGENT = "start_agent"
    CREATE_FORWARD = "create_forward"
    AUTHENTICATE_AND_NEGOTIATE = "authenticate_and_negotiate"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class AgentRuntimeRecord:
    session_id: str
    device_ref: str
    state: AgentRuntimeState
    api_version: str | None
    agent_version: str | None
    agent_build_sha256: str | None
    artifact_sha256: str | None
    forward_host_port: int | None
    token_expires_at: str | None
    token_fingerprint: str | None
    request_id: str | None
    error_category: str | None
    retryable: bool
    details: dict[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AgentRuntimeSecrets:
    session_id: str
    serial: str
    token: str
    forward_host_port: int
    token_expires_at: str
    google_token: str | None = None
    google_account: str | None = None


def device_ref(serial: str) -> str:
    value = validate_serial(serial)
    digest = hashlib.sha256(f"siksik-device:{value}".encode("utf-8")).hexdigest()
    return f"android:{digest[:24]}"


def token_fingerprint(token: str) -> str:
    if not isinstance(token, str) or len(token) < 32 or "\x00" in token:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Token agent tidak valid.")
    return hashlib.sha256(f"siksik-token:{token}".encode("utf-8")).hexdigest()


class AgentRuntimeRepository:
    def __init__(self, database: Database = db) -> None:
        self._db = database

    async def upsert(
        self,
        *,
        session_id: str,
        serial: str,
        state: AgentRuntimeState,
        api_version: str | None = None,
        agent_version: str | None = None,
        agent_build_sha256: str | None = None,
        artifact_sha256: str | None = None,
        forward_host_port: int | None = None,
        token: str | None = None,
        token_expires_at: str | None = None,
        request_id: str | None = None,
        error_category: str | None = None,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> AgentRuntimeRecord:
        if not session_id or len(session_id) > 128:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "ID sesi tidak valid.")
        if forward_host_port is not None and not 1 <= forward_host_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Port runtime tidak valid.")
        now = utcnow()
        ref = device_ref(serial)
        fingerprint = token_fingerprint(token) if token is not None else None
        await self._db.execute(
            """
            INSERT INTO agent_runtimes (
                session_id, device_ref, state, api_version, agent_version,
                agent_build_sha256, artifact_sha256,
                forward_host_port, token_expires_at, token_fingerprint,
                request_id, error_category, retryable, details_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                device_ref = excluded.device_ref,
                state = excluded.state,
                api_version = excluded.api_version,
                agent_version = excluded.agent_version,
                agent_build_sha256 = excluded.agent_build_sha256,
                artifact_sha256 = excluded.artifact_sha256,
                forward_host_port = excluded.forward_host_port,
                token_expires_at = excluded.token_expires_at,
                token_fingerprint = excluded.token_fingerprint,
                request_id = excluded.request_id,
                error_category = excluded.error_category,
                retryable = excluded.retryable,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                ref,
                state.value,
                api_version,
                agent_version,
                agent_build_sha256,
                artifact_sha256,
                forward_host_port,
                token_expires_at,
                fingerprint,
                request_id,
                error_category,
                int(retryable),
                json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
                now,
                now,
            ),
        )
        return await self.get(session_id)

    async def get(self, session_id: str) -> AgentRuntimeRecord:
        row = await self._db.fetchone(
            "SELECT * FROM agent_runtimes WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            raise acquisition_error(ErrorCategory.NOT_FOUND, "Runtime agent tidak ditemukan.")
        return AgentRuntimeRecord(
            session_id=str(row["session_id"]),
            device_ref=str(row["device_ref"]),
            state=AgentRuntimeState(row["state"]),
            api_version=row["api_version"],
            agent_version=row["agent_version"],
            agent_build_sha256=row["agent_build_sha256"],
            artifact_sha256=row["artifact_sha256"],
            forward_host_port=row["forward_host_port"],
            token_expires_at=row["token_expires_at"],
            token_fingerprint=row["token_fingerprint"],
            request_id=row["request_id"],
            error_category=row["error_category"],
            retryable=bool(row["retryable"]),
            details=json.loads(row["details_json"] or "{}"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    async def try_get(self, session_id: str) -> AgentRuntimeRecord | None:
        try:
            return await self.get(session_id)
        except AcquisitionError as exc:
            if exc.category == ErrorCategory.NOT_FOUND:
                return None
            raise

    async def latest_for_device(self, serial: str) -> AgentRuntimeRecord:
        ref = device_ref(serial)
        row = await self._db.fetchone(
            "SELECT session_id FROM agent_runtimes WHERE device_ref = ? ORDER BY updated_at DESC LIMIT 1",
            (ref,),
        )
        if row is None:
            raise acquisition_error(ErrorCategory.NOT_FOUND, "Status Android agent belum tersedia.")
        return await self.get(str(row["session_id"]))

    async def add_event(
        self,
        *,
        session_id: str,
        serial: str,
        state: AgentRuntimeState,
        percent: float,
        message_code: str,
        details: dict[str, object] | None = None,
        request_id: str | None = None,
    ) -> None:
        if not 0 <= percent <= 100 or not message_code or len(message_code) > 128:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Event bootstrap tidak valid.")
        await self._db.execute(
            """
            INSERT INTO agent_bootstrap_events (
                session_id, device_ref, state, percent, message_code,
                details_json, request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                device_ref(serial),
                state.value,
                percent,
                message_code,
                json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
                request_id,
                utcnow(),
            ),
        )

    async def close(self, session_id: str) -> AgentRuntimeRecord:
        await self.get(session_id)
        await self._db.execute(
            """
            UPDATE agent_runtimes SET
                state = ?, forward_host_port = NULL, token_expires_at = NULL,
                token_fingerprint = NULL, error_category = NULL,
                retryable = 0, updated_at = ?
            WHERE session_id = ?
            """,
            (AgentRuntimeState.CLOSED.value, utcnow(), session_id),
        )
        return await self.get(session_id)


class AgentRuntimeRegistry:
    def __init__(self) -> None:
        self._items: dict[str, AgentRuntimeSecrets] = {}
        self._lock = asyncio.Lock()

    async def bind(self, runtime: AgentRuntimeSecrets) -> None:
        validate_serial(runtime.serial)
        if not runtime.session_id or not 1 <= runtime.forward_host_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Runtime agent tidak valid.")
        token_fingerprint(runtime.token)
        async with self._lock:
            self._items[runtime.session_id] = runtime

    async def get(self, session_id: str) -> AgentRuntimeSecrets:
        async with self._lock:
            runtime = self._items.get(session_id)
        if runtime is None:
            raise acquisition_error(ErrorCategory.NOT_FOUND, "Runtime agent tidak aktif.")
        return runtime

    async def remove(self, session_id: str) -> AgentRuntimeSecrets | None:
        async with self._lock:
            return self._items.pop(session_id, None)

    async def remove_for_serial(
        self,
        serial: str,
        *,
        except_session_id: str | None = None,
    ) -> list[AgentRuntimeSecrets]:
        value = validate_serial(serial)
        async with self._lock:
            removed: list[AgentRuntimeSecrets] = []
            for session_id, runtime in list(self._items.items()):
                if runtime.serial != value:
                    continue
                if except_session_id is not None and session_id == except_session_id:
                    continue
                removed.append(self._items.pop(session_id))
            return removed

    async def pop_all(self) -> list[AgentRuntimeSecrets]:
        async with self._lock:
            values = list(self._items.values())
            self._items.clear()
        return values


agent_runtime_repository = AgentRuntimeRepository()
agent_runtime_registry = AgentRuntimeRegistry()
