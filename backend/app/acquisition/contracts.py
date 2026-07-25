from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app.models.schemas import AcquisitionMode, DeviceType, Scenario, SessionStatus


class ProviderKind(str, Enum):
    ANDROID_AGENT = "android_agent"
    ANDROID_LEGACY = "android_legacy"
    IOS = "ios"
    SIMULATOR = "simulator"
    ZIP_UPLOAD = "zip_upload"


class ProgressCallback(Protocol):
    async def __call__(
        self,
        phase: SessionStatus,
        percent: float,
        message: str,
        **fields: Any,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class UploadedArchive:
    content: bytes
    original_name: str


@dataclass(frozen=True, slots=True)
class AcquisitionContext:
    session_id: str
    device_id: str
    device_type: DeviceType
    mode: AcquisitionMode
    scenario: Scenario
    file_count: int
    on_progress: ProgressCallback
    simulated: bool = False
    archive: UploadedArchive | None = None
    request_id: str | None = None
    review_candidates: bool = False


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    staging: Path
    item_count: int
    duration_ms: float
    method: str
    provider: ProviderKind

    def as_legacy_tuple(self) -> tuple[Path, int, float, str]:
        return self.staging, self.item_count, self.duration_ms, self.method


class AcquisitionProvider(Protocol):
    kind: ProviderKind

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult: ...


class AndroidAgentRunner(Protocol):
    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult: ...
