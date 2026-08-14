"""Explicit-nudity enrichment without opening sensitive media or loading a real model."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from app.core import config
from app.models.schemas import AcquisitionMode
from app.services import analysis, nudity
from app.services.hash_cache import reset_analysis_mode, set_analysis_mode


class FakeDetector:
    def __init__(
        self,
        *,
        single: object | None = None,
        batches: Sequence[object] = (),
        fail: bool = False,
    ) -> None:
        self.single = single if single is not None else []
        self.batches = list(batches)
        self.fail = fail
        self.batch_size: int | None = None

    def detect(self, _path: str):
        if self.fail:
            raise RuntimeError("fake inference failure")
        return self.single

    def detect_batch(self, paths: Sequence[str], batch_size: int = 4):
        if self.fail:
            raise RuntimeError("fake batch failure")
        self.batch_size = batch_size
        assert len(paths) == len(self.batches)
        return self.batches


def explicit(label: str, score: float, box: list[int] | None = None) -> dict:
    return {
        "class": label,
        "score": score,
        "box": box or [1, 2, 30, 40],
    }


@pytest.fixture(autouse=True)
def nudity_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "nudity_detection_enabled", True)
    monkeypatch.setattr(config.settings, "nudity_threshold_anus", 0.50)
    monkeypatch.setattr(config.settings, "nudity_threshold_buttocks", 0.60)
    monkeypatch.setattr(config.settings, "nudity_threshold_female_breast", 0.55)
    monkeypatch.setattr(config.settings, "nudity_threshold_female_genitalia", 0.50)
    monkeypatch.setattr(config.settings, "nudity_threshold_male_genitalia", 0.50)
    monkeypatch.setattr(config.settings, "nudity_video_frames_quick", 12)
    monkeypatch.setattr(config.settings, "nudity_video_frames_full", 24)
    monkeypatch.setattr(config.settings, "nudity_video_min_positive_frames", 1)
    monkeypatch.setattr(config.settings, "nudity_batch_size", 4)
    monkeypatch.setattr(config.settings, "nudity_max_evidence_items", 6)


@pytest.mark.unit
def test_image_positive_is_one_l3_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "placeholder.png"
    target.write_bytes(b"fake; detector is injected and never decodes this")
    detector = FakeDetector(
        single=[
            explicit("BELLY_EXPOSED", 0.99),
            explicit("FEMALE_BREAST_EXPOSED", 0.91, [4, 5, 60, 70]),
            explicit("FEMALE_GENITALIA_EXPOSED", 0.82),
        ]
    )
    monkeypatch.setattr(nudity, "_get_detector", lambda: detector)
    monkeypatch.setattr(nudity, "_package_version", lambda: "3.4.2")

    findings = nudity.analyze_image(target)

    assert len(findings) == 1
    finding = findings[0]
    assert finding["category"] == "ketelanjangan"
    assert finding["layer_origin"] == "L3"
    assert finding["confidence"] == 0.91
    assert "Ketelanjangan terdeteksi pada gambar" in finding["label"]
    assert "FEMALE_BREAST_EXPOSED=0.910 box=4,5,60,70" in finding["evidence"]
    assert str(target) not in finding["evidence"]


@pytest.mark.unit
def test_covered_non_explicit_and_below_threshold_do_not_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "placeholder.jpg"
    target.write_bytes(b"fake")
    detector = FakeDetector(
        single=[
            explicit("FEMALE_GENITALIA_COVERED", 0.99),
            explicit("MALE_BREAST_EXPOSED", 0.99),
            explicit("BELLY_EXPOSED", 0.99),
            explicit("BUTTOCKS_EXPOSED", 0.59),
        ]
    )
    monkeypatch.setattr(nudity, "_get_detector", lambda: detector)

    assert nudity.analyze_image(target) == []


@pytest.mark.unit
def test_image_inference_failure_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "broken.jpg"
    target.write_bytes(b"fake")
    monkeypatch.setattr(nudity, "_get_detector", lambda: FakeDetector(fail=True))

    assert nudity.analyze_image(target) == []


@pytest.mark.unit
def test_video_quick_uses_bounded_frames_and_returns_one_l4_finding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "placeholder.mp4"
    video.write_bytes(b"fake; sampler is injected")
    frame_a = nudity.FrameSample(tmp_path / "a.jpg", 10, 1.25, "fake")
    frame_b = nudity.FrameSample(tmp_path / "b.jpg", 20, 3.75, "fake")
    called: list[int] = []

    @contextmanager
    def fake_samples(_path: Path, max_frames: int) -> Iterator[Sequence[nudity.FrameSample]]:
        called.append(max_frames)
        yield (frame_a, frame_b)

    detector = FakeDetector(
        batches=[
            [],
            [explicit("MALE_GENITALIA_EXPOSED", 0.93, [8, 9, 10, 11])],
        ]
    )
    monkeypatch.setattr(nudity, "_get_detector", lambda: detector)
    monkeypatch.setattr(nudity, "_sample_video_frames", fake_samples)
    monkeypatch.setattr(nudity, "_package_version", lambda: "3.4.2")

    token = set_analysis_mode(AcquisitionMode.QUICK)
    try:
        findings = nudity.analyze_video(video)
    finally:
        reset_analysis_mode(token)

    assert called == [12]
    assert detector.batch_size == 4
    assert len(findings) == 1
    finding = findings[0]
    assert finding["category"] == "ketelanjangan"
    assert finding["layer_origin"] == "L4"
    assert finding["confidence"] == 0.93
    assert "sampled=2 positive=1" in finding["evidence"]
    assert "t=3.75s MALE_GENITALIA_EXPOSED=0.930" in finding["evidence"]


@pytest.mark.unit
def test_video_full_uses_deeper_frame_budget(monkeypatch: pytest.MonkeyPatch):
    token = set_analysis_mode(AcquisitionMode.FULL)
    try:
        assert nudity._video_frame_limit() == 24
    finally:
        reset_analysis_mode(token)


@pytest.mark.unit
def test_sample_indexes_are_uniform_and_bounded():
    assert nudity._sample_frame_indexes(101, 5) == [0, 25, 50, 75, 100]
    assert nudity._sample_frame_indexes(10, 1) == [4]
    assert nudity._sample_frame_indexes(0, 10) == []


@pytest.mark.unit
def test_temp_frames_stay_below_configured_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_dir = tmp_path / "siksik-data"
    monkeypatch.setattr(config.settings, "data_dir", data_dir)

    root = nudity._temp_root()

    assert root == data_dir / "tmp" / "nudity"
    assert root.is_dir()


@pytest.mark.unit
def test_quick_gallery_bypass_still_runs_nudity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "gallery" / "photo.jpg"
    target.parent.mkdir()
    target.write_bytes(b"fake")
    flag = {
        "category": "ketelanjangan",
        "label": "Ketelanjangan terdeteksi pada gambar: test",
        "confidence": 0.9,
        "layer_origin": "L3",
        "evidence": "fake detector",
    }
    monkeypatch.setattr(nudity, "analyze_image", lambda _path: [flag])
    monkeypatch.setattr("app.services.vision._analyze_pil_image", lambda _path: [])

    token = set_analysis_mode(AcquisitionMode.QUICK)
    try:
        findings = analysis.analyze_content(
            target,
            "image/jpeg",
            "gallery",
            "",
            [],
        )
    finally:
        reset_analysis_mode(token)

    assert findings == [flag]


@pytest.mark.unit
def test_video_analysis_adds_nudity_without_replacing_existing_vision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"fake")
    nude_flag = {
        "category": "ketelanjangan",
        "label": "Ketelanjangan terdeteksi pada video: test",
        "confidence": 0.9,
        "layer_origin": "L4",
        "evidence": "nudity",
    }
    old_flag = {
        "category": "konten_visual",
        "label": "Existing video finding",
        "confidence": 0.8,
        "layer_origin": "L4",
        "evidence": "existing",
    }
    monkeypatch.setattr(nudity, "analyze_video", lambda _path: [nude_flag])
    monkeypatch.setattr("app.services.vision.analyze_video_file", lambda _path: [old_flag])

    findings = analysis.analyze_content(target, "video/mp4", "video", "", [])

    assert findings == [nude_flag, old_flag]
