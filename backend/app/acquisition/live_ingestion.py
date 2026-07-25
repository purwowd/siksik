from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.acquisition.agent_client import LiveSelectedRecordV1
from app.acquisition.direct_transfer import CANONICAL_RECORD_MIME, CANONICAL_RECORD_SUFFIX
from app.acquisition.file_identity import stable_file_id
from app.core.config import settings
from app.core.db import db
from app.models.schemas import AcquisitionMode, SessionStatus
from app.services import analysis

class LiveSelectedIngestor:
    async def ingest(
        self,
        *,
        session_id: str,
        crawl_id: str,
        records: list[LiveSelectedRecordV1],
        mode: AcquisitionMode,
        on_progress,
    ) -> tuple[int, int]:
        if not records:
            return await self._totals(session_id)
        staging = settings.staging_dir / session_id
        rows: list[tuple[object, ...]] = []
        for item in records:
            record = item.record
            if (
                record.siksik_session_id != session_id
                or record.crawl_id != crawl_id
                or record.record_id != item.candidate.record_id
                or not item.candidate.selected
            ):
                raise RuntimeError("live selected record is not bound to the active crawl")
            raw = record.model_dump_json(exclude_none=False).encode("utf-8")
            relative_path = (
                f"{record.source_kind}/{record.record_id}{CANONICAL_RECORD_SUFFIX}"
            )
            target = (staging / relative_path).resolve()
            if not target.is_relative_to(staging.resolve()):
                raise RuntimeError("live selected path escaped staging")
            await asyncio.to_thread(self._write_atomic, target, raw)
            digest = hashlib.sha256(raw).hexdigest()
            existing = await db.fetchone(
                "SELECT id FROM files WHERE session_id = ? AND path = ? LIMIT 1",
                (session_id, relative_path),
            )
            file_id = (
                str(existing["id"])
                if existing is not None
                else stable_file_id(session_id, relative_path)
            )
            metadata = {
                "acquisition_method": "android_agent_live_selection",
                "live_selection": True,
                "crawl_id": crawl_id,
                "record_id": record.record_id,
                "source_kind": record.source_kind,
                "source_app": record.source_app,
                "observed_at": record.observed_at,
                "social_scope": (
                    record.metadata.social_scope
                    if record.source_kind == "visible_ui"
                    else None
                ),
            }
            rows.append(
                (
                    file_id,
                    session_id,
                    record.source_kind,
                    relative_path,
                    CANONICAL_RECORD_MIME,
                    len(raw),
                    digest,
                    "pulled",
                    0,
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                )
            )
        async with db.transaction() as conn:
            await conn.executemany(
                """
                INSERT INTO files (
                    id, session_id, source, path, mime, size_bytes, sha256,
                    pull_status, analyzed, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    path = excluded.path,
                    mime = excluded.mime,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    pull_status = excluded.pull_status,
                    meta_json = excluded.meta_json
                """,
                rows,
            )
        file_total, analyzed_total, finding_total = await self._progress_totals(session_id)
        await on_progress(
            SessionStatus.ACQUIRING,
            43.0,
            f"Menerima data selection Android ({file_total} masuk)",
            files_listed=file_total,
            files_pulled=file_total,
            files_indexed=file_total,
            files_analyzed=analyzed_total,
            findings_count=finding_total,
        )
        analyzed, findings, _, _ = await analysis.analyze_session(
            session_id,
            staging,
            mode,
            on_progress,
            progress_status=SessionStatus.ACQUIRING,
            progress_start=43.0,
            progress_end=48.0,
            progress_label="Analisis SIKSIK bertahap",
        )
        return analyzed, findings

    @staticmethod
    def _write_atomic(target: Path, raw: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        try:
            partial.write_bytes(raw)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    async def _totals(session_id: str) -> tuple[int, int]:
        _, analyzed, findings = await LiveSelectedIngestor._progress_totals(session_id)
        return analyzed, findings

    @staticmethod
    async def _progress_totals(session_id: str) -> tuple[int, int, int]:
        file_row = await db.fetchone(
            "SELECT COUNT(*) AS total, COALESCE(SUM(analyzed), 0) AS analyzed "
            "FROM files WHERE session_id = ?",
            (session_id,),
        )
        finding_row = await db.fetchone(
            "SELECT COUNT(*) AS total FROM findings WHERE session_id = ?",
            (session_id,),
        )
        return (
            int(file_row["total"]) if file_row else 0,
            int(file_row["analyzed"]) if file_row else 0,
            int(finding_row["total"]) if finding_row else 0,
        )


live_selected_ingestor = LiveSelectedIngestor()
