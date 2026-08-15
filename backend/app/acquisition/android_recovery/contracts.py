from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.acquisition.android_recovery.paths import recovery_relative_path
from app.acquisition.errors import AcquisitionError
from app.models.schemas import AcquisitionMode


class RecoverySource(str, Enum):
    MEDIASTORE_TRASH = "mediastore_trash"
    FILESYSTEM_TRASH = "filesystem_trash"
    GALLERY_CACHE = "gallery_cache"
    CLASSIC_THUMBNAIL = "classic_thumbnail"
    THUMBDATA = "thumbdata"


class RecoveryConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class TrashCandidate:
    candidate_id: str
    source: RecoverySource
    remote_path: str | None
    content_uri: str | None
    display_name: str
    mime_type: str | None
    reported_size: int | None
    expires_epoch_s: int | None


@dataclass(frozen=True, slots=True)
class ImageSpan:
    format: Literal["jpeg", "png", "webp"]
    extension: Literal[".jpg", ".png", ".webp"]
    offset: int
    end: int
    width: int
    height: int
    validation: str


@dataclass(frozen=True, slots=True)
class CacheImageRecord:
    blob_offset: int
    media_id: str
    original_path: str
    image: ImageSpan


@dataclass(frozen=True, slots=True)
class MediaStoreRow:
    media_id: str
    path: str
    display_name: str
    mime_type: str | None
    size_bytes: int | None
    expires_epoch_s: int | None
    is_trashed: bool


@dataclass(frozen=True, slots=True)
class MediaIndex:
    ids: frozenset[str]
    paths: frozenset[str]


class StrictRecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RecoveryArtifactV1(StrictRecoveryModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    relative_path: str = Field(min_length=1, max_length=512)
    source: Literal[
        "mediastore_trash",
        "filesystem_trash",
        "gallery_cache",
        "classic_thumbnail",
        "thumbdata",
    ]
    classification: Literal[
        "trash_resident",
        "source_missing",
        "orphan_mediastore_id",
        "unmatched_thumbdata_slot",
    ]
    confidence: Literal["high", "medium", "low"]
    capture_method: Literal["adb_pull", "mediastore_content_read", "cache_carve"]
    mime_type: str = Field(
        min_length=3,
        max_length=127,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    )
    size_bytes: int = Field(ge=1, le=4_294_967_296)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_epoch_s: int | None = Field(default=None, ge=1)
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("relative_path")
    @classmethod
    def _relative_path_is_owned(cls, value: str) -> str:
        try:
            return recovery_relative_path(value)
        except AcquisitionError as exc:
            raise ValueError("artifact path is outside the recovery namespace") from exc

    @model_validator(mode="after")
    def _filename_matches_candidate(self) -> Self:
        if PurePosixPath(self.relative_path).name.split(".", 1)[0] != self.candidate_id:
            raise ValueError("artifact filename does not match candidate id")
        return self


class RecoveryStatsV1(StrictRecoveryModel):
    candidates_discovered: int = Field(ge=0)
    payloads_captured: int = Field(ge=0)
    payloads_failed: int = Field(ge=0)
    payloads_skipped: int = Field(ge=0)
    duplicate_payloads: int = Field(ge=0)
    bytes_captured: int = Field(ge=0)
    cache_sources_scanned: int = Field(ge=0)
    cache_candidates_recovered: int = Field(ge=0)
    cache_scan_completed: bool = False


class RecoveryManifestV1(StrictRecoveryModel):
    schema_version: Literal[1] = 1
    mode: AcquisitionMode
    status: Literal["complete", "partial"]
    artifacts: list[RecoveryArtifactV1] = Field(max_length=10_000)
    stats: RecoveryStatsV1
    warnings: list[str] = Field(max_length=128)

    @field_validator("warnings")
    @classmethod
    def _warnings_are_safe_tokens(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("recovery warnings must be sorted and unique")
        if any(re.fullmatch(r"[a-z0-9_]{1,64}", item) is None for item in value):
            raise ValueError("recovery warning token is invalid")
        return value

    @model_validator(mode="after")
    def _manifest_is_consistent(self) -> Self:
        if len(self.artifacts) != self.stats.payloads_captured:
            raise ValueError("captured artifact count is inconsistent")
        if sum(item.size_bytes for item in self.artifacts) != self.stats.bytes_captured:
            raise ValueError("captured byte count is inconsistent")
        cache_sources = {
            RecoverySource.GALLERY_CACHE.value,
            RecoverySource.CLASSIC_THUMBNAIL.value,
            RecoverySource.THUMBDATA.value,
        }
        cache_count = sum(item.source in cache_sources for item in self.artifacts)
        if cache_count != self.stats.cache_candidates_recovered:
            raise ValueError("cache recovery count is inconsistent")
        for values in (
            [item.candidate_id for item in self.artifacts],
            [item.relative_path for item in self.artifacts],
            [item.sha256 for item in self.artifacts],
        ):
            if len(values) != len(set(values)):
                raise ValueError("recovery artifacts must be unique")
        if self.status == "complete" and (
            self.warnings or self.stats.payloads_failed or self.stats.payloads_skipped
        ):
            raise ValueError("complete recovery manifest contains partial results")
        return self


@dataclass(frozen=True, slots=True)
class RecoveryRunResult:
    staging: Path
    manifest: RecoveryManifestV1
    duration_ms: float

    @property
    def item_count(self) -> int:
        return len(self.manifest.artifacts)
