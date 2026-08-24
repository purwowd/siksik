from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from app.models.schemas import AgentBootstrapRequest, AgentBootstrapStatus
from app.core.request_context import current_request_id
from app.services.auth import require_perm, AuthUser
from app.services.sessions import sessions

router = APIRouter()

@router.post("/agent/bootstrap", response_model=AgentBootstrapStatus)
async def bootstrap_android_agent(
    body: AgentBootstrapRequest,
    _: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
) -> AgentBootstrapStatus:
    try:
        record = await sessions.retry_agent_bootstrap(body.session_id, body.device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.acquisition.bootstrap import agent_bootstrap

    return AgentBootstrapStatus.model_validate(agent_bootstrap.public_status(record))



@router.get("/agent/status", response_model=AgentBootstrapStatus)
async def android_agent_status(
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
    device_id: str = Query(..., min_length=1, max_length=128),
) -> AgentBootstrapStatus:
    from app.acquisition.bootstrap import agent_bootstrap

    record = await agent_bootstrap.status_for_device(device_id, current_request_id())
    return AgentBootstrapStatus.model_validate(agent_bootstrap.public_status(record))


