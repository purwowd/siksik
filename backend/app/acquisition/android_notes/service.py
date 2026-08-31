from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.android_notes.contracts import (
    NoteRecord,
    NotesAcquisitionResult,
    NotesFlow,
    NotesGateway,
    NotesPolicy,
    NotesState,
)
from app.acquisition.android_notes.extractors import (
    GenericNotesExtractor,
    SamsungNotesExtractor,
)
from app.acquisition.errors import AcquisitionError
from app.acquisition.time_scope import build_time_scope
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.android_notes")
GatewayFactory = Callable[[str], NotesGateway]
NOTES_LAUNCH_BLOCKING_WARNINGS = frozenset(
    {
        "notes_launch_failed",
        "notes_foreground_unavailable",
        "notes_foreground_mismatch",
        "notes_foreground_changed",
        "notes_ui_surface_mismatch",
        "notes_export_surface_unrecognized",
    }
)


class AndroidNotesAcquisitionService:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self._gateway_factory = gateway_factory

    async def acquire(
        self,
        *,
        session_id: str,
        serial: str,
        staging: Path,
        mode: AcquisitionMode,
        simulated: bool,
        on_progress,
        request_id: str | None,
        reference: datetime | None = None,
    ) -> NotesAcquisitionResult:
        started = time.perf_counter()
        if simulated:
            return NotesAcquisitionResult(
                0,
                0.0,
                None,
                NotesState.UNAVAILABLE,
                None,
                None,
                None,
            )
        policy = build_notes_policy(mode, reference=reference)
        gateway = self._gateway_factory(serial)
        await on_progress(
            SessionStatus.ACQUIRING,
            57.0,
            "Mendeteksi aplikasi catatan Android",
            notes_state="detecting",
            notes_captured=0,
            notes_skipped=0,
            notes_warning_count=0,
            crawl_state=None,
            crawl_source=None,
            crawl_target=None,
            crawl_scope=None,
            crawl_stage=None,
            crawl_attempt=None,
            crawl_attempt_state=None,
            crawl_failure_class=None,
            crawl_reason=None,
            crawl_scroll_count=None,
            crawl_screenshot_count=None,
        )
        try:
            apps = await gateway.detect_apps()
        except AcquisitionError as exc:
            logger.warning(
                "android_notes_detection_unavailable",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error_category": exc.category.value,
                },
            )
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.UNAVAILABLE,
                ("notes_detection_unavailable",),
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "android_notes_detection_failed",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.UNAVAILABLE,
                ("notes_detection_unavailable",),
            )
        if not apps:
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.NOT_INSTALLED,
                (),
            )
        app = apps[0]
        await on_progress(
            SessionStatus.ACQUIRING,
            57.5,
            f"Mengambil catatan dari {app.label}",
            notes_state="acquiring",
            notes_flow=app.flow.value,
            notes_app=app.package_name,
        )
        warnings: set[str] = set()
        try:
            async with asyncio.timeout(policy.timeout_s):
                if app.flow == NotesFlow.SAMSUNG_EXPORT:
                    extraction = await SamsungNotesExtractor(gateway).extract(
                        app,
                        policy,
                    )
                    warnings.update(extraction.warnings)
                    if (
                        not extraction.records
                        and set(extraction.warnings).isdisjoint(
                            NOTES_LAUNCH_BLOCKING_WARNINGS
                        )
                    ):
                        fallback = await GenericNotesExtractor(gateway).extract(
                            app,
                            policy,
                        )
                        warnings.add("notes_samsung_export_fallback")
                        warnings.update(fallback.warnings)
                        extraction = fallback
                else:
                    extraction = await GenericNotesExtractor(gateway).extract(app, policy)
        except TimeoutError:
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.PARTIAL,
                ("notes_timeout",),
                app_package=app.package_name,
                app_label=app.label,
                flow=app.flow,
            )
        except AcquisitionError as exc:
            logger.warning(
                "android_notes_acquisition_unavailable",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error_category": exc.category.value,
                },
            )
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.PARTIAL,
                ("notes_adb_unavailable",),
                app_package=app.package_name,
                app_label=app.label,
                flow=app.flow,
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "android_notes_acquisition_failed",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            return await self._finish_unavailable(
                on_progress,
                started,
                NotesState.PARTIAL,
                ("notes_extraction_failed",),
                app_package=app.package_name,
                app_label=app.label,
                flow=app.flow,
            )
        finally:
            await gateway.restore_agent()
        warnings.update(extraction.warnings)
        written = await asyncio.to_thread(
            _persist_records,
            staging,
            extraction.records,
            policy,
        )
        state = extraction.state
        if written < len(extraction.records):
            warnings.add("notes_persistence_partial")
            state = NotesState.PARTIAL
        if warnings and state == NotesState.COMPLETE:
            state = NotesState.PARTIAL
        duration_ms = (time.perf_counter() - started) * 1000
        method = (
            "android_notes_samsung_export"
            if extraction.flow == NotesFlow.SAMSUNG_EXPORT
            else "android_notes_ui_walk"
        )
        logger.info(
            "android_notes_complete",
            extra={
                "session_id": session_id,
                "request_id": request_id,
                "state": state.value,
                "flow": extraction.flow.value,
                "items_saved": written,
                "items_skipped": extraction.skipped,
                "warning_count": len(warnings),
                "duration_ms": round(duration_ms, 1),
            },
        )
        await on_progress(
            SessionStatus.ACQUIRING,
            58.5,
            f"Catatan Android: {written} rekaman",
            notes_state=state.value,
            notes_flow=extraction.flow.value,
            notes_app=app.package_name,
            notes_captured=written,
            notes_skipped=extraction.skipped,
            notes_warning_count=len(warnings),
        )
        return NotesAcquisitionResult(
            item_count=written,
            duration_ms=duration_ms,
            method=method if written else None,
            state=state,
            flow=extraction.flow,
            app_package=app.package_name,
            app_label=app.label,
            skipped=extraction.skipped,
            warnings=tuple(sorted(warnings)),
        )

    async def _finish_unavailable(
        self,
        on_progress,
        started: float,
        state: NotesState,
        warnings: tuple[str, ...],
        *,
        app_package: str | None = None,
        app_label: str | None = None,
        flow: NotesFlow | None = None,
    ) -> NotesAcquisitionResult:
        duration_ms = (time.perf_counter() - started) * 1000
        message = (
            "Aplikasi catatan Android tidak ditemukan"
            if state == NotesState.NOT_INSTALLED
            else "Catatan Android tidak tersedia; akuisisi utama tetap dilanjutkan"
        )
        await on_progress(
            SessionStatus.ACQUIRING,
            58.5,
            message,
            notes_state=state.value,
            notes_flow=flow.value if flow is not None else None,
            notes_app=app_package,
            notes_captured=0,
            notes_skipped=0,
            notes_warning_count=len(warnings),
        )
        return NotesAcquisitionResult(
            item_count=0,
            duration_ms=duration_ms,
            method=None,
            state=state,
            flow=flow,
            app_package=app_package,
            app_label=app_label,
            warnings=warnings,
        )


