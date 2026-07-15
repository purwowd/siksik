"""Rekomendasi sesi — tiga status berdasarkan review temuan."""

from __future__ import annotations

from app.core.db import db, utcnow

REC_LULUS = "LULUS"
REC_TIDAK_LULUS = "TIDAK LULUS"
REC_MENUNGGU_REVIEW = "MENUNGGU REVIEW"


async def count_confirmed(session_id: str) -> int:
    row = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM findings
        WHERE session_id = ? AND review_status = 'confirmed'
        """,
        (session_id,),
    )
    return int(row["c"]) if row else 0


async def count_pending(session_id: str) -> int:
    row = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (session_id,),
    )
    return int(row["c"]) if row else 0


def recommendation_from_counts(*, confirmed: int, pending: int) -> str:
    if confirmed > 0:
        return REC_TIDAK_LULUS
    if pending > 0:
        return REC_MENUNGGU_REVIEW
    return REC_LULUS


def recommendation_from_confirmed(confirmed: int, pending: int = 0) -> str:
    return recommendation_from_counts(confirmed=confirmed, pending=pending)


async def compute_recommendation(session_id: str) -> str:
    confirmed = await count_confirmed(session_id)
    pending = await count_pending(session_id)
    return recommendation_from_counts(confirmed=confirmed, pending=pending)


async def apply_recommendation(session_id: str) -> str:
    """Hitung ulang & persist rekomendasi sesi. Dipanggil saat selesai analisa atau setelah review."""
    rec = await compute_recommendation(session_id)
    await db.execute(
        "UPDATE sessions SET recommendation = ?, updated_at = ? WHERE id = ?",
        (rec, utcnow(), session_id),
    )
    return rec


async def recompute_all_recommendations() -> dict:
    """Migrasi sekali jalan: hitung ulang rekomendasi semua sesi completed."""
    rows = await db.fetchall("SELECT id, recommendation FROM sessions WHERE status = 'completed'")
    updated: list[dict[str, str]] = []
    unchanged = 0
    for row in rows:
        sid = str(row["id"])
        old = row["recommendation"] or ""
        new = await apply_recommendation(sid)
        if new != old:
            updated.append({"session_id": sid, "from": old, "to": new})
        else:
            unchanged += 1
    return {
        "scanned": len(rows),
        "updated": len(updated),
        "unchanged": unchanged,
        "changes": updated,
    }
