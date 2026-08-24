from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from app.models.schemas import GalleryAlbumOut, PaginatedGallery
from app.api.deps import session_mode
from app.services.auth import require_perm, AuthUser

router = APIRouter()

@router.get("/sessions/{session_id}/gallery/albums", response_model=list[GalleryAlbumOut])
async def session_gallery_albums(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
) -> list[GalleryAlbumOut]:
    from app.services import gallery as gallery_mod

    mode = await session_mode(session_id)
    return await gallery_mod.list_albums(session_id, mode)



@router.get("/sessions/{session_id}/gallery", response_model=PaginatedGallery)
async def session_gallery(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    album: str = Query(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=48),
) -> PaginatedGallery:
    from app.services import gallery as gallery_mod

    mode = await session_mode(session_id)
    return await gallery_mod.list_items(session_id, mode, album, page, page_size)

