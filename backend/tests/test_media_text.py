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
