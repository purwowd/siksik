from __future__ import annotations

from pydantic import Field

from app.models.base import RequestModel, ResponseModel


class LoginRequest(RequestModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(ResponseModel):
    token: str
    username: str
    role: str
    display_name: str
    permissions: list[str] = Field(default_factory=list)


class MeResponse(ResponseModel):
    id: str
    username: str
    role: str
    display_name: str
    permissions: list[str] = Field(default_factory=list)
