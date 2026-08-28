"""Hash-cache engine fingerprint."""

from __future__ import annotations

import pytest

from app.core import config
from app.models.schemas import AcquisitionMode
from app.services import hash_cache


@pytest.mark.unit
def test_engine_fingerprint_changes_with_ocr_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    a = hash_cache.engine_fingerprint()
    monkeypatch.setattr(config.settings, "ocr_enabled", True)
    b = hash_cache.engine_fingerprint()
    assert a != b


@pytest.mark.unit
def test_engine_fingerprint_tracks_qwen_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "gpu_qwen_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_qwen_model", "models/qwen-a")
    monkeypatch.setattr(config.settings, "gpu_qwen_plugin", "")
    first = hash_cache.engine_fingerprint()

    monkeypatch.setattr(config.settings, "gpu_qwen_model", "models/qwen-b")
    second = hash_cache.engine_fingerprint()

    assert first != second
    assert "v16" in first
    assert "qwen_decoder=generated-tokens-v1" in first
    assert "qwen_input=max-edge-v1" in first
    assert "qwen_parser=assistant-answer-v1" in first
    assert "qwen_prompt=indonesian-content-json-v2" in first


@pytest.mark.unit
def test_engine_fingerprint_tracks_qwen_max_edge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "gpu_qwen_max_edge_px", 1280)
    first = hash_cache.engine_fingerprint()
    monkeypatch.setattr(config.settings, "gpu_qwen_max_edge_px", 1600)
    second = hash_cache.engine_fingerprint()

    assert first != second
    assert "qwen_px=1280" in first
    assert "qwen_px=1600" in second


@pytest.mark.unit
def test_engine_fingerprint_tracks_content_taxonomy_and_ocr_language(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config.settings, "ocr_langs", "id,en")
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.70)
    first = hash_cache.engine_fingerprint()

    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.82)
    second = hash_cache.engine_fingerprint()

    assert first != second
    assert "ol=id,en" in first
    assert "id-content-v1" in first
    assert "category-evidence-v1" in first


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


@pytest.mark.unit
def test_engine_fingerprint_separates_quick_and_full_nudity_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config.settings, "nudity_detection_enabled", True)
    quick_token = hash_cache.set_analysis_mode(AcquisitionMode.QUICK)
    try:
        quick = hash_cache.engine_fingerprint()
    finally:
        hash_cache.reset_analysis_mode(quick_token)

    full_token = hash_cache.set_analysis_mode(AcquisitionMode.FULL)
    try:
        full = hash_cache.engine_fingerprint()
    finally:
        hash_cache.reset_analysis_mode(full_token)

    assert "mode=quick" in quick
    assert "mode=full" in full
    assert quick != full
