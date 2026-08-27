from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.acquisition.errors import AcquisitionError
from app.api.v1.router import router
from app.core.config import ensure_dirs, settings
from app.core.db import db
from app.core.logging import configure_acquisition_logging
from app.core.request_context import bind_request_id, normalize_request_id, reset_request_id

request_logger = logging.getLogger("siksik.acquisition.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.services.auth import ensure_auth_schema

    configure_acquisition_logging()
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()
    yield
    from app.acquisition.bootstrap import agent_bootstrap

    await agent_bootstrap.shutdown()
    from app.acquisition.ios_setup import ios_setup

    await ios_setup.shutdown()
    await db.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

_cors_kwargs: dict = {
    "allow_origins": settings.cors_origins,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if (settings.cors_allow_origin_regex or "").strip():
    _cors_kwargs["allow_origin_regex"] = settings.cors_allow_origin_regex.strip()
app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    context_token = bind_request_id(request_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        reset_request_id(context_token)
    response.headers["X-Request-ID"] = request_id
    request_logger.info(
        "http_request_completed",
        extra={
            "request_id": request_id,
            "http_method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return response


@app.exception_handler(AcquisitionError)
async def acquisition_error_handler(request: Request, exc: AcquisitionError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", normalize_request_id(None))
    request_logger.warning(
        "acquisition_request_failed",
        extra={
            "request_id": request_id,
            "http_method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
            "error_category": exc.category.value,
            "retryable": exc.retryable,
            "dependency_exit_code": exc.dependency_exit_code,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.envelope(request_id),
        headers={"X-Request-ID": request_id},
    )

app.include_router(router, prefix=settings.api_prefix)


def _mount_desktop_ui() -> bool:
    if not settings.desktop_ui_enabled:
        return False
    dist = settings.desktop_ui_dist.resolve()
    if not dist.is_dir():
        request_logger.warning(
            "desktop_ui_dist_missing",
            extra={"desktop_ui_dist": str(dist)},
        )
        return False
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="desktop-ui")
    request_logger.info(
        "desktop_ui_mounted",
        extra={"desktop_ui_dist": str(dist)},
    )
    return True


if not _mount_desktop_ui():

    @app.get("/")
    async def root():
        return {
            "app": settings.app_name,
            "docs": "/docs",
            "api": settings.api_prefix,
        }
