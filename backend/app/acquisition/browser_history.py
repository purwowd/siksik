from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.adb import AsyncAdbTransport, resolve_agent_forward_host
from app.acquisition.browser_cdp import (
    BrowserHistoryItem,
    CHROME_PACKAGE_ACTIVITY,
    DEVTOOLS_SOCKET,
    collect_chrome_history,
    wait_devtools_ready,
)
from app.acquisition.errors import AcquisitionError
from app.acquisition.time_scope import build_time_scope
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.browser_history")

STAGING_FULL = "browser_history_full"
STAGING_PARTIAL = "browser_history_partial"
SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_file_stem(value: str) -> str:
    cleaned = SAFE_ID.sub("_", value).strip("._")
    return (cleaned or "record")[:80]


def _item_inside_window(item: BrowserHistoryItem, not_before: datetime) -> bool:
    if not item.observed_at:
        return True
    try:
        parsed = datetime.fromisoformat(item.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed >= not_before


def _write_item(directory: Path, item: BrowserHistoryItem) -> Path:
    digest = hashlib.sha256(item.record_id.encode("utf-8")).hexdigest()[:16]
    path = directory / f"{_safe_file_stem(item.record_id)}-{digest}.json"
    album = (
        "Riwayat Browser (lengkap)"
        if item.history_tier == "full"
        else "Riwayat Browser (sebagian)"
    )
    display_name = (
        item.title or item.search_query or item.url or item.source_label or path.name
    )[:180]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": directory.name,
        "history_tier": item.history_tier,
        "url": item.url,
        "title": item.title,
        "observed_at": item.observed_at,
        "visit_count": item.visit_count,
        "evidence_type": item.evidence_type,
        "source_label": item.source_label,
        "search_query": item.search_query,
        "normalized_text": item.preview_text(),
        "preview_text": item.preview_text(),
        "display_name": display_name,
        "album": album,
        "captured_at": item.observed_at,
        "access_count": item.visit_count or 0,
        "extra": item.extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class BrowserHistoryAcquisitionService:
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
    ) -> int:
        full_dir = staging / STAGING_FULL
        partial_dir = staging / STAGING_PARTIAL
        full_dir.mkdir(parents=True, exist_ok=True)
        partial_dir.mkdir(parents=True, exist_ok=True)
        if simulated:
            return self._write_simulated(full_dir, partial_dir)

        if on_progress:
            await on_progress(
                SessionStatus.ACQUIRING,
                61.0,
                "Mengambil riwayat browser Chrome (CDP)…",
                acquisition_method="chrome_cdp",
            )
        time_scope = build_time_scope(mode, reference=reference)
        transport = AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=min(settings.adb_command_timeout_s, 30.0),
        )
        host_port: int | None = None
        t0 = time.perf_counter()
        try:
            try:
                await transport.start_activity(
                    serial,
                    settings.browser_history_chrome_activity or CHROME_PACKAGE_ACTIVITY,
                    {},
                    timeout=20.0,
                )
            except AcquisitionError:
                logger.info(
                    "browser_history_chrome_launch_skipped",
                    extra={"session_id": session_id, "request_id": request_id},
                )
            host_port = await transport.create_abstract_forward(
                serial,
                settings.browser_history_devtools_socket or DEVTOOLS_SOCKET,
            )
            host = resolve_agent_forward_host()
            ready = await wait_devtools_ready(
                host, host_port, attempts=12
            )
            if not ready:
                logger.warning(
                    "browser_history_devtools_not_ready",
                    extra={"session_id": session_id, "request_id": request_id},
                )
                if on_progress:
                    await on_progress(
                        SessionStatus.ACQUIRING,
                        62.0,
                        "Riwayat browser dilewati: Chrome DevTools belum siap",
                        acquisition_method="chrome_cdp",
                    )
                return 0
            items = await collect_chrome_history(
                host,
                host_port,
                timeout=settings.browser_history_timeout_s,
            )
        except (AcquisitionError, OSError, ConnectionError) as exc:
            logger.warning(
                "browser_history_unavailable",
                extra={
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": exc.__class__.__name__,
                },
            )
            if on_progress:
                await on_progress(
                    SessionStatus.ACQUIRING,
                    62.0,
                    "Riwayat browser dilewati; akuisisi utama tetap dilanjutkan",
                    acquisition_method="chrome_cdp",
                )
            return 0
        finally:
            if host_port is not None:
                await transport.remove_forward(serial, host_port)

        written = 0
        seen: set[tuple[str, str | None, str]] = set()
        for item in items:
            if not _item_inside_window(item, time_scope.not_before):
                continue
            key = (item.history_tier, item.url, item.evidence_type)
            if key in seen:
                continue
            seen.add(key)
            target = full_dir if item.history_tier == "full" else partial_dir
            path = _write_item(target, item)
            written += 1

        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "browser_history_complete",
            extra={
                "session_id": session_id,
                "items_saved": written,
                "duration_ms": round(duration_ms, 1),
            },
        )
        if on_progress:
            await on_progress(
                SessionStatus.ACQUIRING,
                63.0,
                f"Riwayat browser: {written} rekaman",
                files_pulled=written,
                acquisition_method="chrome_cdp",
            )
        return written

    def _write_simulated(self, full_dir: Path, partial_dir: Path) -> int:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        samples = [
            BrowserHistoryItem(
                record_id="sim-full-1",
                history_tier="full",
                url="https://example.test/search?q=satria",
                title="Contoh pencarian",
                observed_at=now,
                visit_count=4,
                evidence_type="tab_navigation",
                source_label="Simulasi CDP",
                search_query="satria",
                extra={},
            ),
            BrowserHistoryItem(
                record_id="sim-partial-1",
                history_tier="partial",
                url="https://example.test/",
                title="Asal situs",
                observed_at=now,
                visit_count=1,
                evidence_type="site_engagement",
                source_label="Simulasi CDP",
                search_query=None,
                extra={},
            ),
        ]
        count = 0
        for item in samples:
            directory = full_dir if item.history_tier == "full" else partial_dir
            path = _write_item(directory, item)
            count += 1
        return count
