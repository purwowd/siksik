from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from app.models.schemas import LoginRequest, LoginResponse, MeResponse
from app.api.deps import perms
from app.services.auth import PERMISSIONS, Role, ensure_auth_schema, list_users_safe, login, logout, require_perm, AuthUser

router = APIRouter()

@router.post("/auth/login", response_model=LoginResponse)
async def auth_login(body: LoginRequest, request: Request) -> LoginResponse:
    await ensure_auth_schema()
    user = await login(body.username, body.password, request=request)
    return LoginResponse(
        token=user.token or "",
        username=user.username,
        role=user.role.value,
        display_name=user.display_name,
        permissions=perms(user),
    )



@router.post("/auth/logout")
async def auth_logout(user: Annotated[AuthUser, Depends(require_perm("health"))]) -> dict:
    if user.token:
        await logout(user.token)
    return {"status": "ok"}



@router.get("/auth/me", response_model=MeResponse)
async def auth_me(user: Annotated[AuthUser, Depends(require_perm("health"))]) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        display_name=user.display_name,
        permissions=perms(user),
    )



@router.get("/auth/users")
async def auth_users(_: Annotated[AuthUser, Depends(require_perm("users:manage"))]) -> list[dict]:
    return await list_users_safe()



@router.get("/auth/roles")
async def auth_roles() -> dict:
    """Publik: katalog peran (tanpa kredensial)."""
    catalog = []
    labels = {
        Role.OPERATOR: "Operator Akuisisi",
        Role.ANALIS: "Analis Forensik",
        Role.PIMPINAN: "Pimpinan Panitia",
        Role.ADMIN: "Administrator",
    }
    for role, perms in PERMISSIONS.items():
        catalog.append(
            {
                "role": role.value,
                "label": labels.get(role, role.value),
                "permissions": sorted(perms),
            }
        )
    return {"roles": catalog}


