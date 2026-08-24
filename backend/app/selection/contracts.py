from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.branding import session_id_field
from app.models.schemas import RequestModel, ResponseModel

SourceKind = Literal[
    "media_image",
    "media_video",
    "media_audio",
    "document",
    "sms",
    "contact",
    "visible_ui",
    "notification",
]
SelectionState = Literal[
    "running",
    "awaiting_review",
    "confirmed",
    "cancelled",
    "failed",
]
HumanOverride = Literal["none", "include", "exclude"]


class StrictSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class KeywordPolicyV1(StrictSelectionModel):
    keyword: str = Field(min_length=1, max_length=128)
    category: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    match_terms: list[str] = Field(min_length=1, max_length=16)
    weight_basis_points: int = Field(ge=0, le=10_000)

    @field_validator("match_terms")
    @classmethod
    def unique_normalized_terms(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            not term
            or len(term) > 128
            or term != re.sub(r"\s+", " ", term.lower()).strip()
            for term in value
        ):
            raise ValueError("keyword match terms are invalid")
        return value


class SelectionPolicyV1(StrictSelectionModel):
    schema_version: Literal[1]
    policy_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    keywords: list[KeywordPolicyV1] = Field(min_length=1, max_length=256)
    source_weights_basis_points: dict[SourceKind, int] = Field(min_length=8, max_length=8)
    text_signal_weights_basis_points: dict[
        Literal["ocr", "document_text", "sms", "visible_ui", "notification"],
        int,
    ] = Field(min_length=5, max_length=5)
    face_weight_basis_points: int = Field(ge=0, le=10_000)
    object_label_weights_basis_points: dict[str, int] = Field(max_length=64)
    required_social_scopes: list[
        Literal[
            "own_profile",
            "own_posts",
            "own_tweets",
            "own_story_archive",
            "own_comments",
            "own_replies",
        ]
    ] = Field(max_length=6)
    duplicate_representative_policy: Literal["representative_only", "include_all"]
    threshold_basis_points: int = Field(ge=0, le=10_000)
    maximum_candidates: int = Field(ge=1, le=1_000_000)
    maximum_bytes: int = Field(ge=1, le=4 * 1024 * 1024 * 1024 * 1024)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("keywords")
    @classmethod
    def unique_keywords(cls, value: list[KeywordPolicyV1]) -> list[KeywordPolicyV1]:
        names = [item.keyword for item in value]
        if len(names) != len(set(names)) or any(
            name != re.sub(r"\s+", " ", name.lower()).strip() for name in names
        ):
            raise ValueError("selection keywords are invalid")
        return value

    @field_validator(
        "source_weights_basis_points",
        "text_signal_weights_basis_points",
        "object_label_weights_basis_points",
    )
    @classmethod
    def bounded_weights(cls, value: dict[str, int]) -> dict[str, int]:
        if any(weight < 0 or weight > 10_000 for weight in value.values()):
            raise ValueError("selection weights are invalid")
        if any(not re.fullmatch(r"[a-z0-9_.-]{1,64}", str(key)) for key in value):
            raise ValueError("selection weight labels are invalid")
        return value


class SelectionTotalsV1(StrictSelectionModel):
    total: int = Field(ge=0, le=1_000_000)
    evaluated: int = Field(ge=0, le=1_000_000)
    candidates: int = Field(ge=0, le=1_000_000)
    auto_selected: int = Field(ge=0, le=1_000_000)
    selected: int = Field(ge=0, le=1_000_000)
    below_threshold: int = Field(ge=0, le=1_000_000)
    selected_bytes: int = Field(ge=0, le=4 * 1024 * 1024 * 1024 * 1024)


class SelectionRunV1(StrictSelectionModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = session_id_field(min_length=8, max_length=128)
    state: SelectionState
    policy_version: str = Field(min_length=1, max_length=64)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    selection_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_candidates: bool
    totals: SelectionTotalsV1
    started_at: str = Field(min_length=20, max_length=40)
    updated_at: str = Field(min_length=20, max_length=40)
    frozen_at: str | None = Field(default=None, min_length=20, max_length=40)
    confirmed_at: str | None = Field(default=None, min_length=20, max_length=40)
    failure_reason: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,128}$")

    @field_validator("started_at", "updated_at", "frozen_at", "confirmed_at")
    @classmethod
    def utc_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not value.endswith("Z") or parsed.utcoffset() != timedelta(0):
            raise ValueError("timestamp must use UTC Z notation")
        return value


class ModelSignalV1(StrictSelectionModel):
    signal: str = Field(pattern=r"^[a-z0-9_.:-]{1,128}$")
    value: str = Field(min_length=1, max_length=128)
    weight_basis_points: int = Field(ge=0, le=10_000)


class SelectionCandidateV1(StrictSelectionModel):
    record_id: str = Field(min_length=8, max_length=128)
    source_kind: SourceKind
    source_app: str | None = Field(default=None, max_length=255)
    evidence_text: str | None = Field(default=None, max_length=512)
    score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    auto_selected: bool
    selected: bool
    matched_keywords: list[str] = Field(max_length=64)
    matched_rules: list[str] = Field(max_length=64)
    model_signals: list[ModelSignalV1] = Field(max_length=32)
    reasons: list[str] = Field(max_length=64)
    human_override: HumanOverride
    operator_id: str | None = Field(default=None, min_length=8, max_length=128)
    revision: int = Field(ge=1)
    decided_at: str = Field(min_length=20, max_length=40)
    duplicate_group_id: str | None = Field(default=None, max_length=128)
    representative_record_id: str | None = Field(default=None, min_length=8, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    thumbnail_available: bool

    @field_validator("decided_at")
    @classmethod
    def decided_at_is_utc(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not value.endswith("Z") or parsed.utcoffset() != timedelta(0):
            raise ValueError("timestamp must use UTC Z notation")
        return value


class SelectionCandidatePageV1(StrictSelectionModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = session_id_field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    selection_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    records: list[SelectionCandidateV1] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=8, max_length=128)


class CandidateOverrideRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    override: HumanOverride


class CandidateConfirmRequest(RequestModel):
    expected_revision: int = Field(ge=1)


class CandidateListResponse(ResponseModel):
    session_id: str
    crawl_id: str
    state: SelectionState
    revision: int = Field(ge=1)
    selection_fingerprint: str | None = None
    policy_version: str
    policy_fingerprint: str
    items: list[SelectionCandidateV1] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)


class CandidateMutationResponse(ResponseModel):
    state: SelectionState
    revision: int = Field(ge=1)
    selection_fingerprint: str
    candidate: SelectionCandidateV1


class CandidateConfirmationResponse(ResponseModel):
    state: Literal["confirmed"]
    revision: int = Field(ge=1)
    selection_fingerprint: str
    confirmed_at: str
