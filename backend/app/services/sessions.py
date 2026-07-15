from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.db import db, row_to_session, utcnow
from app.models.schemas import AcquisitionMode, DeviceType, Scenario, SessionStatus, StartSessionRequest
from app.services import acquisition as acq
from app.services import analysis as ai
from app.services import reports as rpt


class SessionManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._active_device: str | None = None

    async def create_and_run(self, req: StartSessionRequest) -> dict[str, Any]:
        async with self._lock:
            active = await db.fetchone(
                """
                SELECT id FROM sessions
                WHERE status IN ('pending','detecting','acquiring','indexing','analyzing')
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
            label = req.label or f"Sesi {device_id}"
            now = utcnow()
            progress = acq.empty_progress(SessionStatus.PENDING)
            timing = acq.empty_timing()

            await db.execute(
                """
                INSERT INTO sessions (
                    id, device_id, device_type, label, mode, scenario, status,
                    progress_json, timing_json, recommendation, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    now,
                    now,
                ),
            )
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
    ) -> dict[str, Any]:
        async with self._lock:
            active = await db.fetchone(
                """
                SELECT id FROM sessions
                WHERE status IN ('pending','detecting','acquiring','indexing','analyzing')
                LIMIT 1
                """
            )
            if active:
                raise RuntimeError(
                    "Sesi lain masih berjalan. Selesaikan / batalkan dulu (satu perangkat per sesi)."
                )

            session_id = str(uuid.uuid4())
            device_id = f"zip:{original_name[:40]}"
            session_label = label or f"ZIP · {original_name}"
            now = utcnow()
            progress = acq.empty_progress(SessionStatus.PENDING)
            timing = acq.empty_timing()

            await db.execute(
                """
                INSERT INTO sessions (
                    id, device_id, device_type, label, mode, scenario, status,
                    progress_json, timing_json, recommendation, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    now,
                    now,
                ),
            )
            self._active_device = device_id
            task = asyncio.create_task(
                self._run_zip_pipeline(session_id, zip_bytes, original_name, mode)
            )
            self._tasks[session_id] = task
            return await self.get(session_id)

    async def get(self, session_id: str) -> dict[str, Any]:
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not row:
            raise KeyError("Session not found")
        return row_to_session(row)

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
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
        await self._update(
            session_id,
            status=SessionStatus.CANCELLED,
            message="Dibatalkan operator",
            percent=100,
        )
        self._active_device = None
        return await self.get(session_id)

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
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not row:
            return
        progress = json.loads(row["progress_json"])
        timing = json.loads(row["timing_json"])
        if status:
            progress["phase"] = status.value
        if percent is not None:
            progress["percent"] = round(percent, 1)
        if message is not None:
            progress["message"] = message
        for k, v in progress_fields.items():
            progress[k] = v
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
        try:

            async def on_progress(phase: SessionStatus, percent: float, message: str, **kw: Any) -> None:
                await self._update(session_id, status=phase, percent=percent, message=message, **kw)

            t0 = time.perf_counter()
            await on_progress(SessionStatus.DETECTING, 3, "Mendeteksi perangkat…")
            devices = await acq.detect_devices(include_simulators=settings.lab_demo_mode)
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

            staging, pulled, t_acq, method = await acq.acquire_dispatch(
                session_id=session_id,
                device_id=device_id,
                device_type=device_type,
                simulated=simulated,
                mode=req.mode,
                scenario=req.scenario,
                file_count=req.file_count,
                on_progress=on_progress,
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
                timing_patch={"t_analyze_ms": round(t_ai, 1)},
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

    async def _run_zip_pipeline(
        self,
        session_id: str,
        zip_bytes: bytes,
        original_name: str,
        mode: AcquisitionMode,
    ) -> None:
        wall0 = time.perf_counter()
        try:

            async def on_progress(phase: SessionStatus, percent: float, message: str, **kw: Any) -> None:
                await self._update(session_id, status=phase, percent=percent, message=message, **kw)

            await on_progress(SessionStatus.DETECTING, 2, "Mode ZIP — lewati deteksi perangkat…")
            await self._update(session_id, timing_patch={"t_detect_ms": 0.0})

            staging, pulled, t_acq, method = await acq.acquire_from_zip(
                session_id,
                zip_bytes,
                on_progress=on_progress,
                original_name=original_name,
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
