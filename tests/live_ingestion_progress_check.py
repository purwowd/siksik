from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.acquisition import live_ingestion
from app.core.db import Database
from app.models.schemas import AcquisitionMode, SessionStatus


class FakeRecord:
    def __init__(self, session_id: str, crawl_id: str, record_id: str) -> None:
        self.siksik_session_id = session_id
        self.crawl_id = crawl_id
        self.record_id = record_id
        self.source_kind = "visible_ui"
        self.source_app = "com.instagram.android"
        self.observed_at = "2026-07-20T00:00:00Z"
        self.metadata = SimpleNamespace(social_scope="own_profile")

    def model_dump_json(self, **_: object) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "siksik_session_id": self.siksik_session_id,
                "crawl_id": self.crawl_id,
                "record_id": self.record_id,
                "source_kind": self.source_kind,
                "source_app": self.source_app,
                "observed_at": self.observed_at,
                "normalized_text": "profile preview",
                "metadata": {"social_scope": "own_profile"},
            },
            separators=(",", ":"),
        )


async def run_check() -> None:
    with tempfile.TemporaryDirectory(prefix="siksik-live-progress-") as directory:
        root = Path(directory)
        database = Database(root / "progress.db")
        await database.connect()
        original_db = live_ingestion.db
        original_settings = live_ingestion.settings
        original_analyze = live_ingestion.analysis.analyze_session
        events: list[dict[str, object]] = []
        analysis_started = False
        try:
            await database.execute(
                """
                INSERT INTO sessions (
                    id, device_id, device_type, label, mode, scenario, status,
                    progress_json, timing_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "session-live-progress",
                    "device",
                    "android",
                    "Device",
                    "full",
                    "clean",
                    "acquiring",
                    "{}",
                    "{}",
                    "2026-07-20T00:00:00Z",
                    "2026-07-20T00:00:00Z",
                ),
            )
            live_ingestion.db = database
            live_ingestion.settings = SimpleNamespace(staging_dir=root / "staging")

            async def on_progress(
                phase: SessionStatus,
                percent: float,
                message: str,
                **fields: object,
            ) -> None:
                events.append(
                    {
                        "phase": phase,
                        "percent": percent,
                        "message": message,
                        **fields,
                    }
                )

            async def analyze_session(*args: object, **kwargs: object):
                nonlocal analysis_started
                analysis_started = True
                if not events or events[0].get("files_pulled") != 1:
                    raise RuntimeError("ingress progress was not published before analysis")
                return 1, 0, 1.0, {}

            live_ingestion.analysis.analyze_session = analyze_session
            record = FakeRecord(
                "session-live-progress",
                "crawl-live-progress",
                "record-live-progress",
            )
            item = SimpleNamespace(
                record=record,
                candidate=SimpleNamespace(record_id=record.record_id, selected=True),
            )
            result = await live_ingestion.LiveSelectedIngestor().ingest(
                session_id=record.siksik_session_id,
                crawl_id=record.crawl_id,
                records=[item],
                mode=AcquisitionMode.FULL,
                on_progress=on_progress,
            )
            row = await database.fetchone(
                "SELECT COUNT(*) AS total, COALESCE(SUM(analyzed), 0) AS analyzed "
                "FROM files WHERE session_id = ?",
                (record.siksik_session_id,),
            )
            if not analysis_started or result != (1, 0):
                raise RuntimeError("incremental analysis was not invoked")
            if row is None or int(row["total"]) != 1 or int(row["analyzed"]) != 0:
                raise RuntimeError("live selected record was not staged consistently")
            if events[0].get("files_analyzed") != 0 or events[0].get("findings_count") != 0:
                raise RuntimeError("ingress progress reported uncommitted analysis")
        finally:
            live_ingestion.analysis.analyze_session = original_analyze
            live_ingestion.settings = original_settings
            live_ingestion.db = original_db
            await database.close()


if __name__ == "__main__":
    asyncio.run(run_check())
    print("live_ingestion_progress_check: ok")
