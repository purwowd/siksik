"""Append-only audit trail for workstation sessions."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.db import db, utcnow


async def record_audit(
    *,
    action: str,
    actor: str,
    session_id: str | None = None,
    detail: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO audit_events (id, session_id, actor, action, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            session_id,
            (actor or "sistem").strip() or "sistem",
            action.strip(),
            (detail or "").strip() or None,
            utcnow(),
        ),
    )


async def list_session_audit(session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        """
        SELECT id, session_id, actor, action, detail, created_at
        FROM audit_events
        WHERE session_id = ?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (session_id, max(1, min(limit, 500))),
    )
    return [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "actor": row["actor"],
            "action": row["action"],
            "detail": row["detail"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
