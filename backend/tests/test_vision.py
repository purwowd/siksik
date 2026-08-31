"""Unit tests for L3 vision heuristics."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import vision as vis


@pytest.mark.unit
def test_vision_status_keys():
    st = vis.vision_status()
    assert "pillow" in st
    assert "ffmpeg" in st
    assert "torch_cuda" in st
    assert "nudity" in st
    assert "sd_detector" in st


@pytest.mark.unit
def test_analyze_image_with_pillow(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    # Strong red high-contrast poster-like image + risky filename
    img = Image.new("RGB", (200, 200), (220, 20, 20))
    path = tmp_path / "poster_provokasi_demo.jpg"
    img.save(path)
    findings = vis.analyze_image_file(path)
    assert findings
    assert findings[0]["layer_origin"] == "L3"


@pytest.mark.unit
def test_safe_image_no_false_alarm(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (200, 200), (180, 180, 180))
    path = tmp_path / "family_photo.jpg"
    img.save(path)
    findings = vis.analyze_image_file(path)
    assert findings == []


@pytest.mark.unit
def test_benign_text_heavy_image_skips_visual_and_qwen(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    from app.core import config
    from app.services import content_visual, media_text
    from app.services.gpu_stack import reason_qwen

    image = tmp_path / "settings-screen.png"
    Image.new("RGB", (640, 960), (245, 245, 245)).save(image)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("benign text-heavy fast path must not run a visual model")

    monkeypatch.setattr(config.settings, "content_visual_skip_text_heavy_without_signal", True)
    monkeypatch.setattr(media_text, "looks_like_text_heavy_image", lambda *_a, **_k: True)
    monkeypatch.setattr(content_visual, "analyze_image", unexpected)
    monkeypatch.setattr(reason_qwen, "moderate_image_decision", unexpected)

    findings = vis.analyze_lightweight_image_file(
        image,
        precomputed_ocr_text="Pengaturan akun dan privasi",
        precomputed_ocr_backend="test",
        include_reasoning=True,
        origin_hint="screenshot",
    )

    assert findings == []


@pytest.mark.unit
def test_qwen_disabled_never_runs_for_ambiguous_visual(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    from app.core import config
    from app.services import content_visual, media_text
    from app.services.gpu_stack import reason_qwen

    image = tmp_path / "ambiguous.jpg"
    Image.new("RGB", (320, 240), (120, 80, 160)).save(image)
    candidate = {
        "category": "political_meme",
        "label": "Meme politik",
        "confidence": 0.8,
        "layer_origin": "L3",
        "visual_confirmation": "ambiguous",
        "evidence": "[visual-candidate:test] ambiguous",
    }

    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_qwen_enabled", False)
    monkeypatch.setattr(media_text, "looks_like_text_heavy_image", lambda *_a, **_k: False)
    monkeypatch.setattr(content_visual, "analyze_image", lambda _path: [candidate])
    monkeypatch.setattr(
        reason_qwen,
        "moderate_image_decision",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Qwen must stay off")),
    )

    findings = vis.analyze_lightweight_image_file(
        image,
        include_reasoning=True,
    )

    assert findings == []


@pytest.mark.unit
def test_video_reuses_one_shared_keyframe_set(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from app.core import config
    from app.services import gpu_stack, nudity

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"ftyp")
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    frames = []
    for index in range(12):
        frame = frame_dir / f"frame_{index:02d}.jpg"
        frame.write_bytes(b"frame")
        frames.append(frame)

    calls = {"extract": 0, "nudity": 0, "gpu": 0}

    def fake_extract(_path, max_frames=3):
        calls["extract"] += 1
        assert max_frames >= 12
        return list(frames)

    def fake_nudity(_path, values):
        calls["nudity"] += 1
        assert values == frames
        return SimpleNamespace(findings=(), cacheable=True)

    def fake_gpu(_path, *, frames=None):
        calls["gpu"] += 1
        assert frames is not None
        assert len(frames) == config.settings.gpu_video_keyframes
        return []

    monkeypatch.setattr(config.settings, "gpu_video_keyframes", 5)
    monkeypatch.setattr(config.settings, "nudity_video_frames_full", 12)
    monkeypatch.setattr(gpu_stack, "stack_enabled", lambda: True)
    monkeypatch.setattr(vis, "extract_video_keyframes", fake_extract)
    monkeypatch.setattr(nudity, "analyze_video_frames_result", fake_nudity)
    monkeypatch.setattr(gpu_stack, "analyze_video_gpu", fake_gpu)

    outcome = vis.analyze_video_file_result(video)

    assert outcome.cacheable is True
    assert calls == {"extract": 1, "nudity": 1, "gpu": 1}
