from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, Literal, TypeVar

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.acquisition.adb import resolve_agent_forward_host
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.core.config import settings
from app.selection.contracts import (
    SelectionCandidatePageV1,
    SelectionCandidateV1,
    SelectionPolicyV1,
    SelectionRunV1,
)

logger = logging.getLogger("siksik.acquisition.agent_client")
ResponseT = TypeVar("ResponseT", bound=BaseModel)
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _social_target_allowlist() -> frozenset[str]:
    return frozenset(settings.android_agent_social_targets)


def validate_utc_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp is invalid") from exc
    if not value.endswith("Z") or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC Z notation")
    return value


class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _clamp_agent_reason(value: object, *, max_length: int = 128) -> object:
    """Accept oversize agent reasons without failing the whole crawl page."""
    if isinstance(value, str) and len(value) > max_length:
        return value[:max_length]
    return value


CapabilityStateV1 = Literal[
    "unavailable",
    "not_granted",
    "awaiting_user",
    "granted",
    "denied",
    "restricted",
    "error",
]


class AgentCapabilityStatusV1(StrictAgentModel):
    state: CapabilityStateV1
    required_for_full: bool


class AgentCapabilitiesV1(StrictAgentModel):
    schema_version: int = Field(ge=1, le=100)
    agent_version: str = Field(min_length=1, max_length=64)
    agent_build_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    api_version: str = Field(min_length=1, max_length=32)
    api_port: int = Field(ge=1, le=65_535)
    android_api_level: int = Field(ge=26, le=10_000)
    package_name: str = Field(min_length=1, max_length=255)
    source_capabilities: dict[str, AgentCapabilityStatusV1]
    preprocessing_capabilities: dict[str, AgentCapabilityStatusV1]
    feature_capabilities: dict[str, AgentCapabilityStatusV1]
    permission_states: dict[str, AgentCapabilityStatusV1]
    special_access_states: dict[str, AgentCapabilityStatusV1]
    available_storage_bytes: int = Field(ge=0)
    active_session_id: str = Field(min_length=8, max_length=128)


class AgentHealthV1(StrictAgentModel):
    schema_version: int = Field(ge=1, le=100)
    session_id: str = Field(min_length=8, max_length=128)
    state: Literal["active"]
    agent_version: str = Field(min_length=1, max_length=64)
    agent_build_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    api_version: str = Field(min_length=1, max_length=32)
    api_port: int = Field(ge=1, le=65_535)


class AgentSessionV1(StrictAgentModel):
    session_id: str = Field(min_length=1, max_length=128)
    api_version: str = Field(min_length=1, max_length=32)
    state: Literal["active", "degraded", "closed"]


class SelectionMutationV1(StrictAgentModel):
    schema_version: Literal[1]
    run: SelectionRunV1
    candidate: SelectionCandidateV1


InventoryModeV1 = Literal["quick", "full"]
InventoryRunStateV1 = Literal[
    "ready",
    "crawling",
    "complete",
    "partial",
    "cancelled",
    "failed",
]
InventorySourceStateV1 = Literal[
    "pending",
    "crawling",
    "complete",
    "partial",
    "denied",
    "restricted",
    "unsupported",
    "cancelled",
    "failed",
]
InventorySourceAdapterV1 = Literal[
    "public_whatsapp",
    "public_telegram",
    "media_store_image",
    "media_store_video",
    "media_store_audio",
    "shared_storage_document",
    "document_tree",
    "sms_content_provider",
    "contacts_content_provider",
    "accessibility_visible_ui",
    "notification_listener",
    "ios_wda_visible_ui",
    "ios_backup_messages",
    "ios_backup_contacts",
]
InventorySourceKindV1 = Literal[
    "media_image",
    "media_video",
    "media_audio",
    "document",
    "sms",
    "contact",
    "visible_ui",
    "notification",
]
# Android agent inventory start/page must return exactly these adapters.
ANDROID_INVENTORY_SOURCES = {
    "public_whatsapp",
    "public_telegram",
    "media_store_image",
    "media_store_video",
    "media_store_audio",
    "shared_storage_document",
    "document_tree",
    "sms_content_provider",
    "contacts_content_provider",
    "accessibility_visible_ui",
    "notification_listener",
}
# iOS provenance adapters (AFC/WDA/backup) — not part of Android agent inventory.
IOS_INVENTORY_SOURCE_ADAPTERS = {
    "ios_wda_visible_ui",
    "ios_backup_messages",
    "ios_backup_contacts",
}
INVENTORY_SOURCES = ANDROID_INVENTORY_SOURCES


class InventorySourceProgressV1(StrictAgentModel):
    state: InventorySourceStateV1
    scanned_count: int = Field(ge=0)
    discovered_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    sampled: bool
    reason: str | None = Field(default=None, max_length=128)
    resume_cursor: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("reason", mode="before")
    @classmethod
    def clamp_reason(cls, value: object) -> object:
        return _clamp_agent_reason(value)


class InventoryTotalsV1(StrictAgentModel):
    scanned: int = Field(ge=0)
    discovered: int = Field(ge=0)
    duplicates: int = Field(ge=0)


class InventoryPartialReasonV1(StrictAgentModel):
    source: InventorySourceAdapterV1
    state: InventorySourceStateV1
    reason: str = Field(min_length=1, max_length=128)

    @field_validator("reason", mode="before")
    @classmethod
    def clamp_reason(cls, value: object) -> object:
        return _clamp_agent_reason(value)


