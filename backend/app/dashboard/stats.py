"""Dashboard aggregate queries — extracted from HTTP layer."""

from __future__ import annotations

import json

from app.acquisition.source_app_hints import SOCIAL_PACKAGE_LABELS
from app.api.deps import counts, gpu_available
from app.core.db import db
from app.models.schemas import DashboardStats, NamedCount, RiskTimeline, YearRiskBucket
from app.services.acquisition import toolchain_status
from app.services.participant import session_focus_label
from app.services.timeline import build_risk_timeline


async def build_dashboard_stats(session_id: str | None = None) -> DashboardStats:
    total = await db.fetchone("SELECT COUNT(*) AS c FROM sessions")
    completed = await db.fetchone("SELECT COUNT(*) AS c FROM sessions WHERE status = 'completed'")
    failed = await db.fetchone("SELECT COUNT(*) AS c FROM sessions WHERE status = 'failed'")
    active = await db.fetchone(
        """
        SELECT COUNT(*) AS c FROM sessions
        WHERE status IN (
            'pending','detecting','preparing_agent','awaiting_access',
            'acquiring','selecting','awaiting_review','indexing','analyzing'
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
    by_cat = counts([dict(r) for r in finding_rows], "category")
    by_layer = counts([dict(r) for r in finding_rows], "layer_origin")
    by_source = counts([dict(r) for r in finding_rows], "source")

    n = max(len(totals), 1)
    tools = await toolchain_status()

    timeline: RiskTimeline | None = None
    tl_sid: str | None = None
    tl_label: str | None = None
    focus = session_id
    if not focus:
        latest = await db.fetchone(
            """
            SELECT s.id, s.label, s.participant_json FROM sessions s
            WHERE s.status = 'completed'
            ORDER BY s.updated_at DESC LIMIT 1
            """
        )
        if latest:
            focus = latest["id"]
            tl_label = session_focus_label(latest["label"], latest["participant_json"])
    if focus:
        srow = await db.fetchone(
            "SELECT id, label, participant_json FROM sessions WHERE id = ?",
            (focus,),
        )
        if srow:
            tl_sid = srow["id"]
            tl_label = session_focus_label(srow["label"], srow["participant_json"])
            frows = await db.fetchall(
                """
                SELECT media_year, category, review_status
                FROM findings WHERE session_id = ?
                """,
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

    social_traces: list[NamedCount] = []
    contact_unique = 0
    contact_records = 0
    if session_id:
        file_rows = await db.fetchall(
            "SELECT source, meta_json FROM files WHERE session_id = ?",
            (session_id,),
        )
        app_counts: dict[str, int] = {}
        for row in file_rows:
            source = str(row["source"] or "").casefold()
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                meta = {}
            if source == "contact":
                contact_records += 1
                if not meta.get("contact_duplicate"):
                    contact_unique += 1
            app = meta.get("source_app")
            if not isinstance(app, str) or app not in SOCIAL_PACKAGE_LABELS:
                continue
            if meta.get("acquisition_self_capture"):
                continue
            if str(meta.get("crawl_artifact_role") or "") == "canonical_record":
                continue
            label = SOCIAL_PACKAGE_LABELS[app]
            app_counts[label] = app_counts.get(label, 0) + 1
        if contact_unique == 0 and contact_records:
            contact_unique = contact_records
        social_traces = [
            NamedCount(name=name, count=count)
            for name, count in sorted(app_counts.items())
        ]

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
        gpu_available=gpu_available(),
        risk_timeline=timeline,
        timeline_session_id=tl_sid,
        timeline_session_label=tl_label,
        social_traces=social_traces,
        contact_unique=contact_unique,
        contact_records=contact_records,
    )
