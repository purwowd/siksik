from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.acquisition.analysis_plan import (
    AnalysisPlan,
    analysis_plan_from_progress,
    default_analysis_plan,
)
from app.core.config import settings
from app.core.request_context import current_request_id
from app.core.db import db, row_to_session, utcnow
from app.acquisition.errors import AcquisitionError
from app.models.schemas import (
    AcquisitionMode,
    DeviceType,
    ParticipantInput,
    Scenario,
    SessionStatus,
    StartSessionRequest,
)
from app.services.participant import (
    find_registration_conflict,
    participant_dict,
    participant_display_label,
    require_complete_participant,
)
from app.services import acquisition as acq
from app.services import analysis as ai
from app.services import reports as rpt

if TYPE_CHECKING:
    from app.acquisition.runtime import AgentRuntimeRecord

SESSION_LOCKED_DETAIL = "Sesi sudah disahkan — data terkunci."


def authorized_at_from_progress(progress_json: Any) -> str | None:
    try:
        progress = (
            progress_json
            if isinstance(progress_json, dict)
            else json.loads(progress_json or "{}")
        )
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(progress, dict):
        return None
    value = str(progress.get("authorized_at") or "").strip()
    return value or None


async def require_unlocked_session(session_id: str) -> None:
    row = await db.fetchone(
        "SELECT progress_json FROM sessions WHERE id = ?",
        (session_id,),
    )
    if not row:
        raise KeyError("Session not found")
    if authorized_at_from_progress(row["progress_json"]):
        raise RuntimeError(SESSION_LOCKED_DETAIL)


_PROGRESS_TIMING_KEYS = {
    "android_inventory_ms": "t_inventory_ms",
    "android_preprocessing_ms": "t_preprocess_ms",
    "android_selection_ms": "t_selection_ms",
    "android_transfer_ms": "t_transfer_ms",
}


def _label_for_session(*, label: str | None, participant: ParticipantInput, fallback: str) -> str:
    if label and label.strip():
        return label.strip()
    return participant_display_label(participant) or fallback


async def _ensure_unique_registration(
    participant: ParticipantInput,
    *,
    exclude_session_id: str | None = None,
) -> dict[str, Any]:
    payload = participant_dict(participant)
    conflict = await find_registration_conflict(
        payload["registration_no"],
        exclude_session_id=exclude_session_id,
    )
    if conflict:
        raise RuntimeError(
            f"No. peserta {payload['registration_no']} sudah dipakai sesi lain hari ini "
            f"({str(conflict['id'])[:8]}… · {conflict['label']})."
        )
    return payload


_IN_FLIGHT_STATUSES = (
    "pending",
    "detecting",
    "preparing_agent",
    "awaiting_access",
    "acquiring",
    "selecting",
    "awaiting_review",
    "indexing",
    "analyzing",
)


def _live_ios_request(req: StartSessionRequest) -> bool:
    device_id = req.device_id or ""
    return (
        req.device_type == DeviceType.IOS
        and not req.force_simulated
        and not device_id.startswith("sim-")
    )


class SessionManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._active_device: str | None = None

    async def has_in_flight(self) -> bool:
        row = await db.fetchone(
            f"""
            SELECT id FROM sessions
            WHERE status IN ({",".join("?" for _ in _IN_FLIGHT_STATUSES)})
            LIMIT 1
            """,
            _IN_FLIGHT_STATUSES,
        )
        return row is not None

    async def create_and_run(
        self,
        req: StartSessionRequest,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        from app.acquisition.ios_setup import ios_setup

        await ios_setup.assert_session_allowed(req)
        async with self._lock:
            active = await db.fetchone(
                """
                SELECT id FROM sessions
                WHERE status IN (
                    'pending','detecting','preparing_agent','awaiting_access',
                    'acquiring','selecting','awaiting_review','indexing','analyzing'
                )
                LIMIT 1
                """
            )
            if active:
                raise RuntimeError(
                    "Sesi lain masih berjalan. Selesaikan / batalkan dulu (satu perangkat per sesi)."
                )

            session_id = str(uuid.uuid4())
            device_id = req.device_id or (
                "sim-android-01" if req.device_type != DeviceType.IOS else "sim-iphone-01"
            )
            label = _label_for_session(
                label=req.label,
                participant=req.participant,
                fallback=f"Sesi {device_id}",
            )
            participant = await _ensure_unique_registration(req.participant)
            now = utcnow()
            plan = req.analysis_plan()
            progress = acq.empty_progress(SessionStatus.PENDING)
            progress.update(plan.to_progress())
            timing = acq.empty_timing()

            await db.execute(
                """
                INSERT INTO sessions (
                    id, device_id, device_type, label, mode, scenario, status,
                    progress_json, timing_json, recommendation, error, created_by,
                    review_candidates, participant_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    device_id,
                    req.device_type.value,
                    label,
                    req.mode.value,
                    req.scenario.value,
                    SessionStatus.PENDING.value,
                    json.dumps(progress),
                    json.dumps(timing),
                    None,
                    None,
                    operator_id,
                    int(req.review_candidates),
                    json.dumps(participant, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            try:
                from app.services.audit import record_audit

                await record_audit(
                    session_id=session_id,
                    actor=operator_id or "sistem",
                    action="session_started",
                    detail=req.mode.value,
                )
            except Exception:
                pass
            self._active_device = device_id
            task = asyncio.create_task(self._run_pipeline(session_id, req))
            self._tasks[session_id] = task
            return await self.get(session_id)

    async def create_and_run_from_zip(
        self,
        *,
        zip_bytes: bytes,
        original_name: str,
        mode: AcquisitionMode = AcquisitionMode.QUICK,
        label: str | None = None,
        participant: ParticipantInput | None = None,
        operator_id: str | None = None,
        analysis_plan: AnalysisPlan | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            active = await db.fetchone(
                """
                SELECT id FROM sessions
                WHERE status IN (
                    'pending','detecting','preparing_agent','awaiting_access',
                    'acquiring','selecting','awaiting_review','indexing','analyzing'
                )
                LIMIT 1
                """
            )
            if active:
                raise RuntimeError(
                    "Sesi lain masih berjalan. Selesaikan / batalkan dulu (satu perangkat per sesi)."
                )

            session_id = str(uuid.uuid4())
            device_id = f"zip:{original_name[:40]}"
            participant = participant or ParticipantInput()
            session_label = _label_for_session(
                label=label,
                participant=participant,
                fallback=f"ZIP · {original_name}",
            )
            participant_payload = await _ensure_unique_registration(participant)
            now = utcnow()
            plan = analysis_plan or default_analysis_plan()
            progress = acq.empty_progress(SessionStatus.PENDING)
            progress.update(plan.to_progress())
            timing = acq.empty_timing()

            await db.execute(
                """
                INSERT INTO sessions (
                    id, device_id, device_type, label, mode, scenario, status,
                    progress_json, timing_json, recommendation, error, created_by,
                    review_candidates, participant_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    device_id,
                    DeviceType.ANDROID.value,
                    session_label,
                    mode.value,
                    Scenario.LULUS.value,
                    SessionStatus.PENDING.value,
                    json.dumps(progress),
                    json.dumps(timing),
                    None,
                    None,
                    operator_id,
                    0,
                    json.dumps(participant_payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._active_device = device_id
            task = asyncio.create_task(
                self._run_zip_pipeline(
                    session_id,
                    zip_bytes,
                    original_name,
                    mode,
                    plan,
                )
            )
            self._tasks[session_id] = task
            return await self.get(session_id)

    async def get(self, session_id: str) -> dict[str, Any]:
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not row:
            raise KeyError("Session not found")
        return row_to_session(row)

    async def update_participant(
        self,
        session_id: str,
        participant: ParticipantInput,
    ) -> dict[str, Any]:
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not row:
            raise KeyError("Session not found")
        if authorized_at_from_progress(row["progress_json"]):
            raise RuntimeError(SESSION_LOCKED_DETAIL)
        require_complete_participant(participant)
        payload = await _ensure_unique_registration(
            participant,
            exclude_session_id=session_id,
        )
        now = utcnow()
        await db.execute(
            """
            UPDATE sessions
            SET participant_json = ?, label = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False),
                _label_for_session(
                    label=None,
                    participant=participant,
                    fallback=row["label"],
                ),
                now,
                session_id,
            ),
        )
        return await self.get(session_id)

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await db.fetchall(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [row_to_session(r) for r in rows]

    async def list_sessions_page(
        self, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        total_row = await db.fetchone("SELECT COUNT(*) AS c FROM sessions")
        total = int(total_row["c"]) if total_row else 0
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(max(1, page), pages)
        offset = (page - 1) * page_size
        rows = await db.fetchall(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        )
        return [row_to_session(r) for r in rows], total

    async def cancel(self, session_id: str) -> dict[str, Any]:
        current = await self.get(session_id)
        if current["status"] in {
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
            SessionStatus.CANCELLED.value,
        }:
            return current
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # The task may have crossed its terminal commit immediately before the
        # cancellation request won the race. Never relabel a genuinely finished
        # session as cancelled.
        current = await self.get(session_id)
        if current["status"] in {
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
        }:
            return current
        file_row = await db.fetchone(
            "SELECT COUNT(*) AS total, COALESCE(SUM(analyzed), 0) AS analyzed "
            "FROM files WHERE session_id = ?",
            (session_id,),
        )
        finding_row = await db.fetchone(
            "SELECT COUNT(*) AS total FROM findings WHERE session_id = ?",
            (session_id,),
        )
        file_total = int(file_row["total"]) if file_row else 0
        analyzed_total = int(file_row["analyzed"]) if file_row else 0
        finding_total = int(finding_row["total"]) if finding_row else 0
        await self._update(
            session_id,
            status=SessionStatus.CANCELLED,
            message="Dibatalkan operator",
            percent=100,
            files_listed=file_total,
            files_analyzed=min(analyzed_total, file_total),
            findings_count=finding_total,
        )
        self._active_device = None
        return await self.get(session_id)

    async def retry_agent_bootstrap(
        self,
        session_id: str,
        device_id: str,
    ) -> AgentRuntimeRecord:
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            raise KeyError("Session not found")
        if (
            row["device_type"] != DeviceType.ANDROID.value
            or row["device_id"] != device_id
            or device_id.startswith("sim-")
            or device_id.startswith("zip:")
        ):
            raise ValueError("Sesi bukan sesi Android live untuk perangkat tersebut.")

        async def on_progress(
            phase: SessionStatus,
            percent: float,
            message: str,
            **fields: Any,
        ) -> None:
            await self._update(
                session_id,
                status=phase,
                percent=percent,
                message=message,
                **fields,
            )

        from app.acquisition.bootstrap import agent_bootstrap
        from app.acquisition.bootstrap_contracts import special_access_for_inventory_mode

        plan = analysis_plan_from_progress(json.loads(row["progress_json"] or "{}"))
        required_access, optional_access = special_access_for_inventory_mode(
            str(row["mode"]),
            require_accessibility=plan.includes_social,
        )

        return await agent_bootstrap.bootstrap(
            session_id=session_id,
            serial=device_id,
            request_id=current_request_id(),
            on_progress=on_progress,
            required_special_access=required_access,
            optional_special_access=optional_access,
        )

    async def _update(
        self,
        session_id: str,
        *,
        status: SessionStatus | None = None,
        percent: float | None = None,
        message: str | None = None,
        timing_patch: dict | None = None,
        recommendation: str | None = None,
        error: str | None = None,
        **progress_fields: Any,
    ) -> None:
        async with self._update_lock:
            row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
            if not row:
                return
            progress = json.loads(row["progress_json"])
            timing = json.loads(row["timing_json"])
            previous_phase = str(progress.get("phase") or "")
            if status:
                progress["phase"] = status.value
            if percent is not None:
                # Bootstrap has its own 0..100 scale. Once it reaches READY,
                # acquisition must be allowed to enter the pipeline's range
                # instead of inheriting a permanent 100%. Preserve monotonic
                # progress for ordinary phase transitions.
                phase_changed = status is not None and status.value != previous_phase
                next_percent = float(percent)
                current_percent = float(progress.get("percent", 0))
                reset_completed_subphase = (
                    phase_changed and current_percent >= 100.0 and next_percent < 100.0
                )
                progress["percent"] = round(
                    next_percent
                    if reset_completed_subphase
                    else max(current_percent, next_percent),
                    1,
                )
            if message is not None:
                progress["message"] = message
            for k, v in progress_fields.items():
                progress[k] = v
                timing_key = _PROGRESS_TIMING_KEYS.get(k)
                if timing_key is not None:
                    try:
                        timing[timing_key] = round(float(v), 1)
                    except (TypeError, ValueError):
                        pass
            if timing_patch:
                timing.update(timing_patch)

            await db.execute(
                """
                UPDATE sessions SET
                    status = ?,
                    progress_json = ?,
                    timing_json = ?,
                    recommendation = COALESCE(?, recommendation),
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value if status else row["status"],
                    json.dumps(progress),
                    json.dumps(timing),
                    recommendation,
                    error if error is not None else row["error"],
                    utcnow(),
                    session_id,
                ),
            )

    async def _run_pipeline(self, session_id: str, req: StartSessionRequest) -> None:
        wall0 = time.perf_counter()
        plan = req.analysis_plan()
        uses_android_agent = False
        ios_restore_usb = req.device_type == DeviceType.IOS
        live_analysis_ms = 0.0
        try:

            async def on_progress(phase: SessionStatus, percent: float, message: str, **kw: Any) -> None:
                nonlocal live_analysis_ms
                if "live_analysis_ms" in kw:
                    live_analysis_ms = max(
                        live_analysis_ms,
                        float(kw["live_analysis_ms"]),
                    )
                await self._update(session_id, status=phase, percent=percent, message=message, **kw)

            t0 = time.perf_counter()
            live_ios = _live_ios_request(req)
            if live_ios:
                await on_progress(
                    SessionStatus.DETECTING,
                    3,
                    "Memastikan iPhone USB di WSL…",
                )
                await acq.ensure_iphone_on_wsl(force=True, reattach=True)
                await on_progress(
                    SessionStatus.DETECTING,
                    3,
                    "Memeriksa lockdownd iPhone…",
                )
                await acq.ensure_iphone_lockdown(udid=req.device_id)
            await on_progress(SessionStatus.DETECTING, 3, "Mendeteksi perangkat…")
            devices = await acq.detect_devices(
                include_simulators=settings.lab_demo_mode,
                reattach_usb=live_ios,
            )
            t_detect = (time.perf_counter() - t0) * 1000
            await self._update(session_id, timing_patch={"t_detect_ms": round(t_detect, 1)})

            device_id = req.device_id or "sim-android-01"
            matched = next((d for d in devices if d.device_id == device_id), None)
            simulated = bool(
                req.force_simulated
                or device_id.startswith("sim-")
                or (matched.simulated if matched else False)
            )
            device_type = matched.device_type if matched else req.device_type
            if device_type == DeviceType.SIMULATED:
                device_type = DeviceType.ANDROID if "android" in device_id else DeviceType.IOS
            uses_android_agent = bool(
                settings.android_agent_enabled
                and device_type == DeviceType.ANDROID
                and not simulated
            )
            ios_restore_usb = device_type == DeviceType.IOS and not simulated

            staging, pulled, t_acq, method = await acq.acquire_dispatch(
                session_id=session_id,
                device_id=device_id,
                device_type=device_type,
                simulated=simulated,
                mode=req.mode,
                scenario=req.scenario,
                file_count=req.file_count,
                on_progress=on_progress,
                review_candidates=req.review_candidates,
                analysis_plan=plan,
            )
            await self._update(
                session_id,
                timing_patch={"t_acquire_ms": round(t_acq, 1)},
                files_pulled=pulled,
                files_listed=pulled,
                acquisition_method=method,
            )

            indexed, t_idx = await acq.index_staging(session_id, staging, on_progress)
            await self._update(
                session_id,
                timing_patch={"t_index_ms": round(t_idx, 1)},
                files_indexed=indexed,
            )

            analyzed, findings_count, t_ai, stats = await ai.analyze_session(
                session_id, staging, req.mode, on_progress
            )
            await self._update(
                session_id,
                timing_patch={"t_analyze_ms": round(live_analysis_ms + t_ai, 1)},
                files_analyzed=analyzed,
                findings_count=findings_count,
                analysis_stats=stats,
            )

            t_total = (time.perf_counter() - wall0) * 1000
            from app.services.recommendation import apply_recommendation

            # Temuan pending → MENUNGGU REVIEW; TIDAK LULUS hanya setelah confirm
            recommendation = await apply_recommendation(session_id)
            await self._update(
                session_id,
                status=SessionStatus.COMPLETED,
                percent=100,
                message="Selesai",
                timing_patch={"t_total_ms": round(t_total, 1)},
                recommendation=recommendation,
                findings_count=findings_count,
            )
            try:
                await rpt.save_session_report(session_id)
            except Exception:
                pass
        except asyncio.CancelledError:
            await self._update(
                session_id,
                status=SessionStatus.CANCELLED,
                percent=100,
                message="Dibatalkan",
                error="cancelled",
            )
            raise
        except AcquisitionError as exc:
            await self._update(
                session_id,
                status=SessionStatus.FAILED,
                percent=100,
                message="Gagal",
                error=exc.public_message,
                agent_error_category=exc.category.value,
                agent_retryable=exc.retryable,
            )
        except Exception as exc:
            await self._update(
                session_id,
                status=SessionStatus.FAILED,
                percent=100,
                message="Gagal",
                error=str(exc),
            )
        finally:
            if uses_android_agent:
                from app.acquisition.bootstrap import agent_bootstrap

                await agent_bootstrap.teardown(session_id, current_request_id())
            if ios_restore_usb:
                await acq.ensure_iphone_on_wsl(force=True)
            self._active_device = None
            self._tasks.pop(session_id, None)

    async def _run_zip_pipeline(
        self,
        session_id: str,
        zip_bytes: bytes,
        original_name: str,
        mode: AcquisitionMode,
        plan: AnalysisPlan,
    ) -> None:
        wall0 = time.perf_counter()
        try:

            async def on_progress(phase: SessionStatus, percent: float, message: str, **kw: Any) -> None:
                await self._update(session_id, status=phase, percent=percent, message=message, **kw)

            await on_progress(SessionStatus.DETECTING, 2, "Mode ZIP — lewati deteksi perangkat…")
            await self._update(session_id, timing_patch={"t_detect_ms": 0.0})

            staging, pulled, t_acq, method = await acq.acquire_zip_dispatch(
                session_id=session_id,
                zip_bytes=zip_bytes,
                mode=mode,
                on_progress=on_progress,
                original_name=original_name,
                analysis_plan=plan,
            )
            await self._update(
                session_id,
                timing_patch={"t_acquire_ms": round(t_acq, 1)},
                files_pulled=pulled,
                files_listed=pulled,
                acquisition_method=method,
            )

            indexed, t_idx = await acq.index_staging(session_id, staging, on_progress)
            await self._update(
                session_id,
                timing_patch={"t_index_ms": round(t_idx, 1)},
                files_indexed=indexed,
            )

            analyzed, findings_count, t_ai, stats = await ai.analyze_session(
                session_id, staging, mode, on_progress
            )
            await self._update(
                session_id,
                timing_patch={"t_analyze_ms": round(t_ai, 1)},
                files_analyzed=analyzed,
                findings_count=findings_count,
                analysis_stats=stats,
            )

            t_total = (time.perf_counter() - wall0) * 1000
            from app.services.recommendation import apply_recommendation

            recommendation = await apply_recommendation(session_id)
            await self._update(
                session_id,
                status=SessionStatus.COMPLETED,
                percent=100,
                message="Selesai (ZIP)",
                timing_patch={"t_total_ms": round(t_total, 1)},
                recommendation=recommendation,
                findings_count=findings_count,
            )
            try:
                await rpt.save_session_report(session_id)
            except Exception:
                pass
        except asyncio.CancelledError:
            await self._update(
                session_id,
                status=SessionStatus.CANCELLED,
                percent=100,
                message="Dibatalkan",
                error="cancelled",
            )
            raise
        except Exception as exc:
            await self._update(
                session_id,
                status=SessionStatus.FAILED,
                percent=100,
                message="Gagal",
                error=str(exc),
            )
        finally:
            self._active_device = None
            self._tasks.pop(session_id, None)


sessions = SessionManager()