class InventoryRunV1(StrictAgentModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    mode: InventoryModeV1
    state: InventoryRunStateV1
    started_at: str = Field(min_length=20, max_length=40)
    updated_at: str = Field(min_length=20, max_length=40)
    completed_at: str | None = Field(default=None, min_length=20, max_length=40)
    source_progress: dict[InventorySourceAdapterV1, InventorySourceProgressV1]
    totals: InventoryTotalsV1
    partial_reasons: list[InventoryPartialReasonV1] = Field(max_length=32)
    resume_cursors: dict[InventorySourceAdapterV1, str]

    _timestamps_are_utc = field_validator(
        "started_at",
        "updated_at",
        "completed_at",
    )(validate_utc_timestamp)


class InventoryExifV1(StrictAgentModel):
    state: Literal["present", "available", "gps_restricted", "restricted", "unavailable"]
    orientation: int | None = None
    camera_make: str | None = Field(default=None, max_length=256)
    camera_model: str | None = Field(default=None, max_length=256)
    lens_model: str | None = Field(default=None, max_length=256)
    exposure_time: str | None = Field(default=None, max_length=256)
    aperture: float | None = None
    focal_length: float | None = None
    iso: int | None = Field(default=None, ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    altitude: float | None = None
    captured_at: str | None = Field(default=None, min_length=20, max_length=40)
    warning_codes: list[str] = Field(max_length=16)

    _timestamp_is_utc = field_validator("captured_at")(validate_utc_timestamp)


class InventoryMetadataV1(StrictAgentModel):
    display_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=127)
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, ge=0)
    date_taken: str | None = Field(default=None, min_length=20, max_length=40)
    date_added: str | None = Field(default=None, min_length=20, max_length=40)
    date_modified: str | None = Field(default=None, min_length=20, max_length=40)
    capture_time: str | None = Field(default=None, min_length=20, max_length=40)
    capture_time_source: Literal[
        "exif_original",
        "date_taken",
        "date_added",
        "date_modified",
        "source_timestamp",
        "unknown",
    ]
    directory_hint: str | None = Field(default=None, max_length=512)
    is_favorite: bool = False
    exif: InventoryExifV1 | None = None
    warning_codes: list[str] = Field(max_length=16)
    thumbnail_available: bool

    _timestamps_are_utc = field_validator(
        "date_taken",
        "date_added",
        "date_modified",
        "capture_time",
    )(validate_utc_timestamp)


class SmsMetadataV1(StrictAgentModel):
    direction: Literal["received", "sent", "draft", "outbox", "failed", "queued", "unknown"]
    address: str | None = Field(default=None, max_length=512)
    address_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    thread_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    message_type: int
    status: int | None = None
    subscription_id: int | None = None
    is_read: bool | None = None
    is_seen: bool | None = None
    sent_at: str | None = Field(default=None, min_length=20, max_length=40)
    warning_codes: list[str] = Field(max_length=16)

    _timestamp_is_utc = field_validator("sent_at")(validate_utc_timestamp)


class ContactIdentityV1(StrictAgentModel):
    value: str = Field(min_length=1, max_length=2048)
    normalized_value: str | None = Field(default=None, max_length=2048)
    label: str | None = Field(default=None, max_length=256)


class ContactOrganizationV1(StrictAgentModel):
    company: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=2048)
    department: str | None = Field(default=None, max_length=2048)


class ContactMetadataV1(StrictAgentModel):
    display_name: str | None = Field(default=None, max_length=2048)
    lookup_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    phones: list[ContactIdentityV1] = Field(max_length=32)
    emails: list[ContactIdentityV1] = Field(max_length=32)
    organizations: list[ContactOrganizationV1] = Field(max_length=32)
    updated_at: str | None = Field(default=None, min_length=20, max_length=40)
    warning_codes: list[str] = Field(max_length=16)

    _timestamp_is_utc = field_validator("updated_at")(validate_utc_timestamp)


class VisibleBoundsV1(StrictAgentModel):
    left: int = Field(ge=-100_000, le=100_000)
    top: int = Field(ge=-100_000, le=100_000)
    right: int = Field(ge=-100_000, le=100_000)
    bottom: int = Field(ge=-100_000, le=100_000)


class VisibleNodeV1(StrictAgentModel):
    sequence: int = Field(ge=0, le=255)
    depth: int = Field(ge=0, le=16)
    text: str | None = Field(default=None, max_length=512)
    content_description: str | None = Field(default=None, max_length=512)
    class_name: str | None = Field(default=None, max_length=512)
    view_id: str | None = Field(default=None, max_length=512)
    bounds: VisibleBoundsV1
    clickable: bool
    scrollable: bool


class SocialProfileMetricsV1(StrictAgentModel):
    posts: int | None = Field(default=None, ge=0, le=10**12)
    followers: int | None = Field(default=None, ge=0, le=10**12)
    friends: int | None = Field(default=None, ge=0, le=10**12)
    following: int | None = Field(default=None, ge=0, le=10**12)


class VisibleUiMetadataV1(StrictAgentModel):
    package_name: str = Field(pattern=r"^[A-Za-z0-9._]{3,255}$")
    social_scope: Literal[
        "own_profile",
        "own_posts",
        "own_tweets",
        "own_story_archive",
        "own_comments",
        "own_replies",
    ]
    window_id: int
    activity_context: str | None = Field(default=None, max_length=512)
    event_type: int = Field(ge=0)
    screen_sequence: int = Field(ge=0)
    nodes: list[VisibleNodeV1] = Field(max_length=256)
    screenshot_ids: list[str] = Field(max_length=16)
    profile_links: list[str] = Field(default_factory=list, max_length=16)
    profile_username: str | None = Field(default=None, max_length=64)
    profile_display_name: str | None = Field(default=None, max_length=256)
    profile_bio: str | None = Field(default=None, max_length=4096)
    profile_metrics: SocialProfileMetricsV1 | None = None
    warning_codes: list[str] = Field(max_length=16)

    @model_validator(mode="after")
    def profile_links_match_scope(self) -> "VisibleUiMetadataV1":
        if self.social_scope != "own_profile" and (
            self.profile_links
            or self.profile_username
            or self.profile_display_name
            or self.profile_bio
            or self.profile_metrics is not None
        ):
            raise ValueError("profile metadata requires own profile scope")
        if any(len(value) > 2048 for value in self.profile_links):
            raise ValueError("profile link is too long")
        return self


