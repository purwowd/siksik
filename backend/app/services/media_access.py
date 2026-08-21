from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.core.db import db, utcnow

TICKET_TTL_SECONDS = 600


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


async def issue_media_ticket(session_id: str, user_id: str, relative_path: str) -> tuple[str, str]:
    ticket = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=TICKET_TTL_SECONDS)).isoformat()
    await db.execute("DELETE FROM media_tickets WHERE expires_at <= ?", (now.isoformat(),))
    await db.execute(
        """
        INSERT INTO media_tickets (
            ticket_hash, session_id, user_id, relative_path, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (_digest(ticket), session_id, user_id, relative_path, expires_at, utcnow()),
    )
    return ticket, expires_at


async def validate_media_ticket(ticket: str, session_id: str, relative_path: str) -> bool:
    if len(ticket) < 32 or len(ticket) > 256:
        return False
    ticket_hash = _digest(ticket)
    row = await db.fetchone(
        """
        SELECT expires_at
        FROM media_tickets
        WHERE ticket_hash = ? AND session_id = ? AND relative_path = ?
        """,
        (ticket_hash, session_id, relative_path),
    )
    if row is None:
        return False
    try:
        expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        await db.execute("DELETE FROM media_tickets WHERE ticket_hash = ?", (ticket_hash,))
        return False
    return True
