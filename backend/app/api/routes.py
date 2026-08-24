from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.branding import PRODUCT_NAME, PRODUCT_TAGLINE
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import (
    AcquisitionMode,
    AgentBootstrapRequest,
    AgentBootstrapStatus,
    AuthorizeRequest,
    BulkReviewRequest,
    DashboardStats,
    DeviceInfo,
    FindingOut,
    GalleryAlbumOut,
    HealthOut,
    LoginRequest,
    LoginResponse,
    MediaTicketOut,
    MediaTicketRequest,
    MeResponse,
    NamedCount,
    PaginatedFindings,
    PaginatedGallery,
    PaginatedSessions,
    ParticipantInput,
    ReviewRequest,
    ReviewStatus,
    RiskTimeline,
    SessionSummary,
    StartSessionRequest,
    UpdateParticipantRequest,
    YearRiskBucket,
)
from app.core.request_context import current_request_id
from app.services.timeline import build_risk_timeline
from app.services.acquisition import detect_devices, toolchain_status
from app.services.auth import (
    PERMISSIONS,
    Role,
    AuthUser,
    ensure_auth_schema,
    list_users_safe,
    login,
    logout,
    require_perm,
    user_from_token,
)
from app.services.reports import build_session_report, report_to_html, save_session_report
from app.services.recommendation import REC_MENUNGGU_REVIEW
from app.services.participant import require_complete_participant
from app.services.sessions import sessions
from app.services.vision import vision_status
from app.selection.contracts import (
    CandidateConfirmRequest,
    CandidateConfirmationResponse,
    CandidateListResponse,
    CandidateMutationResponse,
    CandidateOverrideRequest,
    SelectionRunV1,
    SourceKind,
)
from app.selection.service import selection_review_service

router = APIRouter()
audit_logger = logging.getLogger("siksik.auth")
MAX_FINDING_PREVIEW_CHARS = 320
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".3gp",
    ".avi",
    ".m4v",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".ogg",
    ".opus",
    ".flac",
    ".amr",
    ".html",
    ".htm",
    ".json",
    ".eml",
    ".msg",
    ".txt",
    ".csv",
    ".xml",
    ".log",
    ".vcf",
    ".vcard",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ods",
    ".ppt",
    ".pptx",
    ".odt",
    ".rtf",
    ".pages",
    ".numbers",
    ".key",
}
MEDIA_APPLICATION_MIMES = {
    "application/json",
    "application/pdf",
    "application/rtf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.apple.pages",
    "application/vnd.apple.numbers",
    "application/vnd.apple.keynote",
    "application/vnd.siksik.crawl-record+json",
}