def build_notes_policy(
    mode: AcquisitionMode,
    *,
    reference: datetime | None = None,
) -> NotesPolicy:
    scope = build_time_scope(mode, reference=reference)
    quick = mode == AcquisitionMode.QUICK
    return NotesPolicy(
        mode=mode,
        not_before=scope.not_before,
        max_notes=(
            settings.android_notes_quick_max_notes
            if quick
            else settings.android_notes_full_max_notes
        ),
        max_list_scrolls=(
            settings.android_notes_quick_list_scrolls
            if quick
            else settings.android_notes_full_list_scrolls
        ),
        max_editor_scrolls=(
            settings.android_notes_quick_editor_scrolls
            if quick
            else settings.android_notes_full_editor_scrolls
        ),
        timeout_s=(
            settings.android_notes_quick_timeout_s
            if quick
            else settings.android_notes_full_timeout_s
        ),
        max_note_chars=settings.android_notes_max_note_chars,
        max_export_file_bytes=settings.android_notes_max_export_file_bytes,
        max_export_bytes=(
            settings.android_notes_quick_max_export_bytes
            if quick
            else settings.android_notes_full_max_export_bytes
        ),
        max_ui_bytes=settings.android_notes_ui_dump_max_bytes,
    )


def _persist_records(
    staging: Path,
    records: tuple[NoteRecord, ...],
    policy: NotesPolicy,
) -> int:
    directory = staging / "notes"
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for record in records:
        text = record.normalized_text[: policy.max_note_chars]
        if not text:
            continue
        target = directory / f"{record.stable_id}.json"
        payload = {
            "schema_version": 1,
            "kind": "android_note",
            "record_id": record.stable_id,
            "source": "notes",
            "source_app": record.package_name,
            "source_app_label": record.app_label,
            "title": record.title,
            "body": record.body,
            "normalized_text": text,
            "preview_text": " ".join(text.split())[:2000],
            "display_name": record.title or "Catatan Android",
            "album": "Catatan",
            "folder": record.folder,
            "observed_at": record.observed_at,
            "captured_at": record.source_modified_at or record.observed_at,
            "source_modified_at": record.source_modified_at,
            "timestamp_raw": record.timestamp_raw,
            "extraction_method": record.extraction_method,
            "analysis_eligible": True,
            "time_scope_months": 3 if policy.mode == AcquisitionMode.QUICK else 6,
        }
        temporary = directory / f"_{record.stable_id}.part"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)
            continue
        written += 1
    return written
