from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.acquisition.browser_cdp import (
    chrome_time_to_utc,
    classify_history_tier,
    extract_search_query,
)
from app.acquisition.browser_history import BrowserHistoryAcquisitionService
from app.acquisition.contracts import AcquisitionResult, ProviderKind
from app.core import config
from app.models.schemas import AcquisitionMode, DeviceType, Scenario
from app.services import acquisition as acquisition_service
from app.services.finding_modules import MODULE_SOURCE_SQL, VALID_MODULE_IDS


def test_classify_full_path_versus_origin() -> None:
    assert classify_history_tier("https://news.test/world/id") == "full"
    assert classify_history_tier("https://www.google.com/search?q=satria") == "full"
    assert classify_history_tier("https://example.test/") == "partial"
    assert classify_history_tier("https://example.test") == "partial"
    assert classify_history_tier("https://[*.]example.test") == "partial"
    assert extract_search_query("https://www.google.com/search?q=satria+lab") == "satria lab"


def test_chrome_webkit_epoch_converts_to_utc() -> None:
    # 2020-01-01T00:00:00Z in Chrome microseconds.
    stamp = (1_577_836_800 + 11_644_473_600) * 1_000_000
    assert chrome_time_to_utc(stamp) == "2020-01-01T00:00:00Z"


def test_browser_module_is_registered() -> None:
    assert "browser" in VALID_MODULE_IDS
    sql, _params = MODULE_SOURCE_SQL["browser"]
    assert "browser_history_full" in sql


@pytest.mark.asyncio
async def test_simulated_browser_history_writes_full_and_partial(tmp_path: Path) -> None:
    staging = tmp_path / "session"
    count = await BrowserHistoryAcquisitionService().acquire(
        session_id="session-sim-browser",
        serial="sim-android-01",
        staging=staging,
        mode=AcquisitionMode.QUICK,
        simulated=True,
        on_progress=None,
        request_id="req-1",
        reference=datetime.now(timezone.utc),
    )
    assert count == 2
    assert any((staging / "browser_history_full").glob("*.json"))
    assert any((staging / "browser_history_partial").glob("*.json"))


@pytest.mark.asyncio
async def test_acquire_dispatch_appends_chrome_cdp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_provider(_self, _context):
        return AcquisitionResult(staging, 3, 10.0, "legacy", ProviderKind.ANDROID_LEGACY)

    async def fake_browser(self, **kwargs):
        del self
        assert kwargs["serial"] == "android-live"
        return 4

    async def fake_reference(_session_id: str):
        return datetime(2026, 8, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    monkeypatch.setattr(config.settings, "android_recovery_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", False)
    monkeypatch.setattr(config.settings, "browser_history_enabled", True)
    monkeypatch.setattr(
        "app.acquisition.browser_history.BrowserHistoryAcquisitionService.acquire",
        fake_browser,
    )
    monkeypatch.setattr(
        "app.acquisition.gmail_oauth.session_acquisition_reference",
        fake_reference,
    )

    async def skip_whatsapp(self, **_kwargs):
        del self
        return None

    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.WhatsAppBackupAcquisitionService.acquire",
        skip_whatsapp,
    )

    async def on_progress(*_args, **_kwargs):
        return None

    result = await acquisition_service.acquire_dispatch(
        session_id="session-browser-dispatch",
        device_id="android-live",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )
    assert result == (staging, 7, 10.0, "legacy+chrome_cdp")
