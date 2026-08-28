"""GPU stack unit tests (no heavy weights required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import config
from app.services.gpu_stack import types
from app.services.gpu_stack import audio_whisper, get_stack_status, clear_stack_cache
from app.services.gpu_stack import reason_qwen


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


@pytest.mark.unit
def test_safewatch_plugin_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.services.gpu_stack import video_safewatch

    adapter = tmp_path / "sadt_adapter.py"
    adapter.write_text(
        "def moderate(path):\n"
        "    return [{'category':'anti_pemerintah','label':'plugin hit',"
        "'confidence':0.9,'layer_origin':'L4','evidence':str(path),'backend':'test'}]\n"
    )
    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_safewatch_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_safewatch_model", str(tmp_path))
    monkeypatch.setattr(config.settings, "gpu_safewatch_plugin", "")
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"ftyp")
    hits = video_safewatch.moderate(vid)
    assert len(hits) == 1
    assert hits[0].label == "plugin hit"
    assert hits[0].backend == "test"


@pytest.mark.unit
def test_safewatch_bridge_on_path_keyword(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.services.gpu_stack import video_safewatch

    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_safewatch_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_safewatch_model", "")
    monkeypatch.setattr(config.settings, "gpu_safewatch_plugin", "")
    # Bridge must not go silent when checkpoint "available" either
    vid = tmp_path / "video_makar_demo.mp4"
    vid.write_bytes(b"ftyp")
    hits = video_safewatch.moderate(vid)
    assert hits
    assert "makar" in hits[0].label.lower() or "makar" in hits[0].evidence.lower()


@pytest.mark.unit
def test_qwen_parser_ignores_prompt_when_assistant_says_aman():
    transcript = (
        "system\nKebijakan: cari makar, provokasi, dan radikal.\n"
        "user\nPeriksa gambar ini.\n"
        "assistant\nAMAN"
    )

    hits = reason_qwen._hits_from_text(transcript, layer="L3", backend="test")

    assert hits == []


@pytest.mark.unit
def test_qwen_aman_is_always_zero_findings():
    hits = reason_qwen._hits_from_text(
        "**AMAN**. Tidak ada makar, provokasi, atau radikal.",
        layer="L3",
        backend="test",
    )

    assert hits == []


@pytest.mark.unit
def test_qwen_parser_reads_only_assistant_answer():
    transcript = (
        "<|im_start|>system\nCari makar dan radikal.<|im_end|>\n"
        "<|im_start|>user\nPeriksa gambar.<|im_end|>\n"
        "<|im_start|>assistant\nTerlihat ajakan provokasi.<|im_end|>"
    )

    hits = reason_qwen._hits_from_text(transcript, layer="L3", backend="test")

    assert [hit.label for hit in hits] == ["VL reasoning: provokasi"]
    assert all("makar" not in hit.evidence.lower() for hit in hits)
    assert all("radikal" not in hit.evidence.lower() for hit in hits)


@pytest.mark.unit
def test_qwen_structured_parser_emits_requested_categories_once():
    answer = (
        '{"status":"FLAGGED","detections":['
        '{"category":"political_meme","confidence":0.91,"evidence":"meme presiden"},'
        '{"category":"meme_politik","confidence":0.82,"evidence":"deteksi kedua"},'
        '{"category":"hate_speech","confidence":0.88,"evidence":"ujaran kebencian"}'
        "]}"
    )

    hits = reason_qwen._hits_from_text(answer, layer="L3", backend="test")

    assert [hit.category for hit in hits] == ["political_meme", "hate_speech"]
    assert hits[0].label == "Meme politik"


@pytest.mark.unit
def test_qwen_structured_aman_is_zero_findings():
    hits = reason_qwen._hits_from_text(
        '{"status":"AMAN","detections":[{"category":"extremism","confidence":0.9}]}',
        layer="L3",
        backend="test",
    )

    assert hits == []


@pytest.mark.unit
def test_qwen_malformed_structured_answer_is_not_reinterpreted():
    hits = reason_qwen._hits_from_text(
        '{"status":"FLAGGED","detections":[{"category":"extremism"}',
        layer="L3",
        backend="test",
    )

    assert hits == []


@pytest.mark.unit
def test_qwen_plain_answer_can_still_flag_new_taxonomy():
    hits = reason_qwen._hits_from_text(
        "Terlihat jelas bendera LGBT Pride Month.",
        layer="L3",
        backend="test",
    )

    assert [hit.category for hit in hits] == ["lgbt_content"]


@pytest.mark.unit
def test_qwen_decoder_removes_input_tokens():
    class FakeProcessor:
        decoded_ids = None
        decode_kwargs = None

        def batch_decode(self, ids, **kwargs):
            self.decoded_ids = ids
            self.decode_kwargs = kwargs
            return [" AMAN "]

    processor = FakeProcessor()
    answer = reason_qwen._decode_generated_answer(
        processor,
        generated_ids=[[10, 11, 12, 90, 91]],
        input_ids=[[10, 11, 12]],
    )

    assert processor.decoded_ids == [[90, 91]]
    assert processor.decode_kwargs == {
        "skip_special_tokens": True,
        "clean_up_tokenization_spaces": False,
    }
    assert answer == "AMAN"


@pytest.mark.unit
def test_prepare_qwen_image_downscales(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from PIL import Image

    monkeypatch.setattr(config.settings, "gpu_qwen_max_edge_px", 1280)
    source = tmp_path / "camera.jpg"
    Image.new("RGB", (2000, 1500), (12, 24, 36)).save(source, "JPEG")

    capped, tmp = reason_qwen._prepare_qwen_image(source)
    try:
        assert tmp is not None
        assert capped == tmp
        with Image.open(capped) as image:
            assert image.size == (1280, 960)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_prepare_qwen_image_keeps_small_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from PIL import Image

    monkeypatch.setattr(config.settings, "gpu_qwen_max_edge_px", 1280)
    source = tmp_path / "small.jpg"
    Image.new("RGB", (800, 600), (1, 2, 3)).save(source, "JPEG")

    capped, tmp = reason_qwen._prepare_qwen_image(source)

    assert capped == source
    assert tmp is None


@pytest.mark.unit
def test_moderate_image_sends_capped_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from PIL import Image

    source = tmp_path / "camera.jpg"
    capped = tmp_path / "capped.jpg"
    Image.new("RGB", (64, 64), (8, 8, 8)).save(source, "JPEG")
    Image.new("RGB", (32, 32), (9, 9, 9)).save(capped, "JPEG")
    seen: dict[str, object] = {}

    class Tensorish(list):
        def to(self, _device):
            return self

    class FakeProcessor:
        def apply_chat_template(self, _messages, tokenize=False, add_generation_prompt=True):
            raise RuntimeError("force fallback processor path")

        def __call__(self, **kwargs):
            images = kwargs.get("images") or []
            seen["size"] = images[0].size if images else None
            return {"input_ids": Tensorish([[1, 2, 3]])}

        def batch_decode(self, _ids, **_kwargs):
            return ["AMAN"]

    class FakeModel:
        device = "cpu"

        def generate(self, **_kwargs):
            seen["generated"] = True
            return [[1, 2, 3, 9]]

    def fake_prepare(path: Path) -> tuple[Path, Path | None]:
        seen["source"] = path
        return capped, None

    monkeypatch.setattr(config.settings, "gpu_stack_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_qwen_enabled", True)
    monkeypatch.setattr(config.settings, "gpu_qwen_model", "Qwen/Qwen2.5-VL-3B-Instruct")
    monkeypatch.setattr(config.settings, "gpu_qwen_plugin", "")
    monkeypatch.setattr(reason_qwen, "_prepare_qwen_image", fake_prepare)
    monkeypatch.setattr(reason_qwen, "_try_load", lambda: (FakeModel(), FakeProcessor()))
    monkeypatch.setattr(reason_qwen, "run_plugin", lambda *_args, **_kwargs: None)

    hits = reason_qwen.moderate_image(source)

    assert hits == []
    assert seen.get("generated") is True
    assert seen.get("source") == source
    assert seen.get("size") == (32, 32)
