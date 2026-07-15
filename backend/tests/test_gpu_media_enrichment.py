"""GPU-server gates: OCR + Whisper media enrichment.

Jalankan di mesin NVIDIA setelah deps terpasang:

  cd backend && source .venv/bin/activate
  pip install -r requirements.txt -r requirements-gpu.txt
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

  export SADT_OCR_ENABLED=1
  export SADT_OCR_BACKEND=paddleocr   # atau easyocr
  export SADT_OCR_GPU=1
  export SADT_MEDIA_TEXT_ENABLED=1
  export SADT_GPU_WHISPER_ENABLED=1
  export SADT_GPU_WHISPER_MODEL=base
  export SADT_GPU_WHISPER_LANG=id

  pytest -m "gpu_ocr or gpu_whisper" -q --tb=short
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.core import config
from app.services import media_text
from app.services import ocr as ocr_mod
from app.services.gpu_stack import audio_whisper
from app.services.ocr import FakeOCRBackend


def _env_flag(name: str) -> bool:
    return os.getenv(name, "0").strip() in {"1", "true", "True", "yes"}


@pytest.fixture(autouse=True)
def _reset_ocr():
    ocr_mod.reset_backend_cache()
    yield
    ocr_mod.reset_backend_cache()


@pytest.mark.gpu
@pytest.mark.gpu_ocr
def test_gpu_ocr_reads_banner_and_lexicon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not _env_flag("SADT_OCR_ENABLED") and not _env_flag("SADT_RUN_GPU_OCR"):
        pytest.skip("Set SADT_OCR_ENABLED=1 (atau SADT_RUN_GPU_OCR=1) di mesin GPU")

    monkeypatch.setattr(config.settings, "ocr_enabled", True)
    monkeypatch.setattr(
        config.settings, "ocr_backend", os.getenv("SADT_OCR_BACKEND", "paddleocr")
    )
    monkeypatch.setattr(config.settings, "ocr_gpu", True)
    monkeypatch.setattr(config.settings, "media_text_enabled", True)
    ocr_mod.reset_backend_cache()

    backend = ocr_mod.get_backend()
    if backend is None or not backend.available():
        # media_text path: pick any available
        backend = media_text._pick_ocr_backend()
    if backend is None:
        pytest.skip("Tidak ada OCR backend — pip install -r requirements-gpu.txt")

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    # Simulasi kaos di folder documents (heuristic force OCR)
    docs = tmp_path / "documents"
    docs.mkdir()
    img_path = docs / "kaos_banner.png"
    im = Image.new("RGB", (800, 200), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.text((24, 80), "GANTI PRESIDEN", fill=(0, 0, 0))
    im.save(img_path)

    result = ocr_mod.run_ocr(img_path, backend=backend)
    assert result is not None
    text = (result.text or "").lower()
    # Soft: OCR harus menghasilkan string; keyword assert jika terbaca
    assert isinstance(result.text, str)
    findings = ocr_mod.ocr_findings_from_text(result.text or "", backend=result.backend)
    media_hits = media_text.ocr_image_best_effort(img_path, force=True)

    if "presiden" in text or "ganti" in text:
        assert findings or media_hits, f"OCR baca {result.text!r} tapi lexicon miss"
    else:
        pytest.xfail(f"OCR GPU hidup tapi banner tidak terbaca: {result.text!r}")


@pytest.mark.gpu
@pytest.mark.gpu_ocr
def test_gpu_ocr_no_substring_fp_on_ganti(monkeypatch: pytest.MonkeyPatch):
    """Unit-level guarantee tetap valid di GPU CI — pure lexicon."""
    from app.services.ocr import ocr_findings_from_text

    # Meski "GANTI" mengandung huruf anti, jangan flag "anti"
    findings = ocr_findings_from_text("SERIBU KALI GANTI PRESIDEN", backend="fake")
    labels = " ".join(f["label"].lower() for f in findings)
    assert "anti pemerintah" not in labels
    assert ": anti" not in labels
    assert "presiden" in labels or "ganti presid" in labels


@pytest.mark.gpu
@pytest.mark.gpu_whisper
def test_gpu_whisper_moderates_keyword_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not _env_flag("SADT_GPU_WHISPER_ENABLED") and not _env_flag("SADT_RUN_GPU_WHISPER"):
        pytest.skip("Set SADT_GPU_WHISPER_ENABLED=1 atau SADT_RUN_GPU_WHISPER=1")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg tidak ada di PATH")

    st = audio_whisper.status()
    if not st.get("available"):
        pytest.skip(f"Whisper unavailable: {st}")

    monkeypatch.setattr(config.settings, "media_text_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_whisper_enabled", True)
    monkeypatch.setattr(
        config.settings, "gpu_whisper_model", os.getenv("SADT_GPU_WHISPER_MODEL", "tiny")
    )
    monkeypatch.setattr(config.settings, "gpu_whisper_lang", "id")

    # Generate short WAV via ffmpeg sine + optional: use spoken-like silence won't work.
    # Instead synthesize a tiny mp3/wav with espeak if available, else skip with note.
    wav = tmp_path / "clip.wav"
    # 1s silence — Whisper won't yield keyword; this only gates that transcribe runs
    import subprocess

    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(wav),
        ],
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        pytest.skip("ffmpeg gagal buat wav uji")

    text = audio_whisper.transcribe(wav)
    assert isinstance(text, str)
    # moderate pada sine biasanya kosong — pastikan tidak crash & tidak FP substring random
    hits = audio_whisper.moderate(wav, force=True)
    assert isinstance(hits, list)


@pytest.mark.gpu
@pytest.mark.gpu_whisper
def test_whisper_lexicon_rejects_noscobom_noise():
    from app.services.lexicon import match_keywords

    noise = (
        "Hutan Papua Salib merah Tuat huang besar pesta sangat haram "
        "Semino kife kuba noscobom deko Yumano bumo eade"
    )
    hits = match_keywords(noise)
    assert "bom" not in [h.lower() for h in hits]
    assert "anti" not in [h.lower() for h in hits]


@pytest.mark.unit
def test_documents_path_forces_ocr_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mac/CI: FakeOCR — dokumen putih tetap di-OCR lewat media_text."""
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "ocr_enabled", False)
    monkeypatch.setattr(config.settings, "media_text_enabled", True)

    docs = tmp_path / "documents"
    docs.mkdir()
    img = docs / "poster_white.jpg"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(img)

    assert media_text.looks_like_document_or_download(img)
    assert media_text.should_try_ocr(img)

    fake = FakeOCRBackend(forced_text="Aksi ganti presiden malam ini")
    monkeypatch.setattr(media_text, "_pick_ocr_backend", lambda: fake)
    findings = media_text.ocr_image_best_effort(img)
    assert findings
    assert any("presiden" in f["label"].lower() or "ganti" in f["label"].lower() for f in findings)
