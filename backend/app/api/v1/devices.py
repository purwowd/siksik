from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from app.core.config import settings
from app.models.schemas import DeviceInfo
from app.services.acquisition import detect_devices
from app.services.auth import require_perm, AuthUser

router = APIRouter()

@router.get("/devices", response_model=list[DeviceInfo])
async def list_devices(_: Annotated[AuthUser, Depends(require_perm("devices"))]) -> list[DeviceInfo]:
    return await detect_devices(include_simulators=settings.lab_demo_mode)


