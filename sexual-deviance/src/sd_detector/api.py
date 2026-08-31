"""FastAPI HTTP wrapper untuk SD Detector."""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import load_config
from .detector import ContentDetector
from .media import is_video
from .modes import DetectionMode
from .schema import Action

CONFIG_PATH = os.environ.get("SD_CONFIG", "config.yaml")
DEFAULT_MODE = os.environ.get("SD_MODE")
MAX_UPLOAD_MB = int(os.environ.get("SD_MAX_UPLOAD_MB", "50"))

_detector: Optional[ContentDetector] = None
_lock = threading.Lock()

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class HealthResponse(BaseModel):
    status: str
    mode: str
    llama_sidecar: str
    cache_enabled: bool


class ErrorResponse(BaseModel):
    detail: str


def _get_detector() -> ContentDetector:
    if _detector is None:
        raise RuntimeError("Detector not initialized")
    return _detector


def _parse_mode(value: Optional[str]) -> Optional[DetectionMode]:
    if not value:
        return None
    try:
        return DetectionMode(value.lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{value}'. Use: fast, balanced, full",
        ) from exc


def _check_llama_sidecar() -> str:
    det = _get_detector()
    if det.mode == DetectionMode.FAST:
        return "skipped (fast mode)"
    cfg = det.config.llama
    url = f"http://{cfg.host}:{cfg.port}/health"
    try:
        r = httpx.get(url, timeout=2.0)
        return "ok" if r.status_code == 200 else f"error ({r.status_code})"
    except httpx.HTTPError as exc:
        return f"unreachable ({exc.__class__.__name__})"


def _guess_media_type(filename: str, content_type: Optional[str]) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if content_type:
        if content_type.startswith("video/"):
            return "video"
        if content_type.startswith("image/"):
            return "image"
    return "image"


def _run_with_mode(mode: Optional[DetectionMode], fn):
    with _lock:
        det = _get_detector()
        prev = det.mode
        if mode is not None:
            det.set_mode(mode)
        try:
            return fn()
        finally:
            if mode is not None and prev != mode:
                det.set_mode(prev)


def _result_response(result, include_frames: bool) -> JSONResponse:
    data = result.to_dict()
    if not include_frames:
        data.pop("frames", None)

    status = 200
    if result.action == Action.BLOCK:
        status = 403
    elif result.action == Action.REVIEW:
        status = 422

    return JSONResponse(
        content=data,
        status_code=status,
        headers={"X-SD-Action": result.action.value},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _detector
    mode = _parse_mode(DEFAULT_MODE) if DEFAULT_MODE else None
    _detector = ContentDetector(config_path=CONFIG_PATH, mode=mode)
    _detector.start()
    yield
    _detector.stop()
    _detector = None


app = FastAPI(
    title="SD Detector API",
    description="Deteksi konten seksual dari gambar/video — NudeNet + SmolVLM (llama.cpp sidecar)",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    det = _get_detector()
    return HealthResponse(
        status="ok",
        mode=det.mode.value,
        llama_sidecar=_check_llama_sidecar(),
        cache_enabled=det.config.detector.cache.enabled,
    )


@app.get("/metrics", tags=["ops"])
def metrics() -> dict:
    det = _get_detector()
    snap = det.metrics.snapshot()
    if det.config.detector.cache.enabled:
        snap["cache"] = det._get_cache().stats()
    return snap


@app.post(
    "/v1/analyze",
    responses={
        200: {"description": "allow"},
        403: {"description": "block"},
        422: {"description": "review"},
        408: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
    tags=["detect"],
)
async def analyze_upload(
    file: UploadFile = File(..., description="Gambar atau video"),
    mode: Optional[str] = Query(None, description="fast | balanced | full"),
    include_frames: bool = Query(False, description="Sertakan detail per-frame (video)"),
) -> JSONResponse:
    parsed_mode = _parse_mode(mode)
    data = await file.read()
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_MB} MB limit")

    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    filename = file.filename or "upload"
    media_type = _guess_media_type(filename, file.content_type)
    source = f"upload:{filename}"

    try:
        if media_type == "video":
            suffix = Path(filename).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                result = _run_with_mode(
                    parsed_mode,
                    lambda: _get_detector().analyze_video(tmp_path),
                )
                result.verdict.path = source
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            result = _run_with_mode(
                parsed_mode,
                lambda: _get_detector().analyze_bytes(data, source=source),
            )
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Analysis failed: {exc}") from exc

    return _result_response(result, include_frames)


@app.post("/v1/analyze/image", tags=["detect"])
async def analyze_image(
    file: UploadFile = File(...),
    mode: Optional[str] = Query(None),
    include_frames: bool = Query(False),
) -> JSONResponse:
    if _guess_media_type(file.filename or "", file.content_type) == "video":
        raise HTTPException(status_code=400, detail="Expected image, got video")
    return await analyze_upload(file=file, mode=mode, include_frames=include_frames)


@app.post("/v1/analyze/video", tags=["detect"])
async def analyze_video(
    file: UploadFile = File(...),
    mode: Optional[str] = Query(None),
    include_frames: bool = Query(True),
) -> JSONResponse:
    if _guess_media_type(file.filename or "", file.content_type) != "video":
        suffix = Path(file.filename or "").suffix.lower()
        if not is_video(f"x{suffix}"):
            raise HTTPException(status_code=400, detail="Expected video file")
    return await analyze_upload(file=file, mode=mode, include_frames=include_frames)


def main() -> None:
    import uvicorn

    host = os.environ.get("SD_API_HOST", "0.0.0.0")
    port = int(os.environ.get("SD_API_PORT", "8000"))
    workers = int(os.environ.get("SD_API_WORKERS", "1"))
    uvicorn.run(
        "sd_detector.api:app",
        host=host,
        port=port,
        workers=workers,
        log_level=os.environ.get("SD_API_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