class NotificationMetadataV1(StrictAgentModel):
    package_name: str = Field(pattern=r"^[A-Za-z0-9._]{3,255}$")
    notification_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=2048)
    sub_text: str | None = Field(default=None, max_length=2048)
    big_text: str | None = Field(default=None, max_length=32768)
    text_lines: list[str] = Field(max_length=32)
    category: str | None = Field(default=None, max_length=256)
    channel_id: str | None = Field(default=None, max_length=512)
    post_time: str = Field(min_length=20, max_length=40)
    removed_at: str | None = Field(default=None, min_length=20, max_length=40)
    update_count: int = Field(ge=1)
    warning_codes: list[str] = Field(max_length=16)

    _timestamps_are_utc = field_validator("post_time", "removed_at")(
        validate_utc_timestamp
    )


InventoryRecordMetadataV1 = (
    InventoryMetadataV1
    | SmsMetadataV1
    | ContactMetadataV1
    | VisibleUiMetadataV1
    | NotificationMetadataV1
)


class InventoryProvenanceV1(StrictAgentModel):
    source_adapter: InventorySourceAdapterV1
    enumeration_method: Literal[
        "android_platform_api",
        "android_content_provider",
        "android_accessibility",
        "android_uiautomator",
        "android_notification_listener",
        "ios_webdriveragent",
        "ios_mobilebackup2",
    ]
    agent_version: str = Field(min_length=1, max_length=64)
    original_staged: Literal[False]


PreprocessExecutionStatusV1 = Literal[
    "completed",
    "skipped",
    "truncated",
    "failed",
    "cancelled",
]


class PreprocessEngineIdentityV1(StrictAgentModel):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=160)
    model_asset: str | None = Field(default=None, max_length=512)
    model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PreprocessExecutionV1(StrictAgentModel):
    engine: PreprocessEngineIdentityV1
    status: PreprocessExecutionStatusV1
    duration_ms: int = Field(ge=0, le=3_600_000)
    warnings: list[str] = Field(max_length=32)


class ExactHashSignalV1(PreprocessExecutionV1):
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes_read: int = Field(ge=0, le=4 * 1024 * 1024 * 1024)


class PerceptualHashSignalV1(PreprocessExecutionV1):
    algorithm: Literal["dhash-64"]
    hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class OcrRegionV1(StrictAgentModel):
    text: str = Field(min_length=1, max_length=1024)
    left: int
    top: int
    right: int
    bottom: int
    confidence: float | None = Field(default=None, ge=0, le=1)


class OcrSignalV1(PreprocessExecutionV1):
    text: str = Field(max_length=32768)
    mean_confidence: float | None = Field(default=None, ge=0, le=1)
    regions: list[OcrRegionV1] = Field(max_length=128)


class DocumentTextSignalV1(PreprocessExecutionV1):
    state: Literal[
        "extracted",
        "blank",
        "encrypted",
        "corrupt",
        "unsupported_feature",
        "oversized",
        "truncated",
    ]
    extracted_characters: int = Field(ge=0, le=65536)


class FaceSignalV1(StrictAgentModel):
    face_index: int = Field(ge=0, le=8)
    confidence: float = Field(ge=0, le=1)
    left: int
    top: int
    right: int
    bottom: int
    vector_dimensions: int = Field(ge=1, le=4096)


class FacePreprocessSignalV1(PreprocessExecutionV1):
    signal_count: int = Field(ge=0, le=8)
    signals: list[FaceSignalV1] = Field(max_length=8)


class ObjectLabelSignalV1(StrictAgentModel):
    label: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    left: int
    top: int
    right: int
    bottom: int


class ObjectPreprocessSignalV1(PreprocessExecutionV1):
    labels: list[ObjectLabelSignalV1] = Field(max_length=12)


class NormalizedSourceTextV1(StrictAgentModel):
    status: Literal["completed"]
    characters: int = Field(ge=0, le=65536)


class DuplicateMembershipV1(StrictAgentModel):
    exact_group_id: str | None = Field(default=None, pattern=r"^exact_[0-9a-f]{24}$")
    perceptual_group_id: str | None = Field(
        default=None,
        pattern=r"^perceptual_[0-9a-f]{24}$",
    )
    representative_record_id: str | None = Field(default=None, min_length=8, max_length=128)


class PreprocessResultV1(StrictAgentModel):
    schema_version: Literal[1]
    status: PreprocessExecutionStatusV1
    warnings: list[str] = Field(max_length=64)
    exact_hash: ExactHashSignalV1 | None = None
    perceptual_hash: PerceptualHashSignalV1 | None = None
    ocr: OcrSignalV1 | None = None
    document_text: DocumentTextSignalV1 | None = None
    face: FacePreprocessSignalV1 | None = None
    objects: ObjectPreprocessSignalV1 | None = None
    normalized_source_text: NormalizedSourceTextV1 | None = None
    duplicate_membership: DuplicateMembershipV1 | None = None
    face_cluster_ids: list[str] = Field(default_factory=list, max_length=8)


class CanonicalSelectionModelSignalV1(StrictAgentModel):
    signal: str = Field(pattern=r"^[a-z0-9_.:-]{1,128}$")
    value: str = Field(min_length=1, max_length=128)
    weight_basis_points: int = Field(ge=0, le=10_000)


