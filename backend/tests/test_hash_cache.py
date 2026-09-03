"""Hash-cache engine fingerprint."""

from __future__ import annotations

import json

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
    assert "v20" in first
    assert "qwen_decoder=generated-tokens-v1" in first
    assert "qwen_input=max-edge-v1" in first
    assert "qwen_parser=assistant-answer-v1" in first
    assert "qwen_prompt=indonesian-category-contract-json-v6-candidate-decisions" in first


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
    assert "general-category-contracts-v5" in first
    assert "public-figure-context-only-v2" in first


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


@pytest.mark.unit
def test_ocr_stage_fingerprint_survives_reasoning_changes(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 1280)
    monkeypatch.setattr(config.settings, "gpu_qwen_model", "models/qwen-a")
    first = hash_cache.ocr_stage_fingerprint()

    monkeypatch.setattr(config.settings, "gpu_qwen_model", "models/qwen-b")
    assert hash_cache.ocr_stage_fingerprint() == first

    monkeypatch.setattr(config.settings, "ocr_max_edge_px", 1600)
    assert hash_cache.ocr_stage_fingerprint() != first


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stage_cache_rejects_a_different_stage_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
):
    payload = {
        "_stage": "ocr-text",
        "_fingerprint": "ocr-v1",
        "value": {"text": "teks poster", "backend": "paddleocr"},
    }

    async def fake_fetchone(_sql, _params=()):
        return {"result_json": json.dumps(payload)}

    monkeypatch.setattr(hash_cache.db, "fetchone", fake_fetchone)

    assert await hash_cache.get_stage_cached(
        "content-hash",
        stage="ocr-text",
        fingerprint="ocr-v1",
    ) == payload["value"]
    assert await hash_cache.get_stage_cached(
        "content-hash",
        stage="ocr-text",
        fingerprint="ocr-v2",
    ) is None
