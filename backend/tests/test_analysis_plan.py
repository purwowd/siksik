from __future__ import annotations

import pytest

from app.acquisition.analysis_plan import (
    DEVICE_SOURCE_ADAPTERS,
    SOCIAL_ADAPTERS,
    SOCIAL_TARGET_PACKAGES,
    analysis_plan_from_progress,
    build_analysis_plan,
    default_analysis_plan,
)
from app.models.enums import AnalysisScope
from app.models.session import ParticipantInput, StartSessionRequest


@pytest.mark.unit
def test_default_plan_is_combined_with_all_sources() -> None:
    plan = default_analysis_plan()
    assert plan.scope is AnalysisScope.COMBINED
    assert "gallery" in plan.device_sources
    assert "recovery" in plan.device_sources
    assert plan.social_targets == ("instagram", "facebook", "x")
    assert plan.includes_social is True
    assert plan.includes_recovery is True
    assert SOCIAL_ADAPTERS <= plan.inventory_adapters()


@pytest.mark.unit
def test_omitted_lists_fill_defaults() -> None:
    plan = build_analysis_plan()
    assert plan.scope is AnalysisScope.COMBINED
    assert plan.device_sources == default_analysis_plan().device_sources
    assert plan.social_targets == default_analysis_plan().social_targets


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
    assert plan.includes_gallery is False
    assert plan.includes_recovery is False
    assert plan.inventory_adapters() == SOCIAL_ADAPTERS
    assert plan.allows_file_source("visible_ui") is True
    assert plan.allows_file_source("gallery") is False
    assert plan.allows_file_source("email") is False


@pytest.mark.unit
def test_combined_subset_is_faster_than_full() -> None:
    slim = build_analysis_plan(
        scope="combined",
        device_sources=["gallery"],
        social_targets=["instagram"],
    )
    full = default_analysis_plan()
    assert slim.includes_social is True
    assert slim.includes_recovery is False
    assert DEVICE_SOURCE_ADAPTERS["gallery"] <= slim.inventory_adapters()
    assert SOCIAL_ADAPTERS <= slim.inventory_adapters()
    assert "contacts_content_provider" not in slim.inventory_adapters()
    assert len(slim.inventory_adapters()) < len(full.inventory_adapters())
    assert slim.social_packages == ("com.instagram.android",)
    assert slim.allows_file_source("whatsapp") is True
    assert slim.allows_file_source("contact") is False


@pytest.mark.unit
def test_progress_roundtrip_and_unknown_falls_back() -> None:
    plan = build_analysis_plan(scope="device", device_sources=["documents"])
    restored = analysis_plan_from_progress(plan.to_progress())
    assert restored == plan
    assert analysis_plan_from_progress({"analysis_scope": "nope"}).scope is AnalysisScope.COMBINED


@pytest.mark.unit
def test_start_session_request_defaults_to_device() -> None:
    req = StartSessionRequest(
        participant=ParticipantInput(full_name="Peserta Tes", registration_no="ASN-PLAN-002"),
    )
    assert req.analysis_scope is AnalysisScope.DEVICE
    plan = req.analysis_plan()
    assert plan.scope is AnalysisScope.DEVICE
    assert plan.includes_social is False
    assert plan.social_packages == ()
    assert "gallery" in plan.device_sources


@pytest.mark.unit
def test_start_session_request_builds_device_plan() -> None:
    req = StartSessionRequest(
        device_type="android",
        analysis_scope=AnalysisScope.DEVICE,
        device_sources=["gallery", "recovery"],
        social_targets=["x"],
        participant=ParticipantInput(full_name="Peserta Tes", registration_no="ASN-PLAN-001"),
    )
    plan = req.analysis_plan()
    assert plan.scope is AnalysisScope.DEVICE
    assert plan.includes_recovery is True
    assert plan.includes_social is False
    assert plan.social_packages == ()