class CanonicalSelectionDecisionV1(StrictAgentModel):
    policy_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    auto_selected: bool
    selected: Literal[True]
    matched_keywords: list[str] = Field(max_length=64)
    matched_rules: list[str] = Field(max_length=64)
    model_signals: list[CanonicalSelectionModelSignalV1] = Field(max_length=32)
    reasons: list[str] = Field(max_length=64)
    human_override: Literal["none", "include", "exclude"]
    operator_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._:-]{1,128}$",
    )
    decided_at: str = Field(min_length=20, max_length=40)

    _decided_at_is_utc = field_validator("decided_at")(validate_utc_timestamp)


class InventoryRecordV1(StrictAgentModel):
    schema_version: Literal[1]
    record_id: str = Field(min_length=8, max_length=128)
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    source_kind: InventorySourceKindV1
    source_app: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    source_locator: str = Field(min_length=1, max_length=128)
    observed_at: str = Field(min_length=20, max_length=40)
    source_created_at: str | None = Field(default=None, min_length=20, max_length=40)
    source_modified_at: str | None = Field(default=None, min_length=20, max_length=40)
    normalized_text: str | None = Field(default=None, max_length=65536)
    metadata: InventoryRecordMetadataV1
    attachment_ids: list[str] = Field(max_length=32)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preprocessing: PreprocessResultV1 | None = None
    selection: CanonicalSelectionDecisionV1 | None = None
    provenance: InventoryProvenanceV1

    _timestamps_are_utc = field_validator(
        "observed_at",
        "source_created_at",
        "source_modified_at",
    )(validate_utc_timestamp)

    @model_validator(mode="after")
    def source_metadata_is_bound(self) -> "InventoryRecordV1":
        expected_metadata: dict[str, type[StrictAgentModel]] = {
            "media_image": InventoryMetadataV1,
            "media_video": InventoryMetadataV1,
            "media_audio": InventoryMetadataV1,
            "document": InventoryMetadataV1,
            "sms": SmsMetadataV1,
            "contact": ContactMetadataV1,
            "visible_ui": VisibleUiMetadataV1,
            "notification": NotificationMetadataV1,
        }
        if not isinstance(self.metadata, expected_metadata[self.source_kind]):
            raise ValueError("record metadata does not match source kind")
        if self.source_kind == "visible_ui":
            metadata = self.metadata
            if not isinstance(metadata, VisibleUiMetadataV1):
                raise ValueError("visible UI metadata is invalid")
            allowed_scopes = {
                "com.instagram.android": {
                    "own_profile",
                    "own_posts",
                    "own_story_archive",
                    "own_comments",
                },
                "com.twitter.android": {"own_profile", "own_tweets", "own_replies"},
                "com.facebook.katana": {
                    "own_profile",
                    "own_posts",
                    "own_story_archive",
                    "own_comments",
                },
            }
            if (
                self.source_app != metadata.package_name
                or metadata.social_scope
                not in allowed_scopes.get(metadata.package_name, set())
                or len(self.attachment_ids) != len(set(self.attachment_ids))
                or self.attachment_ids != metadata.screenshot_ids
            ):
                raise ValueError("visible UI account scope binding is invalid")
        return self


