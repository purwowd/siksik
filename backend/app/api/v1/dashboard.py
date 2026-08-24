from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from app.models.schemas import DashboardStats
from app.dashboard.stats import build_dashboard_stats
from app.services.auth import require_perm, AuthUser

router = APIRouter()

@router.get("/dashboard", response_model=DashboardStats)
async def dashboard(
    _: Annotated[AuthUser, Depends(require_perm("dashboard"))],
    session_id: str | None = Query(None, description="Fokus timeline risiko ke sesi ini"),
) -> DashboardStats:
    return await build_dashboard_stats(session_id)

