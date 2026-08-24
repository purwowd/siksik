from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.db import db, utcnow
from app.models.schemas import (
    BulkReviewRequest,
    FindingOut,
    PaginatedFindings,
    ReviewRequest,
    ReviewStatus,
    RiskTimeline,
    YearRiskBucket,
)
from app.api.deps import paginate_findings
from app.services.auth import require_perm, AuthUser
from app.services.timeline import build_risk_timeline

router = APIRouter()

@router.get("/sessions/{session_id}/findings", response_model=PaginatedFindings)
async def session_findings(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    review_status: ReviewStatus | None = None,
    module: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedFindings:
    from app.services.finding_modules import MODULE_SOURCE_SQL, VALID_MODULE_IDS

    module_sql = ""
    module_params: tuple = ()
    if module:
        mid = module.strip().lower()
        if mid not in VALID_MODULE_IDS:
            raise HTTPException(status_code=400, detail=f"Modul tidak dikenal: {module}")
        module_sql, module_params = MODULE_SOURCE_SQL[mid]

    if review_status:
        return await paginate_findings(
            where_sql=f"WHERE f.session_id = ? AND f.review_status = ? {module_sql}",
            params=(session_id, review_status.value, *module_params),
            order_sql="ORDER BY f.confidence DESC",
            page=page,
            page_size=page_size,
        )
    return await paginate_findings(
        where_sql=f"WHERE f.session_id = ? {module_sql}",
        params=(session_id, *module_params),
        order_sql="ORDER BY f.confidence DESC",
        page=page,
        page_size=page_size,
    )


@router.get("/findings", response_model=PaginatedFindings)
async def all_findings(
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    session_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedFindings:
    if session_id:
        return await paginate_findings(
            where_sql="WHERE f.session_id = ?",
            params=(session_id,),
            order_sql="ORDER BY f.created_at DESC",
            page=page,
            page_size=page_size,
        )
    return await paginate_findings(
        where_sql="",
        params=(),
        order_sql="ORDER BY f.created_at DESC",
        page=page,
        page_size=page_size,
    )



@router.patch("/findings/{finding_id}", response_model=FindingOut)
async def review_finding(
    finding_id: str,
    body: ReviewRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:review"))],
) -> FindingOut:
    row = await db.fetchone("SELECT * FROM findings WHERE id = ?", (finding_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Finding not found")
    now = utcnow()
    await db.execute(
        """
        UPDATE findings
        SET review_status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
        """,
        (body.review_status.value, user.username, now, finding_id),
    )
    from app.services.recommendation import apply_recommendation

    await apply_recommendation(str(row["session_id"]))
    refreshed = await paginate_findings(
        where_sql="WHERE f.id = ?",
        params=(finding_id,),
        order_sql="ORDER BY f.created_at DESC",
        page=1,
        page_size=1,
    )
    if not refreshed.items:
        raise HTTPException(status_code=404, detail="Finding not found")
    return refreshed.items[0]



@router.post("/sessions/{session_id}/findings/bulk-review")
async def bulk_review_findings(
    session_id: str,
    body: BulkReviewRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:review"))],
) -> dict:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    now = utcnow()
    pending_row = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (session_id,),
    )
    pending_count = int(pending_row["c"]) if pending_row else 0
    if pending_count == 0:
        return {
            "session_id": session_id,
            "review_status": body.review_status.value,
            "updated": 0,
            "reviewed_by": user.username,
        }
    await db.execute(
        """
        UPDATE findings
        SET review_status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (body.review_status.value, user.username, now, session_id),
    )
    from app.services.recommendation import apply_recommendation

    await apply_recommendation(session_id)
    return {
        "session_id": session_id,
        "review_status": body.review_status.value,
        "updated": pending_count,
        "reviewed_by": user.username,
    }



@router.get("/sessions/{session_id}/risk-timeline", response_model=RiskTimeline)
async def session_risk_timeline(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    years_back: int = Query(5, ge=1, le=15),
) -> RiskTimeline:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    finding_rows = await db.fetchall(
        "SELECT media_year, category, review_status FROM findings WHERE session_id = ?",
        (session_id,),
    )
    data = build_risk_timeline([dict(r) for r in finding_rows], years_back=years_back)
    return RiskTimeline(
        years_back=data["years_back"],
        year_from=data["year_from"],
        year_to=data["year_to"],
        series=[YearRiskBucket(**s) for s in data["series"]],
        older_than_window=data["older_than_window"],
        unknown_date=data["unknown_date"],
        trend=data["trend"],
        insight=data["insight"],
        peak_year=data["peak_year"],
        peak_count=data["peak_count"],
        current_year_count=data["current_year_count"],
        prior_avg=data["prior_avg"],
    )


