from __future__ import annotations

from typing import Any

from pydantic import Field

from app.models.base import ResponseModel


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
    social_traces: list[NamedCount] = Field(default_factory=list)
    contact_unique: int = Field(default=0, ge=0)
    contact_records: int = Field(default=0, ge=0)


class HealthOut(ResponseModel):
    status: str
    app: str
    gpu_available: bool
    staging_dir: str
    db_path: str
    extras: dict[str, Any] = Field(default_factory=dict)
