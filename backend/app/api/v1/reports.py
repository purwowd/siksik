from __future__ import annotations
import json
import logging
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.db import db, utcnow
from app.models.schemas import AuthorizeRequest
from app.services.auth import require_perm, AuthUser
from app.services import reports as rpt
from app.services.reports import build_session_report, report_to_html
from app.services.recommendation import REC_MENUNGGU_REVIEW

router = APIRouter()
logger = logging.getLogger("siksik.auth")


async def _pending_review_count(session_id: str) -> int:
    row = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (session_id,),
    )
    return int(row["c"]) if row else 0

@router.get("/sessions/{session_id}/report")
async def session_report(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("report:read"))],
    format: str = Query("json", pattern="^(json|html|print)$"),
):
    try:
        report = await build_session_report(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if format in ("html", "print"):
        html = report_to_html(report, print_mode=(format == "print"))
        return HTMLResponse(html)
    return JSONResponse(report)



@router.post("/sessions/{session_id}/authorize")
async def authorize_session(
    session_id: str,
    body: AuthorizeRequest,
    user: Annotated[AuthUser, Depends(require_perm("report:authorize"))],
) -> dict:
    row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail="Sesi belum selesai — pengesahan ditunda")
    pending = await _pending_review_count(session_id)
    if pending > 0 or row["recommendation"] == REC_MENUNGGU_REVIEW:
        raise HTTPException(
            status_code=403,
            detail="Pengesahan diblokir — masih ada temuan menunggu verifikasi analis",
        )
    progress = json.loads(row["progress_json"])
    progress["authorized_by"] = user.username
    progress["authorized_at"] = utcnow()
    progress["authorize_note"] = body.note or ""
    await db.execute(
        "UPDATE sessions SET progress_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(progress), utcnow(), session_id),
    )
    logger.info(
        "session_authorized session_id=%s by=%s",
        session_id,
        user.username,
    )
    try:
        await rpt.save_session_report(session_id)
    except Exception:
        logger.exception("save_session_report_failed session_id=%s", session_id)
    return {
        "status": "authorized",
        "session_id": session_id,
        "authorized_by": user.username,
        "recommendation": row["recommendation"],
    }


