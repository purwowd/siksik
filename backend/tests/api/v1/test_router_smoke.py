"""Smoke tests for API v1 router structure."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")


def test_v1_subrouters_registered():
    from app.api.v1 import admin, agent, auth, dashboard, devices, findings, gallery, health, media, reports, selection, sessions
    from app.api.v1.router import router

    assert len(router.routes) >= 10
    assert health.router.routes
    assert auth.router.routes
    assert sessions.router.routes
    assert dashboard.router.routes


def test_v1_domain_paths_present():
    from app.api.v1 import auth, dashboard, health, sessions

    health_paths = {getattr(r, "path", "") for r in health.router.routes}
    auth_paths = {getattr(r, "path", "") for r in auth.router.routes}
    session_paths = {getattr(r, "path", "") for r in sessions.router.routes}
    dash_paths = {getattr(r, "path", "") for r in dashboard.router.routes}

    assert "/health" in health_paths
    assert "/auth/login" in auth_paths
    assert "/sessions" in session_paths
    assert "/dashboard" in dash_paths


def test_legacy_routes_reexport():
    from app.api import routes

    assert routes.router is not None
