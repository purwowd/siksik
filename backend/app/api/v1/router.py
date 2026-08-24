from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, agent, auth, dashboard, devices, findings, gallery, health, media, reports, selection, sessions

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(auth.router, tags=["auth"])
router.include_router(devices.router, tags=["devices"])
router.include_router(agent.router, tags=["agent"])
router.include_router(sessions.router, tags=["sessions"])
router.include_router(selection.router, tags=["selection"])
router.include_router(findings.router, tags=["findings"])
router.include_router(gallery.router, tags=["gallery"])
router.include_router(media.router, tags=["media"])
router.include_router(reports.router, tags=["reports"])
router.include_router(dashboard.router, tags=["dashboard"])
router.include_router(admin.router, tags=["admin"])
