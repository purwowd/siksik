"""Local explicit-nudity enrichment for official L3/L4 findings.

The service uses NudeNet's bundled 320n ONNX model. It never downloads a model,
never writes outside ``settings.data_dir``, and returns no finding when the
optional engine or a media codec is unavailable. That failure isolation keeps
the existing analysis pipeline authoritative and operational.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.core.config import settings
from app.models.schemas import AcquisitionMode, Layer

log = logging.getLogger(__name__)

CATEGORY = "ketelanjangan"
MODEL_NAME = "320n-bundled"

_HUMAN_LABELS: Mapping[str, str] = {
    "ANUS_EXPOSED": "anus terlihat",
    "BUTTOCKS_EXPOSED": "bokong terlihat",
    "FEMALE_BREAST_EXPOSED": "payudara perempuan terlihat",
    "FEMALE_GENITALIA_EXPOSED": "genital perempuan terlihat",
    "MALE_GENITALIA_EXPOSED": "genital laki-laki terlihat",
}


class VideoSamplingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class FrameSample:
    path: Path
    index: int
    timestamp_s: float
    extractor: str


@dataclass(frozen=True)
class NudityAnalysisResult:
    findings: tuple[dict[str, Any], ...]
    cacheable: bool
    warning: str | None = None


def has_nudity_finding(findings: Sequence[Mapping[str, Any]]) -> bool:
    return any(str(item.get("category") or "") == CATEGORY for item in findings)


@dataclass
class _DetectorState:
    attempted: bool = False
    detector: Any | None = None
    error: str | None = None


_state = _DetectorState()
_state_lock = threading.Lock()
# ONNX already parallelizes internally. Serializing calls prevents the existing
# analysis worker pool from multiplying CPU threads and memory pressure.
_inference_lock = threading.Lock()


def reset_detector_state() -> None:
    """Drop process-local detector state (tests/config reload only)."""
    global _state
    with _state_lock:
        _state = _DetectorState()
    _package_version.cache_clear()


@lru_cache(maxsize=1)
def _package_version() -> str:
    try:
        return importlib.metadata.version("nudenet")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def onnx_execution_providers(available: Sequence[str]) -> list[str]:
    """Select an explicit provider list; never let ORT guess an unsafe CUDA path."""
    requested = str(settings.nudity_onnx_device or "cpu").strip().casefold()
    providers: list[str] = []
    if requested in {"cuda", "auto"} and "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def _prepend_nvidia_cuda_libs() -> None:
    """Make Torch's CUDA 12 / cuDNN 9 visible to onnxruntime-gpu (WSL often only has the driver)."""
    try:
        import ctypes

        import nvidia
    except Exception:
        return
    root = Path(nvidia.__file__).resolve().parent
    extra = [path for path in root.glob("*/lib") if path.is_dir()]
    if not extra:
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    prefix = ":".join(str(path) for path in extra)
    if prefix not in current:
        os.environ["LD_LIBRARY_PATH"] = f"{prefix}:{current}" if current else prefix
    libraries: list[Path] = []
    for directory in extra:
        libraries.extend(sorted(directory.glob("lib*.so*")))
    for _ in range(2):
        for candidate in libraries:
            try:
                ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue


def _create_detector(inference_resolution: int = 320) -> Any:
    """Construct NudeNet with a provider-explicit session.

    NudeNet 3.4.2 accepts ``providers`` but does not forward it to ONNX Runtime.
    Building the few constructor fields here avoids ORT first trying a broken
    CUDA provider and crashing the process before Python can handle the error.
    """
    import onnxruntime
    from nudenet import NudeDetector  # type: ignore
    import nudenet.nudenet as nudenet_mod  # type: ignore

    wanted = onnx_execution_providers(onnxruntime.get_available_providers())
    model_path = os.path.join(os.path.dirname(nudenet_mod.__file__), "320n.onnx")
    detector = NudeDetector.__new__(NudeDetector)
    try:
        detector.onnx_session = onnxruntime.InferenceSession(
            model_path,
            providers=wanted,
        )
    except Exception as exc:
        if wanted == ["CPUExecutionProvider"]:
            raise
        log.warning("NudeNet CUDA unavailable (%s); retrying CPU", type(exc).__name__)
        detector.onnx_session = onnxruntime.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
    model_inputs = detector.onnx_session.get_inputs()
    detector.input_width = inference_resolution
    detector.input_height = inference_resolution
    detector.input_name = model_inputs[0].name
    log.info("NudeNet ONNX providers=%s", detector.onnx_session.get_providers())
    return detector


