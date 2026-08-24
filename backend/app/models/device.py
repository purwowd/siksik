from __future__ import annotations

from pydantic import Field

from app.models.base import RequestModel, ResponseModel
from app.models.enums import AgentBootstrapState, DeviceType


class AgentBootstrapRequest(RequestModel):
    session_id: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


class DeviceInfo(ResponseModel):
    device_id: str
    device_type: DeviceType
    label: str
    os_version: str | None = None
    connected: bool = True
    simulated: bool = False
    agent_state: str | None = None
    agent_version: str | None = None
    agent_error_category: str | None = None
    manufacturer: str | None = None
    api_level: int | None = None
    unlocked: bool | None = None
    install_hint: str | None = None
    automation_state: str | None = None


class AgentBootstrapStatus(ResponseModel):
    schema_version: int = 1
    session_id: str
    device_ref: str
    state: AgentBootstrapState
    ready: bool
    api_version: str | None = None
    agent_version: str | None = None
    agent_build_sha256: str | None = None
    artifact_sha256: str | None = None
    install_action: str | None = None
    runtime_permissions: dict[str, str] = Field(default_factory=dict)
    special_access: dict[str, str] = Field(default_factory=dict)
    capabilities: dict | None = None
    retryable: bool = False
    error_category: str | None = None
    created_at: str
    updated_at: str
