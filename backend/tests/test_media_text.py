"""Media-text enrichment: screenshot OCR, video ASR + on-screen OCR."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core import config
from app.services import media_text
from app.services.ocr import FakeOCRBackend, ocr_findings_from_text
from app.services.gpu_stack.types import ModerationHit


@pytest.mark.unit
def test_looks_like_chat_screenshot():
    assert media_text.looks_like_chat_or_screenshot(Path("/tmp/Screenshots/IMG_001.jpg"))
    assert media_text.looks_like_chat_or_screenshot(Path("/media/whatsapp/chat.png"))
    assert not media_text.looks_like_chat_or_screenshot(Path("/gallery/IMG_90210.jpg"))
    hashed = Path("/tmp/staging/session/artifacts/deadbeef.jpg")
    assert media_text.looks_like_chat_or_screenshot(
        hashed,
        origin_hint="Pictures/Screenshots Screenshot_20260817.png",
    )
    assert not media_text.looks_like_document_or_download(
        hashed,
        origin_hint="DCIM/Camera IMG_20260817.jpg",
    )


@pytest.mark.unit
def test_gpu_stack_does_not_force_ocr_on_plain_camera_photo(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    monkeypatch.setattr(config.settings, "media_text_enabled", True)
    monkeypatch.setattr(config.settings, "ocr_full_gallery", False)
    image = tmp_path / "DCIM" / "Camera" / "IMG_0001.jpg"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (128, 128), (128, 128, 128)).save(image)

    assert not media_text.should_try_ocr(image)


@pytest.mark.unit
def test_visual_skip_distinguishes_flat_ui_from_color_photo(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    ui = tmp_path / "settings.png"
    Image.new("RGB", (360, 800), (245, 245, 245)).save(ui)
    photo = tmp_path / "protest.jpg"
    canvas = Image.new("RGB", (800, 520), (40, 120, 190))
    draw = ImageDraw.Draw(canvas)
    for index, color in enumerate(("red", "yellow", "green", "blue")):
        draw.rectangle((index * 200, 180, (index + 1) * 200, 520), fill=color)
    canvas.save(photo)

    assert media_text.should_skip_generic_visual_model(ui)
    assert not media_text.should_skip_generic_visual_model(photo)


@pytest.mark.unit
def test_ocr_documents_best_effort(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    monkeypatch.setattr(config.settings, "media_text_enabled", True)

    docs = tmp_path / "documents"
    docs.mkdir()
    img = docs / "kaos.jpg"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(img)

    fake = FakeOCRBackend(forced_text="SERIBU KALI GANTI PRESIDEN")
    monkeypatch.setattr(media_text, "_pick_ocr_backend", lambda: fake)
    findings = media_text.ocr_image_best_effort(img)
    assert findings
    assert any("dokumen" in f["label"] or "presiden" in f["label"].lower() for f in findings)


@pytest.mark.unit
def test_ocr_token_match_partial_phrase():
    findings = ocr_findings_from_text("Ajak makar terhadap negara", backend="fake")
    assert findings
    assert any("makar" in f["label"].lower() for f in findings)


@pytest.mark.unit
def test_ocr_screenshot_best_effort(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    monkeypatch.setattr(config.settings, "media_text_enabled", True)

    shot = tmp_path / "Screenshots" / "chat_001.png"
    shot.parent.mkdir()
    Image.new("RGB", (120, 200), (240, 240, 240)).save(shot)

    fake = FakeOCRBackend(forced_text="Pesan: gulingkan pemerintah malam ini")
    monkeypatch.setattr(media_text, "_pick_ocr_backend", lambda: fake)

    findings = media_text.ocr_image_best_effort(shot)
    assert findings
    assert any("chat/screenshot" in f["label"] or "OCR" in f["label"] for f in findings)


@pytest.mark.unit
def test_video_enrichment_whisper_and_ocr(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "media_text_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_whisper_enabled", True)
    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    monkeypatch.setattr(config.settings, "video_overlay_keyframes", 2)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    frame = tmp_path / "kf_01.jpg"
    Image.new("RGB", (64, 64), (10, 10, 10)).save(frame)

    hit = ModerationHit(
        category="konten_audio",
        label="Audio/lirik indikasi: hasut",
        confidence=0.82,
        layer_origin="L4",
        evidence="... hasut ...",
        backend="whisper",
    )
    mock_mod = MagicMock(return_value=[hit])
    monkeypatch.setattr(
        "app.services.gpu_stack.audio_whisper.moderate",
        mock_mod,
        raising=False,
    )

    # Patch import path used inside analyze_video_enrichment
    import app.services.gpu_stack.audio_whisper as aw

    monkeypatch.setattr(aw, "moderate", mock_mod)

    monkeypatch.setattr(
        "app.services.vision.extract_video_keyframes",
        lambda path, max_frames=3: [frame],
    )
    monkeypatch.setattr(
        "app.services.vision._analyze_pil_image",
        lambda path: [],
    )

    fake = FakeOCRBackend(forced_text="Teks on screen: provokasi massa")
    monkeypatch.setattr(media_text, "_pick_ocr_backend", lambda: fake)

    findings = media_text.analyze_video_enrichment(video)
    labels = " ".join(f["label"] for f in findings).lower()
    assert "hasut" in labels or "audio" in labels
    assert "provokasi" in labels or "on-screen" in labels