class InventoryPageV1(StrictAgentModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    source_adapter: InventorySourceAdapterV1
    source_state: InventorySourceStateV1
    source_reason: str | None = Field(default=None, max_length=128)
    sampled: bool
    scanned_count: int = Field(ge=0, le=10_000)
    discovered_count: int = Field(ge=0, le=10_000)
    duplicate_count: int = Field(ge=0, le=10_000)
    records: list[InventoryRecordV1] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("source_reason", mode="before")
    @classmethod
    def clamp_source_reason(cls, value: object) -> object:
        return _clamp_agent_reason(value)


class LiveSelectedRecordV1(StrictAgentModel):
    sequence: int = Field(ge=1)
    candidate: SelectionCandidateV1
    record: InventoryRecordV1

    @model_validator(mode="after")
    def selection_is_bound(self) -> "LiveSelectedRecordV1":
        if (
            not self.candidate.selected
            or self.candidate.record_id != self.record.record_id
            or self.record.selection is not None
        ):
            raise ValueError("live selected record binding is invalid")
        return self


class LiveSelectedRecordPageV1(StrictAgentModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    selection_state: Literal[
        "running",
        "awaiting_review",
        "confirmed",
        "cancelled",
        "failed",
    ]
    review_candidates: bool
    records: list[LiveSelectedRecordV1] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")


PreprocessingRunStateV1 = Literal["running", "complete", "partial", "cancelled", "failed"]


class PreprocessingTotalsV1(StrictAgentModel):
    total: int = Field(ge=0, le=1_000_000)
    pending: int = Field(ge=0, le=1_000_000)
    processing: int = Field(ge=0, le=32)
    completed: int = Field(ge=0, le=1_000_000)
    skipped: int = Field(ge=0, le=1_000_000)
    truncated: int = Field(ge=0, le=1_000_000)
    failed: int = Field(ge=0, le=1_000_000)
    cancelled: int = Field(ge=0, le=1_000_000)


class PreprocessorTotalsV1(StrictAgentModel):
    attempted: int = Field(ge=0, le=1_000_000)
    processed: int = Field(ge=0, le=1_000_000)
    skipped: int = Field(ge=0, le=1_000_000)
    truncated: int = Field(ge=0, le=1_000_000)
    failed: int = Field(ge=0, le=1_000_000)
    cancelled: int = Field(ge=0, le=1_000_000)


class PreprocessingRunV1(StrictAgentModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    state: PreprocessingRunStateV1
    started_at: str = Field(min_length=20, max_length=40)
    updated_at: str = Field(min_length=20, max_length=40)
    completed_at: str | None = Field(default=None, min_length=20, max_length=40)
    deadline_at: str = Field(min_length=20, max_length=40)
    totals: PreprocessingTotalsV1
    preprocessor_totals: dict[
        Literal[
            "exact_hash",
            "perceptual_hash",
            "ocr",
            "document_text",
            "face",
            "objects",
        ],
        PreprocessorTotalsV1,
    ] = Field(min_length=6, max_length=6)
    partial_reasons: list[str] = Field(max_length=32)

    _timestamps_are_utc = field_validator(
        "started_at",
        "updated_at",
        "completed_at",
        "deadline_at",
    )(validate_utc_timestamp)


class PreprocessedRecordPageV1(StrictAgentModel):
    schema_version: Literal[1]
    crawl_id: str = Field(min_length=8, max_length=128)
    siksik_session_id: str = Field(min_length=8, max_length=128)
    records: list[InventoryRecordV1] = Field(max_length=20)
    next_cursor: str | None = Field(default=None, min_length=8, max_length=128)


class CrawlTransferV1(StrictAgentModel):
    schema_version: Literal[1]
    stage_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    crawl_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    state: Literal[
        "queued",
        "copying",
        "finalizing",
        "completed",
        "failed",
        "cancelled",
        "cleaned",
    ]
    selection_revision: int = Field(ge=1)
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_records: int = Field(ge=0, le=10_000)
    completed_records: int = Field(ge=0, le=10_000)
    artifact_count: int = Field(ge=0, le=30_000)
    total_bytes: int = Field(ge=0, le=17_179_869_184)
    error_category: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]{1,128}$",
    )


class CrawlManifestDescriptorV1(StrictAgentModel):
    schema_version: Literal[1]
    stage_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    crawl_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    selection_revision: int = Field(ge=1)
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_format: Literal["direct_manifest_files_v1"]
    manifest_relative_path: str = Field(min_length=1, max_length=1024)
    manifest_size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CrawlCleanupReceiptV1(StrictAgentModel):
    schema_version: Literal[1]
    receipt_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    stage_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    crawl_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    deleted_files: int = Field(ge=0, le=100_000)
    already_absent: bool
    deleted_at_epoch_ms: int = Field(ge=1)


class AutomationResultV1(StrictAgentModel):
    schema_version: Literal[1]
    target_package: Literal[
        "com.twitter.android",
        "com.facebook.katana",
        "com.instagram.android",
    ]
    state: Literal[
        "complete",
        "partial",
        "cancelled",
        "failed",
        "target_missing",
        "timeout",
    ]
    reason: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,128}$")
    scroll_count: int = Field(ge=0, le=100)
    screenshot_ids: list[str] = Field(max_length=48)
    duration_ms: int = Field(ge=0, le=3_600_000)


