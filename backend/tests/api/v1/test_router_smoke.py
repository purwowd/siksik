from app.api.v1.router import router


def test_v1_router_exposes_satria_and_main_paths():
    paths = {route.path for route in router.routes if hasattr(route, "path")}
    assert "/sessions/{session_id}/participant" in paths
    assert "/sessions/{session_id}/stream" in paths
    assert "/sessions/{session_id}/findings/bulk-review" in paths
    assert "/sessions/{session_id}/media-ticket" in paths
    assert "/toolchain" in paths
    assert "/agent/bootstrap" in paths