def _pages(total: int, page_size: int) -> int:
    if total <= 0:
        return 1
    return max(1, (total + page_size - 1) // page_size)


def _clamp_page(page: int, pages: int) -> int:
    return min(max(1, page), pages)


FINDING_DEDUP_PREDICATE = """
AND f.id IN (
  SELECT ranked.id FROM (
    SELECT
      f2.id AS id,
      ROW_NUMBER() OVER (
        PARTITION BY COALESCE(NULLIF(fi2.sha256, ''), f2.file_id), f2.label
        ORDER BY f2.confidence DESC, f2.created_at ASC, f2.id ASC
      ) AS rn
    FROM findings f2
    LEFT JOIN files fi2 ON fi2.id = f2.file_id
    WHERE f2.session_id = f.session_id
  ) ranked
  WHERE ranked.rn = 1
)
"""


async def _paginate_findings(
    *,
    where_sql: str,
    params: tuple,
    order_sql: str,
    page: int,
    page_size: int,
) -> PaginatedFindings:
    total_row = await db.fetchone(
        f"SELECT COUNT(*) AS c FROM findings f {where_sql} {FINDING_DEDUP_PREDICATE}",
        params,
    )
    total = int(total_row["c"]) if total_row else 0
    pages = _pages(total, page_size)
    page = _clamp_page(page, pages)
    offset = (page - 1) * page_size
    rows = await db.fetchall(
        f"""
        SELECT
            f.*,
            CASE
                WHEN fi.mime LIKE 'image/%' OR fi.mime LIKE 'video/%' THEN f.path
                ELSE (
                    SELECT ca.relative_path
                    FROM crawl_artifacts ca
                    WHERE ca.session_id = f.session_id
                      AND ca.record_id = CASE
                          WHEN json_valid(fi.meta_json)
                          THEN json_extract(fi.meta_json, '$.crawl_record_id')
                          ELSE NULL
                      END
                      AND ca.verified = 1
                      AND ca.role IN ('source_binary', 'screenshot')
                      AND (ca.mime_type LIKE 'image/%' OR ca.mime_type LIKE 'video/%')
                    ORDER BY CASE ca.role WHEN 'source_binary' THEN 0 ELSE 1 END,
                             ca.relative_path
                    LIMIT 1
                )
            END AS resolved_preview_path,
            (
                SELECT cr.normalized_text
                FROM crawl_records cr
                WHERE cr.session_id = f.session_id
                  AND cr.record_id = CASE
                      WHEN json_valid(fi.meta_json)
                      THEN json_extract(fi.meta_json, '$.crawl_record_id')
                      ELSE NULL
                  END
                LIMIT 1
            ) AS normalized_preview_text
        FROM findings f
        LEFT JOIN files fi ON fi.id = f.file_id
        {where_sql} {FINDING_DEDUP_PREDICATE} {order_sql} LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    )
    items: list[FindingOut] = []
    for row in rows:
        payload = dict(row)
        preview_path = payload.pop("resolved_preview_path", None)
        normalized_text = payload.pop("normalized_preview_text", None)
        preview_source = normalized_text or payload.get("evidence") or ""
        preview_text = " ".join(
            str(preview_source).replace("\x00", " ").split(),
        )[:MAX_FINDING_PREVIEW_CHARS]
        payload["preview_path"] = preview_path
        payload["preview_text"] = preview_text or None
        items.append(FindingOut.model_validate(payload))
    return PaginatedFindings(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


def _gpu_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _counts(rows: list, key: str) -> list[NamedCount]:
    bucket: dict[str, int] = {}
    for r in rows:
        name = r[key] if isinstance(r, dict) else r[key]
        bucket[name] = bucket.get(name, 0) + 1
    return [NamedCount(name=k, count=v) for k, v in sorted(bucket.items(), key=lambda x: -x[1])]


def _perms(user: AuthUser) -> list[str]:
    return sorted(PERMISSIONS.get(user.role, set()))


@router.get("/ready")
async def ready() -> dict:
    return {"status": "ok", "app": settings.app_name}


@router.get("/health", response_model=HealthOut)
async def health(user: Annotated[AuthUser, Depends(require_perm("health"))]) -> HealthOut:
    tools = await toolchain_status()
    extras: dict = {
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
        "product": PRODUCT_NAME,
        "tagline": PRODUCT_TAGLINE,
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
        gpu_available=_gpu_available(),
        staging_dir=staging,
        db_path=db_path,
        extras=extras,
    )


@router.post("/auth/login", response_model=LoginResponse)
async def auth_login(body: LoginRequest, request: Request) -> LoginResponse:
    await ensure_auth_schema()
    user = await login(body.username, body.password, request=request)
    return LoginResponse(
        token=user.token or "",
        username=user.username,
        role=user.role.value,
        display_name=user.display_name,
        permissions=_perms(user),
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
        permissions=_perms(user),
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


@router.get("/devices", response_model=list[DeviceInfo])
async def list_devices(_: Annotated[AuthUser, Depends(require_perm("devices"))]) -> list[DeviceInfo]:
    return await detect_devices(include_simulators=settings.lab_demo_mode)


@router.get("/toolchain")
async def toolchain(_: Annotated[AuthUser, Depends(require_perm("health"))]) -> dict:
    tools = await toolchain_status()
    return {"toolchain": tools, "gpu_available": _gpu_available()}


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


@router.post("/sessions", response_model=SessionSummary)
async def start_session(
    body: StartSessionRequest,
    user: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
) -> SessionSummary:
    wants_sim = bool(body.force_simulated) or (body.device_id or "").startswith("sim-")
    if wants_sim and not settings.lab_demo_mode and not settings.e2e_simulation:
        raise HTTPException(
            status_code=403,
            detail="Mode lab/simulator dinonaktifkan. Sambungkan perangkat live atau set SADT_LAB_DEMO_MODE=1.",
        )
    try:
        if not wants_sim:
            require_complete_participant(body.participant)
        data = await sessions.create_and_run(body, operator_id=user.id)
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/from-zip", response_model=SessionSummary)
async def start_session_from_zip(
    user: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
    file: UploadFile = File(..., description="ZIP hasil adb pull / dump media"),
    mode: AcquisitionMode = Form(AcquisitionMode.QUICK),
    label: str | None = Form(None),
    participant_full_name: str = Form(""),
    participant_registration_no: str = Form(""),
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
        participant = ParticipantInput(
            full_name=participant_full_name,
            registration_no=participant_registration_no,
            nik=participant_nik,
            organization=participant_organization,
        )
        if not settings.lab_demo_mode and not settings.e2e_simulation:
            require_complete_participant(participant)
        data = await sessions.create_and_run_from_zip(
            zip_bytes=raw,
            original_name=name,
            mode=mode,
            label=label,
            participant=participant,
            operator_id=user.id,
        )
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sessions", response_model=PaginatedSessions)
async def list_sessions(
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedSessions:
    rows, total = await sessions.list_sessions_page(page, page_size)
    pages = _pages(total, page_size)
    page = _clamp_page(page, pages)
    return PaginatedSessions(
        items=[SessionSummary.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
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


@router.get("/sessions/{session_id}/stream")
async def session_stream(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("sessions:read"))],
):
    async def events():
        terminal = {"completed", "failed", "cancelled"}
        while True:
            try:
                raw = await sessions.get(session_id)
            except KeyError:
                yield 'event: error\ndata: {"detail":"Session not found"}\n\n'
                return
            summary = SessionSummary.model_validate(raw)
            yield f"data: {json.dumps(summary.model_dump(mode='json'), ensure_ascii=False)}\n\n"
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


@router.get("/sessions/{session_id}/crawl", response_model=SelectionRunV1)
async def session_crawl_selection(
    session_id: str,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> SelectionRunV1:
    try:
        return await selection_review_service.crawl(session_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc


@router.get("/sessions/{session_id}/candidates", response_model=CandidateListResponse)
async def session_candidates(
    session_id: str,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    source_kind: SourceKind | None = Query(None),
    selected: bool | None = Query(None),
    minimum_score: float | None = Query(None, ge=0, le=1),
) -> CandidateListResponse:
    try:
        return await selection_review_service.list_candidates(
            session_id,
            user,
            page=page,
            page_size=page_size,
            source_kind=source_kind,
            selected=selected,
            minimum_score=minimum_score,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc


@router.patch(
    "/sessions/{session_id}/candidates/{record_id}",
    response_model=CandidateMutationResponse,
)
async def update_session_candidate(
    session_id: str,
    record_id: str,
    body: CandidateOverrideRequest,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> CandidateMutationResponse:
    try:
        return await selection_review_service.mutate_candidate(
            session_id,
            record_id,
            user,
            expected_revision=body.expected_revision,
            override=body.override,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection candidate not found") from exc


@router.post(
    "/sessions/{session_id}/candidates/confirm",
    response_model=CandidateConfirmationResponse,
)
async def confirm_session_candidates(
    session_id: str,
    body: CandidateConfirmRequest,
    user: Annotated[AuthUser, Depends(require_perm("candidates:review"))],
) -> CandidateConfirmationResponse:
    try:
        return await selection_review_service.confirm(
            session_id,
            user,
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Selection crawl not found") from exc


@router.post("/sessions/{session_id}/cancel", response_model=SessionSummary)
async def cancel_session(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("sessions:cancel"))],
) -> SessionSummary:
    try:
        return SessionSummary.model_validate(await sessions.cancel(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/sessions/{session_id}/findings", response_model=PaginatedFindings)
async def session_findings(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    review_status: ReviewStatus | None = None,
    module: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedFindings:
    from app.services.finding_modules import MODULE_SOURCE_SQL, VALID_MODULE_IDS

    module_sql = ""
    module_params: tuple = ()
    if module:
        mid = module.strip().lower()
        if mid not in VALID_MODULE_IDS:
            raise HTTPException(status_code=400, detail=f"Modul tidak dikenal: {module}")
        module_sql, module_params = MODULE_SOURCE_SQL[mid]
    if review_status:
        return await _paginate_findings(
            where_sql=f"WHERE f.session_id = ? AND f.review_status = ? {module_sql}",
            params=(session_id, review_status.value, *module_params),
            order_sql="ORDER BY f.confidence DESC",
            page=page,
            page_size=page_size,
        )
    return await _paginate_findings(
        where_sql=f"WHERE f.session_id = ? {module_sql}",
        params=(session_id, *module_params),
        order_sql="ORDER BY f.confidence DESC",
        page=page,
        page_size=page_size,
    )


async def _session_mode(session_id: str) -> AcquisitionMode:
    row = await db.fetchone("SELECT mode FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return AcquisitionMode(str(row["mode"]))


@router.get("/sessions/{session_id}/gallery/albums", response_model=list[GalleryAlbumOut])
async def session_gallery_albums(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
) -> list[GalleryAlbumOut]:
    from app.services import gallery as gallery_mod

    mode = await _session_mode(session_id)
    return await gallery_mod.list_albums(session_id, mode)


@router.get("/sessions/{session_id}/gallery", response_model=PaginatedGallery)
async def session_gallery(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    album: str = Query(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=48),
) -> PaginatedGallery:
    from app.services import gallery as gallery_mod

    mode = await _session_mode(session_id)
    return await gallery_mod.list_items(session_id, mode, album, page, page_size)


async def _resolve_session_media(session_id: str, path: str) -> tuple[str, Path, str | None]:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    rel = path.replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Invalid path")
    staging = (settings.staging_dir / session_id).resolve()
    target = (staging / rel).resolve()
    try:
        target.relative_to(staging)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path di luar staging") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    file_row = await db.fetchone(
        "SELECT mime FROM files WHERE session_id = ? AND path = ? AND pull_status = 'pulled' LIMIT 1",
        (session_id, rel),
    )
    indexed_mime = str(file_row["mime"] or "").casefold() if file_row else ""
    mime_allowed = indexed_mime.startswith(("image/", "video/", "audio/", "text/")) or (
        indexed_mime in MEDIA_APPLICATION_MIMES
    )
    if not mime_allowed:
        from app.acquisition.android_recovery.service import (
            detect_recovery_mime_type,
            recovery_metadata,
        )

        def recovery_mime() -> str | None:
            recovery_artifact = recovery_metadata(staging).get(rel)
            if recovery_artifact is None:
                return None
            return detect_recovery_mime_type(
                target,
                recovery_artifact.mime_type,
            )

        resolved_recovery_mime = recovery_mime()
        if resolved_recovery_mime is not None:
            indexed_mime = resolved_recovery_mime
            mime_allowed = indexed_mime.startswith(
                ("image/", "video/", "audio/", "text/")
            ) or indexed_mime in MEDIA_APPLICATION_MIMES
    if target.suffix.lower() not in MEDIA_EXTENSIONS and not mime_allowed:
        raise HTTPException(status_code=415, detail="Tipe media tidak didukung preview")
    return rel, target, indexed_mime or None


@router.post("/sessions/{session_id}/media-ticket", response_model=MediaTicketOut)
async def session_media_ticket(
    session_id: str,
    body: MediaTicketRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:read"))],
) -> MediaTicketOut:
    from app.services.media_access import issue_media_ticket

    rel, _, _ = await _resolve_session_media(session_id, body.path)
    ticket, expires_at = await issue_media_ticket(session_id, user.id, rel)
    return MediaTicketOut(ticket=ticket, expires_at=expires_at)


@router.get("/sessions/{session_id}/media")
async def session_media(
    session_id: str,
    path: str = Query(..., min_length=1, max_length=1024),
    ticket: str | None = Query(default=None, min_length=32, max_length=256),
    authorization: Annotated[str | None, Header()] = None,
):
    from app.services.media_access import validate_media_ticket

    rel, target, indexed_mime = await _resolve_session_media(session_id, path)
    authorized = bool(ticket) and await validate_media_ticket(ticket or "", session_id, rel)
    if not authorized:
        user = await user_from_token(authorization)
        if user is None:
            raise HTTPException(status_code=401, detail="Autentikasi diperlukan")
        user.require("findings:read")
    media_type = indexed_mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@router.get("/sessions/{session_id}/report")
async def session_report(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("report:read"))],
    format: str = Query("json", pattern="^(json|html|print)$"),
):
    try:
        report = await build_session_report(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if format in ("html", "print"):
        return HTMLResponse(report_to_html(report, print_mode=(format == "print")))
    return JSONResponse(report)


@router.post("/sessions/{session_id}/authorize")
async def authorize_session(
    session_id: str,
    body: AuthorizeRequest,
    user: Annotated[AuthUser, Depends(require_perm("report:authorize"))],
) -> dict:
    row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail="Sesi belum selesai — pengesahan ditunda")
    pending_row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM findings WHERE session_id = ? AND review_status = 'pending'",
        (session_id,),
    )
    pending = int(pending_row["c"]) if pending_row else 0
    if pending > 0 or row["recommendation"] == REC_MENUNGGU_REVIEW:
        raise HTTPException(
            status_code=403,
            detail="Pengesahan diblokir — masih ada temuan menunggu verifikasi analis",
        )
    progress = json.loads(row["progress_json"])
    progress["authorized_by"] = user.username
    progress["authorized_at"] = utcnow()
    progress["authorize_note"] = body.note or ""
    await db.execute(
        "UPDATE sessions SET progress_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(progress), utcnow(), session_id),
    )
    audit_logger.info(
        "session_authorized",
        extra={"session_id": session_id, "actor": user.username},
    )
    try:
        await save_session_report(session_id)
    except Exception:
        audit_logger.exception(
            "session_report_save_failed",
            extra={"session_id": session_id, "actor": user.username},
        )
    return {
        "status": "authorized",
        "session_id": session_id,
        "authorized_by": user.username,
        "recommendation": row["recommendation"],
    }


@router.get("/findings", response_model=PaginatedFindings)
async def all_findings(
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    session_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedFindings:
    if session_id:
        return await _paginate_findings(
            where_sql="WHERE f.session_id = ?",
            params=(session_id,),
            order_sql="ORDER BY f.created_at DESC",
            page=page,
            page_size=page_size,
        )
    return await _paginate_findings(
        where_sql="",
        params=(),
        order_sql="ORDER BY f.created_at DESC",
        page=page,
        page_size=page_size,
    )


@router.patch("/findings/{finding_id}", response_model=FindingOut)
async def review_finding(
    finding_id: str,
    body: ReviewRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:review"))],
) -> FindingOut:
    row = await db.fetchone("SELECT * FROM findings WHERE id = ?", (finding_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Finding not found")
    now = utcnow()
    await db.execute(
        """
        UPDATE findings
        SET review_status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
        """,
        (body.review_status.value, user.username, now, finding_id),
    )
    from app.services.recommendation import apply_recommendation

    await apply_recommendation(str(row["session_id"]))
    refreshed = await _paginate_findings(
        where_sql="WHERE f.id = ?",
        params=(finding_id,),
        order_sql="ORDER BY f.created_at DESC",
        page=1,
        page_size=1,
    )
    if not refreshed.items:
        raise HTTPException(status_code=404, detail="Finding not found")
    return refreshed.items[0]


@router.post("/sessions/{session_id}/findings/bulk-review")
async def bulk_review_findings(
    session_id: str,
    body: BulkReviewRequest,
    user: Annotated[AuthUser, Depends(require_perm("findings:review"))],
) -> dict:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    now = utcnow()
    pending_row = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (session_id,),
    )
    pending_count = int(pending_row["c"]) if pending_row else 0
    if pending_count == 0:
        return {
            "session_id": session_id,
            "review_status": body.review_status.value,
            "updated": 0,
            "reviewed_by": user.username,
        }
    await db.execute(
        """
        UPDATE findings
        SET review_status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE session_id = ? AND review_status = 'pending'
        """,
        (body.review_status.value, user.username, now, session_id),
    )
    from app.services.recommendation import apply_recommendation

    await apply_recommendation(session_id)
    return {
        "session_id": session_id,
        "review_status": body.review_status.value,
        "updated": pending_count,
        "reviewed_by": user.username,
    }


@router.get("/sessions/{session_id}/risk-timeline", response_model=RiskTimeline)
async def session_risk_timeline(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    years_back: int = Query(5, ge=1, le=15),
) -> RiskTimeline:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    finding_rows = await db.fetchall(
        "SELECT media_year, category, review_status FROM findings WHERE session_id = ?",
        (session_id,),
    )
    data = build_risk_timeline([dict(r) for r in finding_rows], years_back=years_back)
    return RiskTimeline(
        years_back=data["years_back"],
        year_from=data["year_from"],
        year_to=data["year_to"],
        series=[YearRiskBucket(**s) for s in data["series"]],
        older_than_window=data["older_than_window"],
        unknown_date=data["unknown_date"],
        trend=data["trend"],
        insight=data["insight"],
        peak_year=data["peak_year"],
        peak_count=data["peak_count"],
        current_year_count=data["current_year_count"],
        prior_avg=data["prior_avg"],
    )


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


@router.get("/dashboard", response_model=DashboardStats)
async def dashboard(
    _: Annotated[AuthUser, Depends(require_perm("dashboard"))],
    session_id: str | None = Query(None, description="Fokus timeline risiko ke sesi ini"),
) -> DashboardStats:
    total = await db.fetchone("SELECT COUNT(*) AS c FROM sessions")
    completed = await db.fetchone("SELECT COUNT(*) AS c FROM sessions WHERE status = 'completed'")
    failed = await db.fetchone("SELECT COUNT(*) AS c FROM sessions WHERE status = 'failed'")
    active = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM sessions
        WHERE status IN (
            'pending','detecting','preparing_agent','awaiting_access',
            'acquiring','indexing','analyzing'
        )
        """
    )
    findings = await db.fetchone("SELECT COUNT(*) AS c FROM findings")
    if session_id:
        pending = await db.fetchone(
            "SELECT COUNT(*) AS c FROM findings WHERE review_status = 'pending' AND session_id = ?",
            (session_id,),
        )
    else:
        pending = await db.fetchone(
            "SELECT COUNT(*) AS c FROM findings WHERE review_status = 'pending'"
        )
    confirmed = await db.fetchone(
        "SELECT COUNT(*) AS c FROM findings WHERE review_status = 'confirmed'"
    )
    rejected = await db.fetchone(
        "SELECT COUNT(*) AS c FROM findings WHERE review_status = 'rejected'"
    )
    lulus = await db.fetchone("SELECT COUNT(*) AS c FROM sessions WHERE recommendation = 'LULUS'")
    tidak = await db.fetchone(
        "SELECT COUNT(*) AS c FROM sessions WHERE recommendation = 'TIDAK LULUS'"
    )
    menunggu = await db.fetchone(
        "SELECT COUNT(*) AS c FROM sessions WHERE recommendation = 'MENUNGGU REVIEW'"
    )

    timing_rows = await db.fetchall(
        "SELECT timing_json, progress_json FROM sessions WHERE status = 'completed'"
    )
    totals: list[float] = []
    acqs: list[float] = []
    anas: list[float] = []
    idxs: list[float] = []
    peak = 0.0
    methods: dict[str, int] = {}
    for r in timing_rows:
        t = json.loads(r["timing_json"])
        p = json.loads(r["progress_json"])
        totals.append(t.get("t_total_ms", 0))
        acqs.append(t.get("t_acquire_ms", 0))
        anas.append(t.get("t_analyze_ms", 0))
        idxs.append(t.get("t_index_ms", 0))
        peak = max(peak, float(p.get("throughput_files_per_sec") or 0))
        m = p.get("acquisition_method") or "unknown"
        methods[m] = methods.get(m, 0) + 1

    finding_rows = await db.fetchall("SELECT category, layer_origin, source FROM findings")
    by_cat = _counts([dict(r) for r in finding_rows], "category")
    by_layer = _counts([dict(r) for r in finding_rows], "layer_origin")
    by_source = _counts([dict(r) for r in finding_rows], "source")

    n = max(len(totals), 1)
    tools = await toolchain_status()

    # Timeline 5 tahun — prefer session_id query, else sesi completed terbaru yang punya findings
    timeline: RiskTimeline | None = None
    tl_sid: str | None = None
    tl_label: str | None = None
    focus = session_id
    if not focus:
        latest = await db.fetchone(
            """
            SELECT s.id, s.label FROM sessions s
            WHERE s.status = 'completed'
            ORDER BY s.updated_at DESC LIMIT 1
            """
        )
        if latest:
            focus = latest["id"]
            tl_label = latest["label"]
    if focus:
        srow = await db.fetchone("SELECT id, label FROM sessions WHERE id = ?", (focus,))
        if srow:
            tl_sid = srow["id"]
            tl_label = srow["label"]
            frows = await db.fetchall(
                "SELECT media_year, category FROM findings WHERE session_id = ?",
                (focus,),
            )
            data = build_risk_timeline([dict(r) for r in frows], years_back=5)
            timeline = RiskTimeline(
                years_back=data["years_back"],
                year_from=data["year_from"],
                year_to=data["year_to"],
                series=[YearRiskBucket(**s) for s in data["series"]],
                older_than_window=data["older_than_window"],
                unknown_date=data["unknown_date"],
                trend=data["trend"],
                insight=data["insight"],
                peak_year=data["peak_year"],
                peak_count=data["peak_count"],
                current_year_count=data["current_year_count"],
                prior_avg=data["prior_avg"],
            )

    return DashboardStats(
        total_sessions=total["c"] if total else 0,
        completed_sessions=completed["c"] if completed else 0,
        active_sessions=active["c"] if active else 0,
        failed_sessions=failed["c"] if failed else 0,
        total_findings=findings["c"] if findings else 0,
        pending_reviews=pending["c"] if pending else 0,
        confirmed_findings=confirmed["c"] if confirmed else 0,
        rejected_findings=rejected["c"] if rejected else 0,
        lulus_count=lulus["c"] if lulus else 0,
        tidak_lulus_count=tidak["c"] if tidak else 0,
        menunggu_review_count=menunggu["c"] if menunggu else 0,
        avg_total_ms=round(sum(totals) / n, 1) if totals else 0,
        avg_acquire_ms=round(sum(acqs) / n, 1) if acqs else 0,
        avg_analyze_ms=round(sum(anas) / n, 1) if anas else 0,
        avg_index_ms=round(sum(idxs) / n, 1) if idxs else 0,
        throughput_peak_fps=peak,
        findings_by_category=by_cat,
        findings_by_layer=by_layer,
        findings_by_source=by_source,
        acquisition_methods=[NamedCount(name=k, count=v) for k, v in methods.items()],
        toolchain=tools,
        gpu_available=_gpu_available(),
        risk_timeline=timeline,
        timeline_session_id=tl_sid,
        timeline_session_label=tl_label,
    )
