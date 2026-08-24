from fastapi.routing import APIRoute

from app.api.routes import router as source_router
from app.api.v1.router import router as v1_router


def _signatures(router) -> set[tuple[str, tuple[str, ...], str]]:
    return {
        (route.path, tuple(sorted(route.methods or ())), route.name)
        for route in router.routes
        if isinstance(route, APIRoute)
    }


def test_v1_router_preserves_all_main_routes_once() -> None:
    source = _signatures(source_router)
    modular = _signatures(v1_router)
    assert modular == source
    assert len(modular) == len(
        [route for route in v1_router.routes if isinstance(route, APIRoute)]
    )
