from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agent,
    auth,
    dashboard,
    devices,
    findings,
    gallery,
    health,
    media,
    reports,
    selection,
    sessions,
)

router = APIRouter()
for group in (
    health.router,
    auth.router,
    devices.router,
    agent.router,
    sessions.router,
    selection.router,
    findings.router,
    gallery.router,
    media.router,
    reports.router,
    dashboard.router,
    admin.router,
):
    router.routes.extend(group.routes)

__all__ = ["router"]
