from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.models.base import RequestModel, ResponseModel
from app.models.enums import (
    AcquisitionMode,
    AnalysisScope,
    AgentBootstrapState,
    DeviceType,
    RecoveryState,
    Scenario,
    SessionStatus,
)


class ParticipantIdentity(ResponseModel):
    """Identitas peserta seleksi yang terikat ke sesi akuisisi."""

    full_name: str = ""
    registration_no: str = ""
    nik: str | None = None
    organization: str | None = None


class ParticipantInput(RequestModel):
    full_name: str = Field(min_length=1, max_length=128)
    registration_no: str = Field(min_length=1, max_length=64)
    nik: str | None = Field(default=None, max_length=32)
    organization: str | None = Field(default=None, max_length=128)

    @field_validator("full_name", "registration_no", "nik", "organization", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("full_name", "registration_no")
    @classmethod
    def require_non_empty(cls, value: Any) -> Any:
        if not value:
            raise ValueError("wajib diisi")
        return value

    @field_validator("nik")
    @classmethod
    def nik_must_be_16_digits(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not text.isdigit() or len(text) != 16:
            raise ValueError("NIK harus 16 digit angka")
        return text


class StartSessionRequest(RequestModel):
    device_id: str | None = Field(default=None, max_length=512)
    device_type: DeviceType = DeviceType.ANDROID
    mode: AcquisitionMode = AcquisitionMode.QUICK
    analysis_scope: AnalysisScope = AnalysisScope.DEVICE
    device_sources: list[str] = Field(default_factory=list)
    social_targets: list[str] = Field(default_factory=list)
    scenario: Scenario = Scenario.LULUS
    file_count: int = Field(default=1200, ge=1, le=1_000_000)
    label: str | None = Field(default=None, max_length=256)
    participant: ParticipantInput
    force_simulated: bool = False
    review_candidates: bool = False

    @field_validator("device_id", "label", mode="before")
    @classmethod
    def empty_string_as_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def analysis_plan(self):
        from app.acquisition.analysis_plan import build_analysis_plan

        return build_analysis_plan(
            scope=self.analysis_scope,
            device_sources=self.device_sources,
            social_targets=self.social_targets,
        )


class UpdateParticipantRequest(RequestModel):
    participant: ParticipantInput


class AuthorizeRequest(RequestModel):
    note: str | None = Field(default=None, max_length=4000)


class SessionProgress(ResponseModel):
    phase: SessionStatus = SessionStatus.PENDING
    percent: float = Field(default=0, ge=0, le=100)
    message: str = "Menunggu"
    files_listed: int = Field(default=0, ge=0)
    files_pulled: int = Field(default=0, ge=0)
    files_indexed: int = Field(default=0, ge=0)
    files_analyzed: int = Field(default=0, ge=0)
    findings_count: int = Field(default=0, ge=0)
    throughput_files_per_sec: float = Field(default=0, ge=0)
    live_analysis_ms: float = Field(default=0, ge=0)
    acquisition_method: str | None = None
    hits_l1: int = Field(default=0, ge=0)
    hits_l2: int = Field(default=0, ge=0)
    hits_l3: int = Field(default=0, ge=0)
    hits_l4: int = Field(default=0, ge=0)
    hits_ocr: int = Field(default=0, ge=0)
    hits_asr: int = Field(default=0, ge=0)
    authorized_by: str | None = None
    authorized_at: str | None = None
    authorize_note: str | None = None
    bootstrap_state: AgentBootstrapState | None = None
    agent_state: str | None = None
    agent_version: str | None = None
    agent_api_version: str | None = None
    agent_install_action: str | None = None
    agent_retryable: bool | None = None
    agent_error_category: str | None = None
    runtime_permissions: dict[str, str] | None = None
    special_access: dict[str, str] | None = None
    selection_state: str | None = None
    selection_revision: int | None = Field(default=None, ge=1)
    selection_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    selection_policy_version: str | None = None
    selection_policy_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    selection_candidates: int | None = Field(default=None, ge=0)
    selection_selected: int | None = Field(default=None, ge=0)
    recovery_state: RecoveryState | None = None
    recovery_mode: AcquisitionMode | None = None
    recovery_candidates: int | None = Field(default=None, ge=0)
    recovery_captured: int | None = Field(default=None, ge=0)
    recovery_bytes: int | None = Field(default=None, ge=0)
    recovery_warning_count: int | None = Field(default=None, ge=0)
    recovery_duration_ms: float | None = Field(default=None, ge=0)
    recovery_cache_sources: int | None = Field(default=None, ge=0)
    recovery_cache_captured: int | None = Field(default=None, ge=0)
    recovery_error_category: str | None = Field(default=None, max_length=64)
    ios_library_state: str | None = Field(default=None, max_length=32)
    ios_hidden_captured: int | None = Field(default=None, ge=0)
    ios_recently_deleted_captured: int | None = Field(default=None, ge=0)
    ios_cache_captured: int | None = Field(default=None, ge=0)
    ios_deleted_metadata_captured: int | None = Field(default=None, ge=0)
    ios_library_warning_count: int | None = Field(default=None, ge=0)
    analysis_scope: str | None = None
    device_sources: list[str] | None = None
    social_targets: list[str] | None = None
    report_sha256: str | None = None
    authorized_confirmed_findings: int | None = Field(default=None, ge=0)


class TimingBreakdown(ResponseModel):
    t_detect_ms: float = Field(default=0, ge=0)
    t_acquire_ms: float = Field(default=0, ge=0)
    t_inventory_ms: float = Field(default=0, ge=0)
    t_preprocess_ms: float = Field(default=0, ge=0)
    t_selection_ms: float = Field(default=0, ge=0)
    t_transfer_ms: float = Field(default=0, ge=0)
    t_index_ms: float = Field(default=0, ge=0)
    t_analyze_ms: float = Field(default=0, ge=0)
    t_total_ms: float = Field(default=0, ge=0)


class SessionSummary(ResponseModel):
    id: str
    device_id: str
    device_type: DeviceType
    label: str
    mode: AcquisitionMode
    scenario: Scenario
    status: SessionStatus
    progress: SessionProgress
    timing: TimingBreakdown
    participant: ParticipantIdentity | None = None
    recommendation: str | None = None
    review_candidates: bool = False
    created_at: str
    updated_at: str
    error: str | None = None


class PaginatedSessions(ResponseModel):
    items: list[SessionSummary] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
