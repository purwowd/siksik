"""Hash-cache engine fingerprint."""

from __future__ import annotations

import pytest

from app.core import config
from app.services import hash_cache


@pytest.mark.unit
def test_engine_fingerprint_changes_with_ocr_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    a = hash_cache.engine_fingerprint()
    monkeypatch.setattr(config.settings, "ocr_enabled", True)
    b = hash_cache.engine_fingerprint()
    assert a != b


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_cache_list_is_miss(monkeypatch: pytest.MonkeyPatch):
    stored = {"x": None}

    async def fake_fetchone(sql, params=()):
        class R(dict):
            def __getitem__(self, k):
                return super().__getitem__(k)

        return R(result_json='[{"label":"old"}]')

    async def fake_execute(*a, **k):
        return None

    monkeypatch.setattr(hash_cache.db, "fetchone", fake_fetchone)
    hit = await hash_cache.get_cached("abc")
    assert hit is None  # legacy list invalidated
