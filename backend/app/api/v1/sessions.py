from __future__ import annotations
import asyncio
import json
from typing import Annotated
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from app.core.db import db
from app.core.config import settings
from app.models.schemas import (
    AcquisitionMode,
    AnalysisScope,
    PaginatedSessions,
    ParticipantInput,
    SessionSummary,
    StartSessionRequest,
    UpdateParticipantRequest,
)
from app.acquisition.analysis_plan import build_analysis_plan
from app.api.deps import pages, clamp_page
from app.services.auth import require_perm, AuthUser
from app.services.sessions import sessions
from app.services.audit import list_session_audit, record_audit

router = APIRouter()


def _participant_from_form(
    *,
    full_name: str | None,
    registration_no: str | None,
    nik: str | None,
    organization: str | None,
) -> ParticipantInput:
    return ParticipantInput(
        full_name=(full_name or "").strip(),
        registration_no=(registration_no or "").strip(),
        nik=nik,
        organization=organization,
    )


def _csv_form_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.post("/sessions", response_model=SessionSummary)
async def start_session(
    body: StartSessionRequest,
    user: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
) -> SessionSummary:
    wants_sim = bool(body.force_simulated) or (body.device_id or "").startswith("sim-")
    if wants_sim and not settings.lab_demo_mode and not settings.e2e_simulation:
        raise HTTPException(
            status_code=403,
            detail="Mode lab/simulator dinonaktifkan. Sambungkan HP dengan kabel USB atau unggah arsip perangkat.",
        )
    try:
        data = await sessions.create_and_run(body, operator_id=user.id)
        await record_audit(
            session_id=data["id"],
            actor=user.username,
            action="session_started",
            detail=f"{body.analysis_scope.value} · {body.mode.value}",
        )
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/from-zip", response_model=SessionSummary)
async def start_session_from_zip(
    user: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
    file: UploadFile = File(..., description="ZIP arsip perangkat (galeri/dokumen)"),
    mode: AcquisitionMode = Form(AcquisitionMode.QUICK),
    label: str | None = Form(None),
    analysis_scope: AnalysisScope = Form(AnalysisScope.DEVICE),
    device_sources: str | None = Form(None),
    social_targets: str | None = Form(None),
    participant_full_name: str = Form(..., min_length=1),
    participant_registration_no: str = Form(..., min_length=1),
    participant_nik: str | None = Form(None),
    participant_organization: str | None = Form(None),
) -> SessionSummary:
    """Analisa arsip ZIP tanpa akuisisi USB (opsional)."""
    if not settings.zip_enabled:
        raise HTTPException(status_code=403, detail="Upload ZIP dinonaktifkan (SADT_ZIP_ENABLED=0)")
    name = file.filename or "upload.zip"
    if not name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File harus berformat .zip")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="ZIP kosong")
    max_b = settings.zip_max_mb * 1024 * 1024
    if len(raw) > max_b:
        raise HTTPException(status_code=413, detail=f"ZIP melebihi {settings.zip_max_mb} MB")
    try:
        participant = _participant_from_form(
            full_name=participant_full_name,
            registration_no=participant_registration_no,
            nik=participant_nik,
            organization=participant_organization,
        )
        data = await sessions.create_and_run_from_zip(
            zip_bytes=raw,
            original_name=name,
            mode=mode,
            label=label,
            participant=participant,
            operator_id=user.id,
            analysis_plan=build_analysis_plan(
                scope=analysis_scope,
                device_sources=_csv_form_list(device_sources),
                social_targets=_csv_form_list(social_targets),
            ),
        )
        await record_audit(
            session_id=data["id"],
            actor=user.username,
            action="session_started",
            detail=f"zip · {analysis_scope.value} · {mode.value}",
        )
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@router.patch("/sessions/{session_id}/participant", response_model=SessionSummary)
async def update_session_participant(
    session_id: str,
    body: UpdateParticipantRequest,
    _: Annotated[AuthUser, Depends(require_perm("sessions:update_participant"))],
) -> SessionSummary:
    try:
        data = await sessions.update_participant(session_id, body.participant)
        return SessionSummary.model_validate(data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sessions", response_model=PaginatedSessions)
async def list_sessions(
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedSessions:
    rows, total = await sessions.list_sessions_page(page, page_size)
    pages_total = pages(total, page_size)
    page = clamp_page(page, pages_total)
    return PaginatedSessions(
        items=[SessionSummary.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=pages_total,
    )



@router.get("/sessions/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
) -> SessionSummary:
    try:
        return SessionSummary.model_validate(await sessions.get(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc



@router.post("/sessions/{session_id}/cancel", response_model=SessionSummary)
async def cancel_session(
    session_id: str,
    user: Annotated[AuthUser, Depends(require_perm("sessions:cancel"))],
) -> SessionSummary:
    try:
        data = await sessions.cancel(session_id)
        await record_audit(
            session_id=session_id,
            actor=user.username,
            action="session_cancelled",
        )
        return SessionSummary.model_validate(data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/sessions/{session_id}/audit")
async def session_audit(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
) -> list[dict]:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return await list_session_audit(session_id)



@router.get("/sessions/{session_id}/stream")
async def session_stream(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
):
    """SSE stream of session progress — replaces aggressive polling when supported."""

    async def events():
        terminal = {"completed", "failed", "cancelled"}
        while True:
            try:
                raw = await sessions.get(session_id)
            except KeyError:
                yield "event: error\ndata: {\"detail\":\"Session not found\"}\n\n"
                return
            summary = SessionSummary.model_validate(raw)
            payload = summary.model_dump(mode="json")
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if summary.status in terminal:
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
