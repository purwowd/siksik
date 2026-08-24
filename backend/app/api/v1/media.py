from __future__ import annotations
import mimetypes
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from app.models.schemas import MediaTicketOut, MediaTicketRequest
from app.api.deps import resolve_session_media
from app.services.auth import require_perm, user_from_token, AuthUser

router = APIRouter()

@router.post("/sessions/{session_id}/media-ticket", response_model=MediaTicketOut)
async def session_media_ticket(
    session_id: str,
    body: MediaTicketRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:read"))],
) -> MediaTicketOut:
    from app.services.media_access import issue_media_ticket

    rel, _, _ = await resolve_session_media(session_id, body.path)
    ticket, expires_at = await issue_media_ticket(session_id, user.id, rel)
    return MediaTicketOut(ticket=ticket, expires_at=expires_at)



@router.get("/sessions/{session_id}/media")
async def session_media(
    session_id: str,
    path: str = Query(..., min_length=1, max_length=1024),
    ticket: str | None = Query(default=None, min_length=32, max_length=256),
    authorization: Annotated[str | None, Header()] = None,
):
    from app.services.media_access import validate_media_ticket

    rel, target, indexed_mime = await resolve_session_media(session_id, path)
    authorized = bool(ticket) and await validate_media_ticket(ticket or "", session_id, rel)
    if not authorized:
        user = await user_from_token(authorization)
        if user is None:
            raise HTTPException(status_code=401, detail="Autentikasi diperlukan")
        user.require("findings:read")
    media_type = indexed_mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


