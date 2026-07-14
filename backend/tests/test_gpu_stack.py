"""GPU stack unit tests (no heavy weights required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import config
from app.services.gpu_stack import types
from app.services.gpu_stack import audio_whisper, get_stack_status, clear_stack_cache


@pytest.mark.unit
def test_moderation_hit_as_finding():
    hit = types.ModerationHit(
        category="konten_audio",
        label="Audio/lirik indikasi: provokasi",
        confidence=0.82,
        layer_origin="L4",
        evidence="contoh lirik",
        backend="whisper",
    )
    f = hit.as_finding()
    assert f["layer_origin"] == "L4"
    assert "[whisper]" in f["evidence"]
    assert f["confidence"] == 0.82


@pytest.mark.unit
def test_stack_status_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    clear_stack_cache()
    st = get_stack_status()
    assert st.enabled is True
    for key in ("video", "image", "reason", "audio", "ocr"):
        assert key in st.backends
        assert "name" in st.backends[key]
    clear_stack_cache()


@pytest.mark.unit
def test_whisper_skips_without_enable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(config.settings, "gpu_stack_enabled", False)
    monkeypatch.setattr(config.settings, "gpu_whisper_enabled", False)
    fake = tmp_path / "a.mp4"
    fake.write_bytes(b"not-a-real-video")
    assert audio_whisper.moderate(fake) == []
