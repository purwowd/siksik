from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeviceType(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    SIMULATED = "simulated"


class AcquisitionMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class Scenario(str, Enum):
    LULUS = "lulus"
    TIDAK_LULUS = "tidak_lulus"


class SessionStatus(str, Enum):
    PENDING = "pending"
    DETECTING = "detecting"
    PREPARING_AGENT = "preparing_agent"
    AWAITING_ACCESS = "awaiting_access"
    ACQUIRING = "acquiring"
    SELECTING = "selecting"
    AWAITING_REVIEW = "awaiting_review"
    INDEXING = "indexing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentBootstrapState(str, Enum):
    DETECT_DEVICE = "detect_device"
    VALIDATE_DEVICE = "validate_device"
    RESOLVE_OR_BUILD_AGENT = "resolve_or_build_agent"
    INSPECT_INSTALLED_PACKAGE = "inspect_installed_package"
    INSTALL_OR_UPDATE = "install_or_update"
    AWAITING_INSTALL_APPROVAL = "awaiting_install_approval"
    APPLY_RUNTIME_PERMISSIONS = "apply_runtime_permissions"
    AWAITING_RUNTIME_PERMISSION = "awaiting_runtime_permission"
    VERIFY_SPECIAL_ACCESS = "verify_special_access"
    AWAITING_ACCESS = "awaiting_access"
    START_AGENT = "start_agent"
    CREATE_FORWARD = "create_forward"
    AUTHENTICATE_AND_NEGOTIATE = "authenticate_and_negotiate"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Layer(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


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


class StartSessionRequest(RequestModel):
    device_id: str | None = Field(default=None, max_length=512)
    device_type: DeviceType = DeviceType.ANDROID
    mode: AcquisitionMode = AcquisitionMode.QUICK
    scenario: Scenario = Scenario.LULUS
    file_count: int = Field(default=1200, ge=1, le=1_000_000)
    label: str | None = Field(default=None, max_length=256)
    force_simulated: bool = False
    review_candidates: bool = False

    @field_validator("device_id", "label", mode="before")
    @classmethod
    def empty_string_as_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AgentBootstrapRequest(RequestModel):
    session_id: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


class ReviewRequest(RequestModel):
    review_status: ReviewStatus


class AuthorizeRequest(RequestModel):
    note: str | None = Field(default=None, max_length=4000)


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
    capabilities: dict[str, Any] | None = None
    retryable: bool = False
    error_category: str | None = None
    created_at: str
    updated_at: str


class TimingBreakdown(ResponseModel):
    t_detect_ms: float = Field(default=0, ge=0)
    t_acquire_ms: float = Field(default=0, ge=0)
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
    recommendation: str | None = None
    review_candidates: bool = False
    created_at: str
    updated_at: str
    error: str | None = None


class FindingOut(ResponseModel):
    id: str
    session_id: str
    file_id: str
    source: str
    path: str
    category: str
    label: str
    confidence: float = Field(ge=0, le=1)
    layer_origin: str
    evidence: str
    review_status: ReviewStatus
    created_at: str
    media_year: int | None = None
    media_captured_at: str | None = None
    preview_path: str | None = Field(default=None, max_length=1024)
    preview_text: str | None = Field(default=None, max_length=320)


class NamedCount(ResponseModel):
    name: str
    count: int = Field(ge=0)


class YearRiskBucket(ResponseModel):
    year: int
    total: int = Field(ge=0)
    by_category: list[NamedCount] = Field(default_factory=list)


class RiskTimeline(ResponseModel):
    years_back: int = Field(ge=1)
    year_from: int
    year_to: int
    series: list[YearRiskBucket] = Field(default_factory=list)
    older_than_window: int = Field(default=0, ge=0)
    unknown_date: int = Field(default=0, ge=0)
    trend: str
    insight: str
    peak_year: int | None = None
    peak_count: int = Field(default=0, ge=0)
    current_year_count: int = Field(default=0, ge=0)
    prior_avg: float = Field(default=0, ge=0)


class DashboardStats(ResponseModel):
    total_sessions: int = Field(default=0, ge=0)
    completed_sessions: int = Field(default=0, ge=0)
    active_sessions: int = Field(default=0, ge=0)
    failed_sessions: int = Field(default=0, ge=0)
    total_findings: int = Field(default=0, ge=0)
    pending_reviews: int = Field(default=0, ge=0)
    confirmed_findings: int = Field(default=0, ge=0)
    rejected_findings: int = Field(default=0, ge=0)
    lulus_count: int = Field(default=0, ge=0)
    tidak_lulus_count: int = Field(default=0, ge=0)
    menunggu_review_count: int = Field(default=0, ge=0)
    avg_total_ms: float = Field(default=0, ge=0)
    avg_acquire_ms: float = Field(default=0, ge=0)
    avg_analyze_ms: float = Field(default=0, ge=0)
    avg_index_ms: float = Field(default=0, ge=0)
    throughput_peak_fps: float = Field(default=0, ge=0)
    findings_by_category: list[NamedCount] = Field(default_factory=list)
    findings_by_layer: list[NamedCount] = Field(default_factory=list)
    findings_by_source: list[NamedCount] = Field(default_factory=list)
    acquisition_methods: list[NamedCount] = Field(default_factory=list)
    toolchain: dict[str, bool] = Field(default_factory=dict)
    gpu_available: bool = False
    risk_timeline: RiskTimeline | None = None
    timeline_session_id: str | None = None
    timeline_session_label: str | None = None


class HealthOut(ResponseModel):
    status: str
    app: str
    gpu_available: bool
    staging_dir: str
    db_path: str
    extras: dict[str, Any] = Field(default_factory=dict)


class PaginatedSessions(ResponseModel):
    items: list[SessionSummary] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)


class PaginatedFindings(ResponseModel):
    items: list[FindingOut] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
