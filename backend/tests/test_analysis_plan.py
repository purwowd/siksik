from __future__ import annotations

import json

import pytest

from app.acquisition.analysis_plan import (
    DEVICE_SOURCE_ADAPTERS,
    SOCIAL_ADAPTERS,
    SOCIAL_TARGET_PACKAGES,
    analysis_plan_from_progress,
    build_analysis_plan,
    default_analysis_plan,
)
from app.models.schemas import AnalysisScope, ParticipantInput, StartSessionRequest


@pytest.mark.unit
def test_default_plan_is_combined_with_all_current_sources() -> None:
    plan = default_analysis_plan()
    assert plan.scope is AnalysisScope.COMBINED
    assert {"gallery", "recovery", "email", "browser", "whatsapp"} <= set(
        plan.device_sources
    )
    assert plan.social_targets == ("instagram", "facebook", "x")
    assert plan.includes_social is True
    assert plan.includes_recovery is True
    assert SOCIAL_ADAPTERS <= plan.inventory_adapters()


@pytest.mark.unit
def test_omitted_lists_fill_combined_defaults() -> None:
    plan = build_analysis_plan()
    assert plan == default_analysis_plan()


@pytest.mark.unit
def test_device_scope_skips_social_and_accessibility() -> None:
    plan = build_analysis_plan(
        scope="device",
        device_sources=["gallery", "sms"],
        social_targets=["instagram", "facebook", "x"],
    )
    assert plan.scope is AnalysisScope.DEVICE
    assert plan.device_sources == ("gallery", "sms")
    assert plan.social_targets == ()
    assert plan.social_packages == ()
    assert plan.includes_social is False
    assert plan.includes_recovery is False
    assert "contacts_content_provider" not in plan.inventory_adapters()
    assert "sms_content_provider" in plan.inventory_adapters()
    assert SOCIAL_ADAPTERS.isdisjoint(plan.inventory_adapters())
    assert plan.allows_file_source("gallery") is True
    assert plan.allows_file_source("visible_ui") is False
    assert plan.allows_file_source("sms") is True
    assert plan.allows_file_source("recovered_cache") is False
    assert plan.allows_file_source("whatsapp") is False


@pytest.mark.unit
def test_social_scope_skips_phone_inventory() -> None:
    plan = build_analysis_plan(
        scope="social",
        device_sources=["gallery", "contacts"],
        social_targets=["instagram"],
    )
    assert plan.scope is AnalysisScope.SOCIAL
    assert plan.device_sources == ()
    assert plan.social_targets == ("instagram",)
    assert plan.social_packages == (SOCIAL_TARGET_PACKAGES["instagram"],)
    assert plan.inventory_adapters() == SOCIAL_ADAPTERS
    assert plan.allows_file_source("visible_ui") is True
    assert plan.allows_file_source("gallery") is False
    assert plan.allows_file_source("email") is False


@pytest.mark.unit
def test_combined_subset_keeps_only_requested_current_paths() -> None:
    slim = build_analysis_plan(
        scope="combined",
        device_sources=["gallery", "browser"],
        social_targets=["instagram"],
    )
    full = default_analysis_plan()
    assert slim.includes_social is True
    assert slim.includes_browser is True
    assert slim.includes_recovery is False
    assert DEVICE_SOURCE_ADAPTERS["gallery"] <= slim.inventory_adapters()
    assert SOCIAL_ADAPTERS <= slim.inventory_adapters()
    assert len(slim.inventory_adapters()) < len(full.inventory_adapters())
    assert slim.social_packages == ("com.instagram.android",)
    assert slim.allows_file_source("browser_history_full") is True
    assert slim.allows_file_source("whatsapp") is False
    assert slim.allows_file_source("contact") is False


@pytest.mark.unit
def test_progress_roundtrip_and_unknown_falls_back() -> None:
    plan = build_analysis_plan(scope="device", device_sources=["documents"])
    assert analysis_plan_from_progress(plan.to_progress()) == plan
    fallback = analysis_plan_from_progress({"analysis_scope": "nope"})
    assert fallback == default_analysis_plan()


@pytest.mark.unit
def test_legacy_start_request_defaults_to_combined() -> None:
    req = StartSessionRequest(
        participant=ParticipantInput(
            full_name="Peserta Tes",
            registration_no="ASN-PLAN-002",
        ),
    )
    assert req.analysis_scope is None
    plan = req.analysis_plan()
    assert plan.scope is AnalysisScope.COMBINED
    assert plan.includes_social is True
    assert plan.includes_email is True
    assert plan.includes_browser is True
    assert plan.includes_whatsapp is True


@pytest.mark.unit
def test_start_session_request_builds_device_plan() -> None:
    req = StartSessionRequest(
        device_type="android",
        analysis_scope=AnalysisScope.DEVICE,
        device_sources=["gallery", "recovery"],
        social_targets=["x"],
        participant=ParticipantInput(
            full_name="Peserta Tes",
            registration_no="ASN-PLAN-001",
        ),
    )
    plan = req.analysis_plan()
    assert plan.scope is AnalysisScope.DEVICE
    assert plan.includes_recovery is True
    assert plan.includes_social is False
    assert plan.social_packages == ()


@pytest.mark.asyncio
async def test_analysis_marks_out_of_scope_files_without_creating_findings(
    client,
    tmp_path,
) -> None:
    from app.core.db import db, utcnow
    from app.models.schemas import AcquisitionMode
    from app.services.acquisition import index_staging
    from app.services.analysis import analyze_session

    session_id = "session-analysis-scope-001"
    now = utcnow()
    progress = build_analysis_plan(
        scope="social",
        social_targets=["instagram"],
    ).to_progress()
    progress.update({"phase": "analyzing", "percent": 60, "message": "Analisis"})
    await db.execute(
        "INSERT INTO sessions (id, device_id, device_type, label, mode, scenario, "
        "status, progress_json, timing_json, recommendation, error, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            "android-test",
            "android",
            "Scope test",
            "quick",
            "lulus",
            "analyzing",
            json.dumps(progress),
            "{}",
            None,
            None,
            now,
            now,
        ),
    )
    staging = tmp_path / session_id
    email_dir = staging / "email"
    email_dir.mkdir(parents=True)
    (email_dir / "indikasi-makar.txt").write_text(
        "rencana makar dan serangan",
        encoding="utf-8",
    )

    async def no_progress(*_args, **_kwargs):
        return None

    await index_staging(session_id, staging, no_progress)
    analyzed, findings, *_ = await analyze_session(
        session_id,
        staging,
        AcquisitionMode.QUICK,
        no_progress,
    )

    assert analyzed == 1
    assert findings == 0
