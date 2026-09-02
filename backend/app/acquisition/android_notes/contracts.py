from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from app.models.schemas import AcquisitionMode

ANDROID_NOTE_MIME = "application/vnd.siksik.android-note+json"


class NotesFlow(str, Enum):
    SAMSUNG_EXPORT = "samsung_export"
    UI_WALK = "ui_walk"


class NotesState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    NOT_INSTALLED = "not_installed"


@dataclass(frozen=True, slots=True)
class NoteApp:
    package_name: str
    label: str
    component: str
    flow: NotesFlow


@dataclass(frozen=True, slots=True)
class NoteRecord:
    package_name: str
    app_label: str
    title: str
    body: str
    observed_at: str
    source_modified_at: str | None
    timestamp_raw: str | None
    folder: str | None
    extraction_method: str

    @property
    def normalized_text(self) -> str:
        parts = [part.strip() for part in (self.title, self.body) if part.strip()]
        return "\n".join(dict.fromkeys(parts))

    @property
    def stable_id(self) -> str:
        value = "\0".join(
            (
                self.package_name,
                self.title,
                self.body,
                self.source_modified_at or "",
                self.timestamp_raw or "",
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NotesPolicy:
    mode: AcquisitionMode
    not_before: datetime
    max_notes: int
    max_list_scrolls: int
    max_editor_scrolls: int
    timeout_s: float
    max_note_chars: int
    max_export_file_bytes: int
    max_export_bytes: int
    max_ui_bytes: int


@dataclass(frozen=True, slots=True)
class NotesExtractionResult:
    records: tuple[NoteRecord, ...]
    flow: NotesFlow
    state: NotesState
    warnings: tuple[str, ...] = ()
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class NotesAcquisitionResult:
    item_count: int
    duration_ms: float
    method: str | None
    state: NotesState
    flow: NotesFlow | None
    app_package: str | None
    app_label: str | None
    skipped: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RemoteExport:
    path: str
    size_bytes: int | None = None
    modified_epoch_s: int | None = None


class NotesGateway(Protocol):
    async def detect_apps(self) -> tuple[NoteApp, ...]: ...

    async def launch(self, app: NoteApp) -> bool: ...

    def last_failure_reason(self) -> str | None: ...

    async def adopt_export_surface(self) -> bool: ...

    async def restore_agent(self) -> None: ...

    async def dump_ui(self, max_bytes: int) -> str: ...

    async def screen_size(self) -> tuple[int, int]: ...

    async def tap(self, x: int, y: int) -> bool: ...

    async def long_press(self, x: int, y: int, duration_ms: int = 900) -> bool: ...

    async def swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> bool: ...

    async def back(self) -> bool: ...

    async def settle(self, seconds: float) -> None: ...

    async def list_exports(self) -> tuple[RemoteExport, ...]: ...

    async def pull_export(self, remote: RemoteExport, destination: Path, timeout_s: float) -> bool: ...

    async def cleanup_export(self, remote: RemoteExport) -> bool: ...


@dataclass(slots=True)
class NotesRunBudget:
    policy: NotesPolicy
    exported_bytes: int = 0
    warnings: set[str] = field(default_factory=set)

    def reserve_export(self, size_bytes: int | None) -> bool:
        size = size_bytes if size_bytes is not None and size_bytes >= 0 else 0
        if size > self.policy.max_export_file_bytes:
            self.warnings.add("notes_export_file_oversized")
            return False
        if self.exported_bytes + size > self.policy.max_export_bytes:
            self.warnings.add("notes_export_budget_exhausted")
            return False
        self.exported_bytes += size
        return True