def _get_detector() -> Any | None:
    if not settings.nudity_detection_enabled:
        return None
    if _state.attempted:
        return _state.detector
    with _state_lock:
        if _state.attempted:
            return _state.detector
        _state.attempted = True
        try:
            if str(settings.nudity_onnx_device).strip().casefold() in {"cuda", "auto"}:
                _prepend_nvidia_cuda_libs()
            _state.detector = _create_detector(inference_resolution=320)
        except Exception as exc:
            _state.error = type(exc).__name__
            log.warning("Nudity detector unavailable: %s", _state.error)
        return _state.detector


def _threshold_for(label: str) -> float | None:
    return {
        "ANUS_EXPOSED": settings.nudity_threshold_anus,
        "BUTTOCKS_EXPOSED": settings.nudity_threshold_buttocks,
        "FEMALE_BREAST_EXPOSED": settings.nudity_threshold_female_breast,
        "FEMALE_GENITALIA_EXPOSED": settings.nudity_threshold_female_genitalia,
        "MALE_GENITALIA_EXPOSED": settings.nudity_threshold_male_genitalia,
    }.get(label)


def _normalize_box(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        x, y, width, height = (int(round(float(item))) for item in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return max(0, x), max(0, y), width, height


def _normalize_detections(raw: object) -> list[Detection]:
    if not isinstance(raw, list):
        return []
    out: list[Detection] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        label_value = item.get("class", item.get("label"))
        if not isinstance(label_value, str):
            continue
        label = label_value.strip().upper()
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            continue
        out.append(Detection(label, score, _normalize_box(item.get("box"))))
    return out


def _triggering(raw: object) -> list[Detection]:
    matches: list[Detection] = []
    for detection in _normalize_detections(raw):
        threshold = _threshold_for(detection.label)
        if threshold is not None and detection.score >= threshold:
            matches.append(detection)
    return sorted(matches, key=lambda item: item.score, reverse=True)


def _detect_one(detector: Any, path: Path) -> object:
    from app.services.inference_guard import gpu_inference_slot

    with _inference_lock, gpu_inference_slot():
        return detector.detect(str(path))


def _detect_many(detector: Any, paths: Sequence[Path]) -> list[object]:
    values = [str(path) for path in paths]
    if not values:
        return []
    from app.services.inference_guard import gpu_inference_slot

    with _inference_lock, gpu_inference_slot():
        if len(values) == 1:
            return [detector.detect(values[0])]
        try:
            raw = detector.detect_batch(values, batch_size=settings.nudity_batch_size)
            if isinstance(raw, list) and len(raw) == len(values):
                return list(raw)
        except Exception:
            # Some ONNX providers expose only a fixed batch axis.
            pass
        return [detector.detect(value) for value in values]


def _class_summary(detections: Sequence[Detection]) -> str:
    labels: list[str] = []
    for detection in detections:
        label = _HUMAN_LABELS[detection.label]
        if label not in labels:
            labels.append(label)
    return ", ".join(labels[:3])


def _box_text(box: tuple[int, int, int, int] | None) -> str:
    return "" if box is None else f" box={','.join(str(value) for value in box)}"


def _engine_label() -> str:
    return f"NudeNet {_package_version()}/{MODEL_NAME}"


def analyze_image_result(path: Path) -> NudityAnalysisResult:
    """Analyze an image and retain whether an empty result is safe to cache."""
    if not settings.nudity_detection_enabled:
        return NudityAnalysisResult((), True)
    detector = _get_detector()
    if detector is None:
        return NudityAnalysisResult((), False, _state.error or "detector_unavailable")
    try:
        matches = _triggering(_detect_one(detector, path))
    except Exception as exc:
        log.debug("Nudity image analysis skipped: %s", type(exc).__name__)
        return NudityAnalysisResult((), False, "image_inference_failed")
    if not matches:
        return NudityAnalysisResult((), True)

    top = matches[: settings.nudity_max_evidence_items]
    evidence_items = [
        f"{item.label}={item.score:.3f}{_box_text(item.box)}"
        for item in top
    ]
    return NudityAnalysisResult(
        (
            {
                "category": CATEGORY,
                "label": f"Ketelanjangan terdeteksi pada gambar: {_class_summary(matches)}",
                "confidence": round(matches[0].score, 3),
                "layer_origin": Layer.L3.value,
                "evidence": f"{_engine_label()} | {'; '.join(evidence_items)}"[:320],
            },
        ),
        True,
    )


def analyze_image(path: Path) -> list[dict]:
    """Return at most one pending-compatible L3 finding for an image."""
    return list(analyze_image_result(path).findings)


def _video_frame_limit() -> int:
    from app.services.hash_cache import get_analysis_mode

    if get_analysis_mode() == AcquisitionMode.QUICK:
        return settings.nudity_video_frames_quick
    return settings.nudity_video_frames_full


def _sample_frame_indexes(frame_count: int, sample_count: int) -> list[int]:
    if frame_count < 1 or sample_count < 1:
        return []
    requested = min(frame_count, sample_count)
    if requested == 1:
        return [(frame_count - 1) // 2]
    last = frame_count - 1
    return sorted(
        {
            int(round(index * last / (requested - 1)))
            for index in range(requested)
        }
    )


def _temp_root() -> Path:
    root = settings.data_dir / "tmp" / "nudity"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise VideoSamplingError("ffprobe_unavailable")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=duration",
                "-of",
                "json",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=settings.nudity_video_probe_timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoSamplingError("ffprobe_timeout") from exc
    except OSError as exc:
        raise VideoSamplingError("ffprobe_failed") from exc
    if result.returncode != 0:
        raise VideoSamplingError("video_probe_failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoSamplingError("video_probe_invalid_json") from exc

    values: list[object] = []
    if isinstance(payload, dict):
        format_value = payload.get("format")
        if isinstance(format_value, dict):
            values.append(format_value.get("duration"))
        streams = payload.get("streams")
        if isinstance(streams, list):
            values.extend(
                stream.get("duration")
                for stream in streams
                if isinstance(stream, dict)
            )
    for raw in values:
        try:
            duration = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise VideoSamplingError("video_duration_unavailable")


@contextmanager
def _sample_with_ffmpeg(
    video_path: Path,
    max_frames: int,
) -> Iterator[Sequence[FrameSample]]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VideoSamplingError("ffmpeg_unavailable")
    duration_s = _probe_duration(video_path)
    requested = min(max_frames, max(1, math.ceil(duration_s)))
    fps = requested / duration_s

    with tempfile.TemporaryDirectory(prefix="frames_", dir=_temp_root()) as raw_dir:
        frame_dir = Path(raw_dir)
        pattern = frame_dir / "frame_%04d.jpg"
        filter_value = (
            f"fps={fps:.10f},"
            f"scale={settings.nudity_frame_max_edge_px}:{settings.nudity_frame_max_edge_px}:"
            "force_original_aspect_ratio=decrease"
        )
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(video_path),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-vf",
                    filter_value,
                    "-frames:v",
                    str(requested),
                    "-q:v",
                    "3",
                    str(pattern),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=settings.nudity_video_extract_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoSamplingError("video_frame_extraction_timeout") from exc
        except OSError as exc:
            raise VideoSamplingError("video_frame_extraction_failed") from exc
        if result.returncode != 0:
            raise VideoSamplingError("video_frame_extraction_failed")

        frames = sorted(frame_dir.glob("frame_*.jpg"))[:requested]
        if not frames:
            raise VideoSamplingError("video_has_no_decodable_frames")
        spacing = duration_s / len(frames)
        yield tuple(
            FrameSample(path, index, index * spacing, "ffmpeg")
            for index, path in enumerate(frames)
        )


@contextmanager
def _sample_with_opencv(
    video_path: Path,
    max_frames: int,
) -> Iterator[Sequence[FrameSample]]:
    try:
        import cv2  # type: ignore
    except (ImportError, OSError) as exc:
        raise VideoSamplingError("opencv_unavailable") from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise VideoSamplingError("opencv_video_open_failed")
    try:
        raw_frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        raw_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if (
            not math.isfinite(raw_frame_count)
            or not math.isfinite(raw_fps)
            or raw_frame_count < 1
            or raw_fps <= 0
        ):
            raise VideoSamplingError("opencv_video_metadata_unavailable")
        frame_count = int(raw_frame_count)
        duration_s = frame_count / raw_fps
        requested = min(max_frames, max(1, math.ceil(duration_s)))
        indexes = _sample_frame_indexes(frame_count, requested)

        with tempfile.TemporaryDirectory(prefix="cv_frames_", dir=_temp_root()) as raw_dir:
            frame_dir = Path(raw_dir)
            samples: list[FrameSample] = []
            for source_index in indexes:
                capture.set(cv2.CAP_PROP_POS_FRAMES, float(source_index))
                decoded, frame = capture.read()
                if not decoded or frame is None or getattr(frame, "size", 0) == 0:
                    continue
                try:
                    height, width = frame.shape[:2]
                except (AttributeError, ValueError):
                    continue
                longest = max(int(width), int(height))
                if longest < 1:
                    continue
                if longest > settings.nudity_frame_max_edge_px:
                    scale = settings.nudity_frame_max_edge_px / longest
                    frame = cv2.resize(
                        frame,
                        (
                            max(1, int(round(width * scale))),
                            max(1, int(round(height * scale))),
                        ),
                        interpolation=cv2.INTER_AREA,
                    )
                frame_path = frame_dir / f"frame_{len(samples):04d}.jpg"
                if not cv2.imwrite(str(frame_path), frame):
                    continue
                samples.append(
                    FrameSample(
                        frame_path,
                        source_index,
                        source_index / raw_fps,
                        "opencv",
                    )
                )
            if not samples:
                raise VideoSamplingError("opencv_video_has_no_decodable_frames")
            yield tuple(samples)
    finally:
        capture.release()


@contextmanager
def _sample_video_frames(
    video_path: Path,
    max_frames: int,
) -> Iterator[Sequence[FrameSample]]:
    ffmpeg_error: VideoSamplingError | None = None
    if shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None:
        try:
            with _sample_with_ffmpeg(video_path, max_frames) as frames:
                yield frames
                return
        except VideoSamplingError as exc:
            ffmpeg_error = exc
    try:
        with _sample_with_opencv(video_path, max_frames) as frames:
            yield frames
    except VideoSamplingError as opencv_error:
        if ffmpeg_error is None:
            raise
        raise VideoSamplingError(
            f"video_extractors_failed:{ffmpeg_error}:{opencv_error}"
        ) from opencv_error


def analyze_video_result(path: Path) -> NudityAnalysisResult:
    """Analyze sampled frames and distinguish clean media from an incomplete run."""
    if not settings.nudity_detection_enabled:
        return NudityAnalysisResult((), True)
    detector = _get_detector()
    if detector is None:
        return NudityAnalysisResult((), False, _state.error or "detector_unavailable")
    try:
        with _sample_video_frames(path, _video_frame_limit()) as frames:
            raw_batches = _detect_many(detector, [frame.path for frame in frames])
            if len(raw_batches) != len(frames):
                return NudityAnalysisResult((), False, "video_batch_incomplete")
            evidence: list[tuple[FrameSample, Detection]] = []
            positive_frames: set[int] = set()
            for frame, raw in zip(frames, raw_batches):
                matches = _triggering(raw)
                if matches:
                    positive_frames.add(frame.index)
                    evidence.extend((frame, match) for match in matches)
    except Exception as exc:
        log.debug("Nudity video analysis skipped: %s", type(exc).__name__)
        warning = str(exc) if isinstance(exc, VideoSamplingError) else "video_inference_failed"
        return NudityAnalysisResult((), False, warning[:128])

    if len(positive_frames) < settings.nudity_video_min_positive_frames:
        return NudityAnalysisResult((), True)
    evidence.sort(key=lambda item: item[1].score, reverse=True)
    top = evidence[: settings.nudity_max_evidence_items]
    detections = [item[1] for item in evidence]
    evidence_items = [
        f"t={frame.timestamp_s:.2f}s {detection.label}={detection.score:.3f}"
        f"{_box_text(detection.box)}"
        for frame, detection in top
    ]
    samplers = ",".join(sorted({frame.extractor for frame in frames}))
    prefix = (
        f"{_engine_label()} | sampled={len(frames)} positive={len(positive_frames)} "
        f"sampler={samplers} | "
    )
    return NudityAnalysisResult(
        (
            {
                "category": CATEGORY,
                "label": f"Ketelanjangan terdeteksi pada video: {_class_summary(detections)}",
                "confidence": round(evidence[0][1].score, 3),
                "layer_origin": Layer.L4.value,
                "evidence": f"{prefix}{'; '.join(evidence_items)}"[:320],
            },
        ),
        True,
    )


def analyze_video_frames_result(
    video_path: Path,
    frame_paths: Sequence[Path],
) -> NudityAnalysisResult:
    """Analyze an already-decoded shared frame set without another ffmpeg pass."""
    if not settings.nudity_detection_enabled:
        return NudityAnalysisResult((), True)
    detector = _get_detector()
    if detector is None:
        return NudityAnalysisResult((), False, _state.error or "detector_unavailable")
    selected_paths = list(frame_paths)[: _video_frame_limit()]
    if not selected_paths:
        return NudityAnalysisResult((), False, "shared_video_frames_empty")
    try:
        duration = _probe_duration(video_path)
    except VideoSamplingError:
        duration = float(len(selected_paths))
    spacing = duration / max(len(selected_paths), 1)
    frames = tuple(
        FrameSample(path, index, index * spacing, "shared-ffmpeg")
        for index, path in enumerate(selected_paths)
    )
    try:
        raw_batches = _detect_many(detector, [frame.path for frame in frames])
        if len(raw_batches) != len(frames):
            return NudityAnalysisResult((), False, "video_batch_incomplete")
        evidence: list[tuple[FrameSample, Detection]] = []
        positive_frames: set[int] = set()
        for frame, raw in zip(frames, raw_batches):
            matches = _triggering(raw)
            if matches:
                positive_frames.add(frame.index)
                evidence.extend((frame, match) for match in matches)
    except Exception as exc:
        log.debug("Shared-frame nudity analysis skipped: %s", type(exc).__name__)
        return NudityAnalysisResult((), False, "video_inference_failed")
    if len(positive_frames) < settings.nudity_video_min_positive_frames:
        return NudityAnalysisResult((), True)
    evidence.sort(key=lambda item: item[1].score, reverse=True)
    top = evidence[: settings.nudity_max_evidence_items]
    detections = [item[1] for item in evidence]
    evidence_items = [
        f"t={frame.timestamp_s:.2f}s {detection.label}={detection.score:.3f}"
        f"{_box_text(detection.box)}"
        for frame, detection in top
    ]
    prefix = (
        f"{_engine_label()} | sampled={len(frames)} positive={len(positive_frames)} "
        "sampler=shared-ffmpeg | "
    )
    return NudityAnalysisResult(
        (
            {
                "category": CATEGORY,
                "label": f"Ketelanjangan terdeteksi pada video: {_class_summary(detections)}",
                "confidence": round(evidence[0][1].score, 3),
                "layer_origin": Layer.L4.value,
                "evidence": f"{prefix}{'; '.join(evidence_items)}"[:320],
            },
        ),
        True,
    )


def analyze_video(path: Path) -> list[dict]:
    """Return at most one pending-compatible L4 finding for sampled video frames."""
    return list(analyze_video_result(path).findings)


def status() -> dict[str, Any]:
    try:
        package_available = importlib.util.find_spec("nudenet") is not None
    except (ImportError, ValueError):
        package_available = False
    return {
        "enabled": bool(settings.nudity_detection_enabled),
        "available": bool(_state.detector is not None or (not _state.attempted and package_available)),
        "initialized": _state.detector is not None,
        "error": _state.error,
        "engine": "NudeNet",
        "package_version": _package_version() if package_available else None,
        "model": MODEL_NAME,
        "video_frames_quick": settings.nudity_video_frames_quick,
        "video_frames_full": settings.nudity_video_frames_full,
        "thresholds": {
            label: _threshold_for(label)
            for label in sorted(_HUMAN_LABELS)
        },
    }
