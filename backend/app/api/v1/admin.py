from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from app.services.auth import require_perm, AuthUser

router = APIRouter()

@router.post("/admin/clear-hash-cache")
async def clear_hash_cache_endpoint(
    _: Annotated[AuthUser, Depends(require_perm("users:manage"))],
) -> dict:
    """Invalidate cache enrichment — wajib setelah pasang/nyalakan OCR atau Whisper."""
    from app.services.hash_cache import clear_hash_cache, engine_fingerprint

    n = await clear_hash_cache()
    return {"cleared": n, "engine": engine_fingerprint()}



@router.post("/admin/recompute-recommendations")
async def recompute_recommendations_endpoint(
    _: Annotated[AuthUser, Depends(require_perm("users:manage"))],
) -> dict:
    """Hitung ulang LULUS / MENUNGGU REVIEW / TIDAK LULUS untuk semua sesi completed."""
    from app.services.recommendation import recompute_all_recommendations

    return await recompute_all_recommendations()


