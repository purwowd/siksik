from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.api.routes import router as source_router


def route_group(*names: str) -> APIRouter:
    expected = set(names)
    routes = [
        route
        for route in source_router.routes
        if isinstance(route, APIRoute) and route.name in expected
    ]
    found = {route.name for route in routes}
    if found != expected or len(routes) != len(names):
        raise RuntimeError(
            f"Router v1 tidak sinkron: expected={sorted(expected)}, found={sorted(found)}"
        )
    router = APIRouter()
    router.routes.extend(routes)
    return router
