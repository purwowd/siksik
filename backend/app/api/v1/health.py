from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from app.core.branding import PRODUCT_NAME, PRODUCT_TAGLINE
from app.core.config import settings
from app.models.schemas import HealthOut
from app.api.deps import gpu_available, perms
from app.services.acquisition import toolchain_status
from app.services.auth import Role, require_perm, AuthUser
from app.services.vision import vision_status

router = APIRouter()

@router.get("/ready")
async def ready() -> dict:
    """Probe publik untuk shell desktop (tanpa auth)."""
    return {"status": "ok", "app": settings.app_name}


@router.get("/health", response_model=HealthOut)
async def health(user: Annotated[AuthUser, Depends(require_perm("health"))]) -> HealthOut:
    tools = await toolchain_status()
    extras: dict = {
        "product": PRODUCT_NAME,
        "tagline": PRODUCT_TAGLINE,
        "focus_scope": settings.focus_scope,
        "image_cap_quick": settings.image_cap_quick,
        "image_cap_full": settings.image_cap_full,
        "zip_enabled": settings.zip_enabled,
        "zip_max_mb": settings.zip_max_mb,
        "ocr_full_gallery": settings.ocr_full_gallery,
        "ocr_max_edge_px": settings.ocr_max_edge_px,
        "video_cap_quick": settings.video_cap_quick,
        "video_cap_full": settings.video_cap_full,
        "video_whisper_max_duration_s": settings.video_whisper_max_duration_s,
        "analysis_engine": __import__("app.services.hash_cache", fromlist=["engine_fingerprint"]).engine_fingerprint(),
        "worker_concurrency": settings.worker_concurrency,
        "lab_demo_mode": settings.lab_demo_mode,
        "runtime_env": settings.runtime_env,
        "toolchain": tools,
        "vision": vision_status(),
        "rbac": True,
    }
    # Path detail hanya untuk admin — kurangi info leak di konsol bersama
    if user.role == Role.ADMIN:
        staging = str(settings.staging_dir)
        db_path = str(settings.db_path)
    else:
        staging = "[redacted]"
        db_path = "[redacted]"
    return HealthOut(
        status="ok",
        app=settings.app_name,
        gpu_available=gpu_available(),
        staging_dir=staging,
        db_path=db_path,
        extras=extras,
    )



@router.get("/toolchain")
async def toolchain(_: Annotated[AuthUser, Depends(require_perm("health"))]) -> dict:
    tools = await toolchain_status()
    return {"toolchain": tools, "gpu_available": gpu_available()}


