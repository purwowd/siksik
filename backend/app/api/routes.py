from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pathlib import Path

from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import (
    AcquisitionMode,
    AuthorizeRequest,
    DashboardStats,
    DeviceInfo,
    FindingOut,
    HealthOut,
    LoginRequest,
    LoginResponse,
    MeResponse,
    NamedCount,
    PaginatedFindings,
    PaginatedSessions,
    ReviewRequest,
    ReviewStatus,
    RiskTimeline,
    SessionSummary,
    StartSessionRequest,
    YearRiskBucket,
)
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
)
from app.services.reports import build_session_report, report_to_html
from app.services.sessions import sessions
from app.services.vision import vision_status

router = APIRouter()


def _pages(total: int, page_size: int) -> int:
    if total <= 0:
        return 1
    return max(1, (total + page_size - 1) // page_size)


def _clamp_page(page: int, pages: int) -> int:
    return min(max(1, page), pages)


async def _paginate_findings(
    *,
    where_sql: str,
    params: tuple,
    order_sql: str,
    page: int,
    page_size: int,
) -> PaginatedFindings:
    total_row = await db.fetchone(f"SELECT COUNT(*) AS c FROM findings {where_sql}", params)
    total = int(total_row["c"]) if total_row else 0
    pages = _pages(total, page_size)
    page = _clamp_page(page, pages)
    offset = (page - 1) * page_size
    rows = await db.fetchall(
        f"SELECT * FROM findings {where_sql} {order_sql} LIMIT ? OFFSET ?",
        (*params, page_size, offset),
    )
    return PaginatedFindings(
        items=[FindingOut.model_validate(dict(r)) for r in rows],
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


@router.post("/sessions", response_model=SessionSummary)
async def start_session(
    body: StartSessionRequest,
    _: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
) -> SessionSummary:
    wants_sim = bool(body.force_simulated) or (body.device_id or "").startswith("sim-")
    if wants_sim and not settings.lab_demo_mode:
        raise HTTPException(
            status_code=403,
            detail="Mode lab/simulator dinonaktifkan. Sambungkan perangkat live atau set SADT_LAB_DEMO_MODE=1.",
        )
    try:
        data = await sessions.create_and_run(body)
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sessions/from-zip", response_model=SessionSummary)
async def start_session_from_zip(
    _: Annotated[AuthUser, Depends(require_perm("sessions:start"))],
    file: UploadFile = File(..., description="ZIP hasil adb pull / dump media"),
    mode: AcquisitionMode = Form(AcquisitionMode.QUICK),
    label: str | None = Form(None),
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
        data = await sessions.create_and_run_from_zip(
            zip_bytes=raw,
            original_name=name,
            mode=mode,
            label=label,
        )
        return SessionSummary.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
) -> PaginatedFindings:
    if review_status:
        return await _paginate_findings(
            where_sql="WHERE session_id = ? AND review_status = ?",
            params=(session_id, review_status.value),
            order_sql="ORDER BY confidence DESC",
            page=page,
            page_size=page_size,
        )
    return await _paginate_findings(
        where_sql="WHERE session_id = ?",
        params=(session_id,),
        order_sql="ORDER BY confidence DESC",
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/{session_id}/media")
async def session_media(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("findings:read"))],
    path: str = Query(..., min_length=1, max_length=1024, description="Relative path dalam staging"),
):
    """Serve image/video preview dari staging sesi (path traversal aman)."""
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    # Normalisasi relative path
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
    # Batasi jenis media untuk UI preview
    ext = target.suffix.lower()
    if ext not in {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
        ".3gp",
        ".avi",
    }:
        raise HTTPException(status_code=415, detail="Tipe media tidak didukung preview")
    return FileResponse(target, filename=target.name)


@router.get("/sessions/{session_id}/report")
async def session_report(
    session_id: str,
    _: Annotated[AuthUser, Depends(require_perm("report:read"))],
    format: str = Query("json", pattern="^(json|html)$"),
):
    try:
        report = await build_session_report(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if format == "html":
        return HTMLResponse(report_to_html(report))
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
    progress = json.loads(row["progress_json"])
    progress["authorized_by"] = user.username
    progress["authorized_at"] = utcnow()
    progress["authorize_note"] = body.note or ""
    await db.execute(
        "UPDATE sessions SET progress_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(progress), utcnow(), session_id),
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
            where_sql="WHERE session_id = ?",
            params=(session_id,),
            order_sql="ORDER BY created_at DESC",
            page=page,
            page_size=page_size,
        )
    return await _paginate_findings(
        where_sql="",
        params=(),
        order_sql="ORDER BY created_at DESC",
        page=page,
        page_size=page_size,
    )


@router.patch("/findings/{finding_id}", response_model=FindingOut)
async def review_finding(
    finding_id: str,
    body: ReviewRequest,
    _: Annotated[AuthUser, Depends(require_perm("findings:review"))],
) -> FindingOut:
    row = await db.fetchone("SELECT * FROM findings WHERE id = ?", (finding_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Finding not found")
    await db.execute(
        "UPDATE findings SET review_status = ? WHERE id = ?",
        (body.review_status.value, finding_id),
    )
    from app.services.recommendation import apply_recommendation

    await apply_recommendation(str(row["session_id"]))
    row = await db.fetchone("SELECT * FROM findings WHERE id = ?", (finding_id,))
    return FindingOut.model_validate(dict(row))


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
        WHERE status IN ('pending','detecting','acquiring','indexing','analyzing')
        """
    )
    findings = await db.fetchone("SELECT COUNT(*) AS c FROM findings")
    pending = await db.fetchone("SELECT COUNT(*) AS c FROM findings WHERE review_status = 'pending'")
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
