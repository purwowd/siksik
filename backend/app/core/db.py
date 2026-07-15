from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        """Additive migrations for existing DBs."""
        cols = await self.fetchall("PRAGMA table_info(findings)")
        names = {r["name"] for r in cols}
        if "media_year" not in names:
            await self.execute("ALTER TABLE findings ADD COLUMN media_year INTEGER")
        if "media_captured_at" not in names:
            await self.execute("ALTER TABLE findings ADD COLUMN media_captured_at TEXT")

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
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        await self.conn.executemany(sql, seq)
        await self.conn.commit()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchall()


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
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


db = Database()
utcnow = _utcnow
