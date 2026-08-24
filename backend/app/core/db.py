from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import aiosqlite

from app.core.config import settings


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-64000;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    device_type TEXT NOT NULL,
    label TEXT NOT NULL,
    mode TEXT NOT NULL,
    scenario TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    timing_json TEXT NOT NULL,
    recommendation TEXT,
    error TEXT,
    created_by TEXT,
    review_candidates INTEGER NOT NULL DEFAULT 0,
    participant_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    pull_status TEXT NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    category TEXT NOT NULL,
    label TEXT NOT NULL,
    confidence REAL NOT NULL,
    layer_origin TEXT NOT NULL,
    evidence TEXT NOT NULL,
    review_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    media_year INTEGER,
    media_captured_at TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_findings_session ON findings(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_status);

CREATE TABLE IF NOT EXISTS hash_cache (
    sha256 TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MigrationHandler = Callable[[aiosqlite.Connection], Awaitable[None]]


async def _migration_finding_media_dates(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(findings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "media_year" not in columns:
        await conn.execute("ALTER TABLE findings ADD COLUMN media_year INTEGER")
    if "media_captured_at" not in columns:
        await conn.execute("ALTER TABLE findings ADD COLUMN media_captured_at TEXT")


async def _migration_agent_runtimes(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runtimes (
            session_id TEXT PRIMARY KEY,
            device_ref TEXT NOT NULL,
            state TEXT NOT NULL,
            api_version TEXT,
            agent_version TEXT,
            forward_host_port INTEGER,
            token_expires_at TEXT,
            token_fingerprint TEXT,
            request_id TEXT,
            error_category TEXT,
            retryable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runtimes_state ON agent_runtimes(state)"
    )


async def _migration_agent_bootstrap_trace(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(agent_runtimes)")
    columns = {row[1] for row in await cursor.fetchall()}
    additions = {
        "agent_build_sha256": "TEXT",
        "artifact_sha256": "TEXT",
        "details_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, declaration in additions.items():
        if name not in columns:
            await conn.execute(f"ALTER TABLE agent_runtimes ADD COLUMN {name} {declaration}")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_bootstrap_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            device_ref TEXT NOT NULL,
            state TEXT NOT NULL,
            percent REAL NOT NULL,
            message_code TEXT NOT NULL,
            details_json TEXT NOT NULL,
            request_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_session ON agent_bootstrap_events(session_id, id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_device ON agent_bootstrap_events(device_ref, id)"
    )


async def _migration_selection_review(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(sessions)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "created_by" not in columns:
        await conn.execute("ALTER TABLE sessions ADD COLUMN created_by TEXT")
    if "review_candidates" not in columns:
        await conn.execute(
            "ALTER TABLE sessions ADD COLUMN review_candidates INTEGER NOT NULL DEFAULT 0"
        )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_runs (
            crawl_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            selection_revision INTEGER NOT NULL,
            selection_fingerprint TEXT,
            review_candidates INTEGER NOT NULL,
            selection_confirmed INTEGER NOT NULL DEFAULT 0,
            totals_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            frozen_at TEXT,
            confirmed_at TEXT,
            failure_reason TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS selection_candidates (
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_app TEXT,
            evidence_text TEXT,
            score REAL NOT NULL,
            threshold REAL NOT NULL,
            auto_selected INTEGER NOT NULL,
            selected INTEGER NOT NULL,
            matched_keywords_json TEXT NOT NULL,
            matched_rules_json TEXT NOT NULL,
            model_signals_json TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            human_override TEXT NOT NULL,
            operator_id TEXT,
            decided_at TEXT NOT NULL,
            duplicate_group_id TEXT,
            representative_record_id TEXT,
            size_bytes INTEGER,
            thumbnail_available INTEGER NOT NULL,
            PRIMARY KEY(crawl_id, record_id),
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_selection_candidates_browse "
        "ON selection_candidates(session_id, score DESC, record_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_selection_candidates_source "
        "ON selection_candidates(session_id, source_kind, selected)"
    )


async def _migration_direct_crawl_ingestion(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_permissions (
            crawl_id TEXT NOT NULL,
            permission TEXT NOT NULL,
            state TEXT NOT NULL,
            reason TEXT,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(crawl_id, permission),
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_records (
            record_id TEXT NOT NULL,
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_app TEXT,
            social_scope TEXT,
            normalized_text TEXT,
            content_sha256 TEXT,
            selection_revision INTEGER NOT NULL,
            selection_fingerprint TEXT NOT NULL,
            canonical_json TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY(crawl_id, record_id),
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_artifacts (
            artifact_id TEXT PRIMARY KEY,
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            role TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            verified INTEGER NOT NULL,
            ingested_at TEXT NOT NULL,
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id),
            FOREIGN KEY(crawl_id, record_id) REFERENCES crawl_records(crawl_id, record_id)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_transfers (
            stage_id TEXT PRIMARY KEY,
            crawl_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            selection_revision INTEGER NOT NULL,
            selection_fingerprint TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            artifact_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            receipt_id TEXT NOT NULL,
            cleanup_receipt_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_records_session_source "
        "ON crawl_records(session_id, source_kind, record_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_records_scope "
        "ON crawl_records(crawl_id, social_scope, record_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_records_hash "
        "ON crawl_records(content_sha256)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_artifacts_record "
        "ON crawl_artifacts(crawl_id, record_id, role)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_artifacts_hash "
        "ON crawl_artifacts(sha256)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_transfers_state "
        "ON crawl_transfers(state, updated_at)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_events_crawl "
        "ON crawl_events(crawl_id, id)"
    )


async def _migration_direct_crawl_composite_identity(
    conn: aiosqlite.Connection,
) -> None:
    cursor = await conn.execute("PRAGMA table_info(crawl_records)")
    primary_key = {
        row[1]: int(row[5])
        for row in await cursor.fetchall()
        if int(row[5]) > 0
    }
    if primary_key == {"crawl_id": 1, "record_id": 2}:
        return
    await conn.execute(
        """
        CREATE TABLE crawl_records_v2 (
            record_id TEXT NOT NULL,
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_app TEXT,
            social_scope TEXT,
            normalized_text TEXT,
            content_sha256 TEXT,
            selection_revision INTEGER NOT NULL,
            selection_fingerprint TEXT NOT NULL,
            canonical_json TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY(crawl_id, record_id),
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        """
        INSERT INTO crawl_records_v2 (
            record_id, crawl_id, session_id, source_kind, source_app,
            social_scope, normalized_text, content_sha256, selection_revision,
            selection_fingerprint, canonical_json, canonical_path, ingested_at
        )
        SELECT
            record_id, crawl_id, session_id, source_kind, source_app,
            social_scope, normalized_text, content_sha256, selection_revision,
            selection_fingerprint, canonical_json, canonical_path, ingested_at
        FROM crawl_records
        """
    )
    await conn.execute(
        """
        CREATE TABLE crawl_artifacts_v2 (
            artifact_id TEXT PRIMARY KEY,
            crawl_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            role TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            verified INTEGER NOT NULL,
            ingested_at TEXT NOT NULL,
            FOREIGN KEY(crawl_id) REFERENCES crawl_runs(crawl_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id),
            FOREIGN KEY(crawl_id, record_id)
                REFERENCES crawl_records_v2(crawl_id, record_id)
        )
        """
    )
    await conn.execute(
        """
        INSERT INTO crawl_artifacts_v2 (
            artifact_id, crawl_id, session_id, record_id, source_kind, role,
            mime_type, relative_path, size_bytes, sha256, verified, ingested_at
        )
        SELECT
            artifact_id, crawl_id, session_id, record_id, source_kind, role,
            mime_type, relative_path, size_bytes, sha256, verified, ingested_at
        FROM crawl_artifacts
        """
    )
    await conn.execute("DROP TABLE crawl_artifacts")
    await conn.execute("DROP TABLE crawl_records")
    await conn.execute("ALTER TABLE crawl_records_v2 RENAME TO crawl_records")
    await conn.execute("ALTER TABLE crawl_artifacts_v2 RENAME TO crawl_artifacts")
    await conn.execute(
        "CREATE INDEX idx_crawl_records_session_source "
        "ON crawl_records(session_id, source_kind, record_id)"
    )
    await conn.execute(
        "CREATE INDEX idx_crawl_records_scope "
        "ON crawl_records(crawl_id, social_scope, record_id)"
    )
    await conn.execute(
        "CREATE INDEX idx_crawl_records_hash ON crawl_records(content_sha256)"
    )
    await conn.execute(
        "CREATE INDEX idx_crawl_artifacts_record "
        "ON crawl_artifacts(crawl_id, record_id, role)"
    )
    await conn.execute(
        "CREATE INDEX idx_crawl_artifacts_hash ON crawl_artifacts(sha256)"
    )


async def _migration_social_snapshot_enrichment(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS social_snapshot_enrichments (
            crawl_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_app TEXT NOT NULL,
            social_scope TEXT NOT NULL,
            artifact_ids_json TEXT NOT NULL,
            debug_paths_json TEXT NOT NULL,
            ocr_text TEXT,
            ocr_backend TEXT,
            ocr_confidence REAL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(crawl_id, record_id),
            FOREIGN KEY(crawl_id, record_id)
                REFERENCES crawl_records(crawl_id, record_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_snapshot_session "
        "ON social_snapshot_enrichments(session_id, source_app, social_scope, record_id)"
    )


async def _migration_media_tickets(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_tickets (
            ticket_hash TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_tickets_expiry ON media_tickets(expires_at)"
    )


async def _migration_session_participant(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(sessions)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "participant_json" not in columns:
        await conn.execute(
            "ALTER TABLE sessions ADD COLUMN participant_json TEXT NOT NULL DEFAULT '{}'"
        )


async def _migration_finding_review_audit(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(findings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "reviewed_by" not in columns:
        await conn.execute("ALTER TABLE findings ADD COLUMN reviewed_by TEXT")
    if "reviewed_at" not in columns:
        await conn.execute("ALTER TABLE findings ADD COLUMN reviewed_at TEXT")


MIGRATIONS: tuple[tuple[int, str, MigrationHandler], ...] = (
    (1, "finding_media_dates", _migration_finding_media_dates),
    (2, "agent_runtimes", _migration_agent_runtimes),
    (3, "agent_bootstrap_trace", _migration_agent_bootstrap_trace),
    (4, "selection_review", _migration_selection_review),
    (5, "direct_crawl_ingestion", _migration_direct_crawl_ingestion),
    (6, "direct_crawl_composite_identity", _migration_direct_crawl_composite_identity),
    (7, "social_snapshot_enrichment", _migration_social_snapshot_enrichment),
    (8, "media_tickets", _migration_media_tickets),
    (9, "session_participant", _migration_session_participant),
    (10, "finding_review_audit", _migration_finding_review_audit),
)


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self._conn: aiosqlite.Connection | None = None
        self._operation_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self._conn is not None:
            raise RuntimeError("Database already connected")
        # A Database may be reopened by tests or application lifecycle hooks on
        # a fresh event loop. asyncio primitives cannot be reused across loops.
        self._operation_lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL
            )
            """
        )
        cursor = await self.conn.execute("SELECT version FROM schema_migrations")
        applied = {int(row[0]) for row in await cursor.fetchall()}
        try:
            for version, name, handler in MIGRATIONS:
                if version in applied:
                    continue
                await handler(self.conn)
                await self.conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, _utcnow()),
                )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._operation_lock:
            await self.conn.execute(sql, params)
            await self.conn.commit()

    async def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        async with self._operation_lock:
            await self.conn.executemany(sql, seq)
            await self.conn.commit()

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        async with self._operation_lock:
            await self.conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self._operation_lock:
            cur = await self.conn.execute(sql, params)
            try:
                return await cur.fetchone()
            finally:
                await cur.close()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self._operation_lock:
            cur = await self.conn.execute(sql, params)
            try:
                return await cur.fetchall()
            finally:
                await cur.close()


def _participant_from_row(row: aiosqlite.Row) -> dict[str, Any] | None:
    keys = set(row.keys())
    if "participant_json" not in keys:
        return None
    raw = row["participant_json"]
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    full_name = str(data.get("full_name") or "").strip()
    registration_no = str(data.get("registration_no") or "").strip()
    if not full_name and not registration_no:
        return None
    nik = str(data.get("nik") or "").strip() or None
    organization = str(data.get("organization") or "").strip() or None
    return {
        "full_name": full_name,
        "registration_no": registration_no,
        "nik": nik,
        "organization": organization,
    }


def row_to_session(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "device_type": row["device_type"],
        "label": row["label"],
        "mode": row["mode"],
        "scenario": row["scenario"],
        "status": row["status"],
        "progress": json.loads(row["progress_json"]),
        "timing": json.loads(row["timing_json"]),
        "recommendation": row["recommendation"],
        "review_candidates": bool(row["review_candidates"]),
        "participant": _participant_from_row(row),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


db = Database()
utcnow = _utcnow