class AgentErrorDetail(StrictAgentModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    request_id: str | None = Field(default=None, max_length=128)


class AgentErrorEnvelope(StrictAgentModel):
    error: AgentErrorDetail


@dataclass(frozen=True, slots=True)
class AgentClientConfig:
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or not 1 <= self.max_attempts <= 6:
            raise ValueError("agent client timeout or retry count is invalid")
        if not 1024 <= self.max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("agent response limit is invalid")


@dataclass(frozen=True, slots=True)
class AgentResponse(Generic[ResponseT]):
    body: ResponseT
    request_id: str
    status_code: int


AGENT_CODE_MAP: dict[str, ErrorCategory] = {
    "validation_error": ErrorCategory.VALIDATION_ERROR,
    "companion_auth_missing": ErrorCategory.AGENT_AUTH_INVALID,
    "companion_auth_invalid": ErrorCategory.AGENT_AUTH_INVALID,
    "companion_auth_expired": ErrorCategory.AGENT_AUTH_INVALID,
    "companion_session_mismatch": ErrorCategory.AGENT_SESSION_MISMATCH,
    "companion_api_mismatch": ErrorCategory.AGENT_API_MISMATCH,
    "agent_auth_invalid": ErrorCategory.AGENT_AUTH_INVALID,
    "agent_session_mismatch": ErrorCategory.AGENT_SESSION_MISMATCH,
    "agent_api_mismatch": ErrorCategory.AGENT_API_MISMATCH,
    "approval_ui_unavailable": ErrorCategory.AWAITING_USER,
    "grant_denied": ErrorCategory.ACCESS_DENIED,
    "grant_cancelled": ErrorCategory.ACCESS_DENIED,
    "grant_revoked": ErrorCategory.ACCESS_DENIED,
    "storage_unavailable": ErrorCategory.STORAGE_UNAVAILABLE,
    "not_found": ErrorCategory.NOT_FOUND,
    "conflict": ErrorCategory.CONFLICT,
    "invalid_cursor": ErrorCategory.VALIDATION_ERROR,
    "source_adapter_failed": ErrorCategory.AGENT_UNREACHABLE,
    "selection_policy_missing": ErrorCategory.CONFLICT,
    "selection_policy_mismatch": ErrorCategory.CONFLICT,
    "selection_not_ready": ErrorCategory.CONFLICT,
    "selection_not_reviewable": ErrorCategory.CONFLICT,
    "selection_revision_conflict": ErrorCategory.CONFLICT,
    "selection_immutable": ErrorCategory.CONFLICT,
    "selection_budget_exceeded": ErrorCategory.CONFLICT,
    "stage_not_ready": ErrorCategory.CONFLICT,
    "stage_failed": ErrorCategory.AGENT_UNREACHABLE,
    "stage_cancelled": ErrorCategory.CONFLICT,
    "access_denied": ErrorCategory.ACCESS_DENIED,
}


def validation_issues(exc: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for error in exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:8]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "$"
        issues.append(
            {
                "location": location[:256],
                "type": str(error.get("type", "validation_error"))[:128],
            },
        )
    return issues


def format_validation_issue(issues: list[dict[str, str]]) -> str:
    if not issues:
        return "validation_error"
    first = issues[0]
    return f"{first['location']}={first['type']}"


class GoogleAccountInfoV1(StrictAgentModel):
    name: str = Field(min_length=1, max_length=256)
    type: str = Field(default="com.google", max_length=128)


class GoogleAccountsResponseV1(StrictAgentModel):
    session_id: str = Field(min_length=1, max_length=128)
    accounts: list[GoogleAccountInfoV1] = Field(default_factory=list, max_length=64)


class GoogleTokenResponseV1(StrictAgentModel):
    session_id: str = Field(min_length=1, max_length=128)
    account_name: str = Field(min_length=1, max_length=256)
    token: str | None = Field(default=None, max_length=2048)
    scope: str | None = Field(default=None, max_length=512)
    error: str | None = Field(default=None, max_length=256)


class AgentClient:
    def __init__(
        self,
        host_port: int,
        token: str,
        *,
        config: AgentClientConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        forward_host: str | None = None,
    ) -> None:
        if not 1 <= host_port <= 65535:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Port agent tidak valid.")
        if not isinstance(token, str) or not 32 <= len(token) <= 512 or "\x00" in token:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Token agent tidak valid.")
        host = resolve_agent_forward_host(
            forward_host or settings.android_agent_forward_host,
        )
        self._base_url = httpx.URL(f"http://{host}:{host_port}")
        self._token = token
        self._config = config or AgentClientConfig()
        self._transport = transport

    async def capabilities(
        self,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[AgentCapabilitiesV1]:
        return await self.request(
            "GET",
            "/v1/capabilities",
            AgentCapabilitiesV1,
            request_id=request_id,
        )

    async def health(self, *, request_id: str | None = None) -> AgentResponse[AgentHealthV1]:
        return await self.request(
            "GET",
            "/v1/health",
            AgentHealthV1,
            request_id=request_id,
        )

    async def bootstrap(
        self,
        session_id: str,
        api_version: str,
        *,
        selection_policy: SelectionPolicyV1 | None = None,
        review_candidates: bool = False,
        request_id: str | None = None,
    ) -> AgentResponse[AgentSessionV1]:
        self._validate_id(session_id, "session")
        if not api_version or len(api_version) > 32:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Versi API agent tidak valid.")
        body: dict[str, object] = {"session_id": session_id, "api_version": api_version}
        if selection_policy is not None:
            body["selection_policy"] = selection_policy.model_dump(mode="json")
            body["review_candidates"] = review_candidates
        return await self.request(
            "POST",
            "/v1/sessions",
            AgentSessionV1,
            json_body=body,
            request_id=request_id,
            retry_auth_once=True,
        )

    async def stop(
        self,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[AgentSessionV1]:
        self._validate_id(session_id, "session")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/stop",
            AgentSessionV1,
            json_body={},
            request_id=request_id,
        )

    async def start_inventory(
        self,
        session_id: str,
        mode: InventoryModeV1,
        *,
        document_grant_id: str | None = None,
        target_packages: list[str] | None = None,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryRunV1]:
        self._validate_id(session_id, "session")
        if mode not in {"quick", "full"}:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Mode inventory tidak valid.")
        if document_grant_id is not None:
            self._validate_id(document_grant_id, "grant")
        targets = target_packages or []
        allowed_targets = _social_target_allowlist()
        if len(targets) != len(set(targets)) or not set(targets) <= allowed_targets:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Daftar target social tidak valid.",
            )
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl",
            InventoryRunV1,
            json_body={
                "mode": mode,
                "document_grant_id": document_grant_id,
                "target_packages": targets,
            },
            request_id=request_id,
        )

    async def report_automation_result(
        self,
        session_id: str,
        crawl_id: str,
        result: AutomationResultV1,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/automation-results",
            InventoryRunV1,
            json_body=result.model_dump(mode="json", exclude={"schema_version"}),
            request_id=request_id,
        )

    async def inventory_status(
        self,
        session_id: str,
        crawl_id: str | None = None,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryRunV1]:
        self._validate_id(session_id, "session")
        suffix = ""
        if crawl_id is not None:
            suffix = f"/{self._validate_id(crawl_id, 'crawl')}"
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl{suffix}",
            InventoryRunV1,
            request_id=request_id,
        )

    async def inventory_page(
        self,
        session_id: str,
        crawl_id: str,
        source: InventorySourceAdapterV1,
        *,
        cursor: str | None = None,
        limit: int = 100,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryPageV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if source not in INVENTORY_SOURCES or not 1 <= limit <= 100:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Parameter halaman inventory tidak valid.",
            )
        query: dict[str, str | int] = {"source": source, "limit": limit}
        if cursor is not None:
            query["cursor"] = self._validate_id(cursor, "cursor")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/records?{httpx.QueryParams(query)}",
            InventoryPageV1,
            request_id=request_id,
        )

    async def cancel_inventory(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryRunV1]:
        return await self._inventory_action(session_id, crawl_id, "cancel", request_id)

    async def resume_inventory(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[InventoryRunV1]:
        return await self._inventory_action(session_id, crawl_id, "resume", request_id)

    async def start_preprocessing(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[PreprocessingRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/preprocessing",
            PreprocessingRunV1,
            json_body={},
            request_id=request_id,
        )

    async def preprocessing_status(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[PreprocessingRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/preprocessing",
            PreprocessingRunV1,
            request_id=request_id,
        )

    async def preprocessing_records(
        self,
        session_id: str,
        crawl_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
        request_id: str | None = None,
    ) -> AgentResponse[PreprocessedRecordPageV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if not 1 <= limit <= 20:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas record preprocessing tidak valid.",
            )
        query: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            query["cursor"] = self._validate_id(cursor, "cursor")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/preprocessing/records?"
            f"{httpx.QueryParams(query)}",
            PreprocessedRecordPageV1,
            request_id=request_id,
        )

    async def cancel_preprocessing(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[PreprocessingRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/preprocessing/cancel",
            PreprocessingRunV1,
            json_body={},
            request_id=request_id,
        )

    async def start_selection(
        self,
        session_id: str,
        crawl_id: str,
        policy_fingerprint: str,
        review_candidates: bool,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if not re.fullmatch(r"[0-9a-f]{64}", policy_fingerprint):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Fingerprint policy selection tidak valid.",
            )
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection",
            SelectionRunV1,
            json_body={
                "policy_fingerprint": policy_fingerprint,
                "review_candidates": review_candidates,
            },
            request_id=request_id,
        )

    async def selection_status(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection",
            SelectionRunV1,
            request_id=request_id,
        )

    async def selection_candidates(
        self,
        session_id: str,
        crawl_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionCandidatePageV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if not 1 <= limit <= 100:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas candidate selection tidak valid.",
            )
        query: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            query["cursor"] = self._validate_id(cursor, "cursor")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection/candidates?"
            f"{httpx.QueryParams(query)}",
            SelectionCandidatePageV1,
            request_id=request_id,
        )

    async def live_selected_records(
        self,
        session_id: str,
        crawl_id: str,
        *,
        cursor: str | None = None,
        limit: int = 16,
        request_id: str | None = None,
    ) -> AgentResponse[LiveSelectedRecordPageV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if not 1 <= limit <= 100:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Batas live selection tidak valid.",
            )
        query: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            if not cursor.isdigit() or len(cursor) > 20:
                raise acquisition_error(
                    ErrorCategory.VALIDATION_ERROR,
                    "Cursor live selection tidak valid.",
                )
            query["cursor"] = cursor
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection/live-selected-records?"
            f"{httpx.QueryParams(query)}",
            LiveSelectedRecordPageV1,
            request_id=request_id,
        )

    async def mutate_selection_candidate(
        self,
        session_id: str,
        crawl_id: str,
        record_id: str,
        *,
        expected_revision: int,
        override: Literal["none", "include", "exclude"],
        operator_id: str,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionMutationV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        self._validate_id(record_id, "record")
        self._validate_id(operator_id, "operator")
        if expected_revision < 1 or override not in {"none", "include", "exclude"}:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Perubahan candidate selection tidak valid.",
            )
        return await self.request(
            "PATCH",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection/candidates/{record_id}",
            SelectionMutationV1,
            json_body={
                "expected_revision": expected_revision,
                "override": override,
                "operator_id": operator_id,
            },
            request_id=request_id,
        )

    async def confirm_selection(
        self,
        session_id: str,
        crawl_id: str,
        *,
        expected_revision: int,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        if expected_revision < 1:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Revision selection tidak valid.",
            )
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection/confirm",
            SelectionRunV1,
            json_body={"expected_revision": expected_revision},
            request_id=request_id,
        )

    async def cancel_selection(
        self,
        session_id: str,
        crawl_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[SelectionRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/selection/cancel",
            SelectionRunV1,
            json_body={},
            request_id=request_id,
        )

    async def start_transfer(
        self,
        session_id: str,
        crawl_id: str,
        *,
        stage_id: str,
        selection_revision: int,
        selection_fingerprint: str,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> AgentResponse[CrawlTransferV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        self._validate_id(stage_id, "stage")
        if selection_revision < 1 or not re.fullmatch(r"[0-9a-f]{64}", selection_fingerprint):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Selection transfer tidak valid.")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/transfer",
            CrawlTransferV1,
            json_body={
                "stage_id": stage_id,
                "selection_revision": selection_revision,
                "selection_fingerprint": selection_fingerprint,
            },
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    async def transfer_status(
        self,
        session_id: str,
        crawl_id: str,
        stage_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[CrawlTransferV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        self._validate_id(stage_id, "stage")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/transfer/status?"
            f"{httpx.QueryParams({'stage_id': stage_id})}",
            CrawlTransferV1,
            request_id=request_id,
        )

    async def transfer_manifest(
        self,
        session_id: str,
        crawl_id: str,
        stage_id: str,
        *,
        request_id: str | None = None,
    ) -> AgentResponse[CrawlManifestDescriptorV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        self._validate_id(stage_id, "stage")
        return await self.request(
            "GET",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/transfer/manifest?"
            f"{httpx.QueryParams({'stage_id': stage_id})}",
            CrawlManifestDescriptorV1,
            request_id=request_id,
        )

    async def cleanup_transfer(
        self,
        session_id: str,
        crawl_id: str,
        stage_id: str,
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> AgentResponse[CrawlCleanupReceiptV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        self._validate_id(stage_id, "stage")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/transfer/cleanup",
            CrawlCleanupReceiptV1,
            json_body={"stage_id": stage_id},
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    async def list_google_accounts(
        self,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> list[GoogleAccountInfoV1]:
        self._validate_id(session_id, "session")
        res = await self.request(
            "GET",
            "/v1/accounts/google",
            GoogleAccountsResponseV1,
            request_id=request_id,
        )
        return res.body.accounts

    async def get_google_auth_token(
        self,
        session_id: str,
        account_name: str,
        scope: str = "oauth2:https://www.googleapis.com/auth/gmail.readonly",
        *,
        request_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str | None:
        self._validate_id(session_id, "session")
        json_body: dict[str, str] = {"account_name": account_name, "scope": scope}
        client_id = settings.gmail_client_id.strip()
        if client_id:
            json_body["client_id"] = client_id
        res = await self.request(
            "POST",
            "/v1/accounts/google/token",
            GoogleTokenResponseV1,
            json_body=json_body,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )
        return res.body.token

    async def _inventory_action(
        self,
        session_id: str,
        crawl_id: str,
        action: Literal["cancel", "resume"],
        request_id: str | None,
    ) -> AgentResponse[InventoryRunV1]:
        self._validate_id(session_id, "session")
        self._validate_id(crawl_id, "crawl")
        return await self.request(
            "POST",
            f"/v1/sessions/{session_id}/crawl/{crawl_id}/{action}",
            InventoryRunV1,
            json_body={},
            request_id=request_id,
        )

    async def request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseT],
        *,
        json_body: dict[str, object] | None = None,
        request_id: str | None = None,
        retry_auth_once: bool = False,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentResponse[ResponseT]:
        if method not in {"GET", "POST", "PATCH", "DELETE"}:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Method agent tidak valid.")
        if not path.startswith("/v1/") or "//" in path or "\x00" in path:
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Path agent tidak valid.")
        rid = self._request_id(request_id)
        if idempotency_key is not None and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{8,128}",
            idempotency_key,
        ):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Idempotency key tidak valid.")
        last_error: AcquisitionError | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                return await self._request_once(
                    method,
                    path,
                    response_model,
                    json_body=json_body,
                    request_id=rid,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                )
            except AcquisitionError as exc:
                transient = exc.category == ErrorCategory.AGENT_UNREACHABLE and exc.retryable
                stale_auth = (
                    retry_auth_once
                    and attempt == 1
                    and exc.category == ErrorCategory.AGENT_AUTH_INVALID
                )
                if attempt >= self._config.max_attempts or not (transient or stale_auth):
                    raise
                last_error = exc
                logger.warning(
                    "agent_request_retry",
                    extra={
                        "request_id": rid,
                        "attempt": attempt,
                        "max_attempts": self._config.max_attempts,
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    },
                )
                await asyncio.sleep(min(0.1 * (2 ** (attempt - 1)), 0.8))
        if last_error is None:
            raise acquisition_error(ErrorCategory.INTERNAL_ERROR, "Request agent tidak diproses.")
        raise last_error

    async def _request_once(
        self,
        method: str,
        path: str,
        response_model: type[ResponseT],
        *,
        json_body: dict[str, object] | None,
        request_id: str,
        idempotency_key: str | None,
        timeout_seconds: float | None = None,
    ) -> AgentResponse[ResponseT]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=timeout_seconds or self._config.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                headers = {
                    "Authorization": f"Bearer {self._token}",
                    "X-Request-ID": request_id,
                    "Accept": "application/json",
                }
                if idempotency_key is not None:
                    headers["Idempotency-Key"] = idempotency_key
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise acquisition_error(
                ErrorCategory.AGENT_UNREACHABLE,
                "Request ke Android agent melewati batas waktu.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise acquisition_error(
                ErrorCategory.AGENT_UNREACHABLE,
                "Android agent tidak dapat dihubungi.",
                retryable=True,
            ) from exc

        declared = response.headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > self._config.max_response_bytes):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Respons Android agent melewati batas ukuran.",
            )
        if len(response.content) > self._config.max_response_bytes:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Respons Android agent melewati batas ukuran.",
            )
        response_request_id = response.headers.get("x-request-id")
        if response_request_id != request_id:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Android agent mengembalikan request ID yang tidak valid.",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Android agent mengembalikan JSON yang tidak valid.",
            ) from exc
        if response.is_error:
            try:
                detail = AgentErrorEnvelope.model_validate(payload).error
            except ValidationError as exc:
                issues = validation_issues(exc)
                logger.error(
                    "agent_error_contract_invalid",
                    extra={
                        "request_id": request_id,
                        "http_method": method,
                        "status_code": response.status_code,
                        "validation_issues": issues,
                    },
                )
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Android agent mengembalikan error yang tidak valid "
                    f"({format_validation_issue(issues)}).",
                ) from exc
            category = AGENT_CODE_MAP.get(detail.code, ErrorCategory.AGENT_UNREACHABLE)
            raise acquisition_error(
                category,
                detail.message,
                retryable=detail.retryable,
            )
        try:
            body = response_model.model_validate(payload)
        except ValidationError as exc:
            issues = validation_issues(exc)
            source_match = re.search(r"[?&]source=([a-z_]+)", path)
            source_adapter = source_match.group(1) if source_match is not None else None
            contract_name = response_model.__name__
            if source_adapter is not None:
                contract_name = f"{contract_name}/{source_adapter}"
            logger.error(
                "agent_response_contract_invalid",
                extra={
                    "request_id": request_id,
                    "http_method": method,
                    "response_model": response_model.__name__,
                    "source_adapter": source_adapter,
                    "status_code": response.status_code,
                    "validation_issues": issues,
                },
            )
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Android agent mengembalikan kontrak data yang tidak valid "
                f"({contract_name}: {format_validation_issue(issues)}).",
            ) from exc
        return AgentResponse(body=body, request_id=request_id, status_code=response.status_code)

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                f"ID {label} tidak valid.",
            )
        return value

    @staticmethod
    def _request_id(value: str | None) -> str:
        request_id = value or str(uuid.uuid4())
        if not SAFE_REQUEST_ID.fullmatch(request_id):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Request ID tidak valid.")
        return request_id
