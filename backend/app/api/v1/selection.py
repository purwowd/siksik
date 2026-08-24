from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from app.services.auth import require_perm, AuthUser
from app.selection.contracts import (
    CandidateConfirmRequest, CandidateConfirmationResponse, CandidateListResponse,
    CandidateMutationResponse, CandidateOverrideRequest, SelectionRunV1, SourceKind,
)
from app.selection.service import selection_review_service

router = APIRouter()

@router.get("/sessions/{session_id}/crawl", response_model=SelectionRunV1)
async def session_crawl_selection(
    session_id: str,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> SelectionRunV1:
    try:
        return await selection_review_service.crawl(session_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc



@router.get("/sessions/{session_id}/candidates", response_model=CandidateListResponse)
async def session_candidates(
    session_id: str,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    source_kind: SourceKind | None = Query(None),
    selected: bool | None = Query(None),
    minimum_score: float | None = Query(None, ge=0, le=1),
) -> CandidateListResponse:
    try:
        return await selection_review_service.list_candidates(
            session_id,
            user,
            page=page,
            page_size=page_size,
            source_kind=source_kind,
            selected=selected,
            minimum_score=minimum_score,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc



@router.patch(
    "/sessions/{session_id}/candidates/{record_id}",
    response_model=CandidateMutationResponse,
)
async def update_session_candidate(
    session_id: str,
    record_id: str,
    body: CandidateOverrideRequest,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> CandidateMutationResponse:
    try:
        return await selection_review_service.mutate_candidate(
            session_id,
            record_id,
            user,
            expected_revision=body.expected_revision,
            override=body.override,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection candidate not found") from exc



@router.post(
    "/sessions/{session_id}/candidates/confirm",
    response_model=CandidateConfirmationResponse,
)
async def confirm_session_candidates(
    session_id: str,
    body: CandidateConfirmRequest,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> CandidateConfirmationResponse:
    try:
        return await selection_review_service.confirm(
            session_id,
            user,
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc


