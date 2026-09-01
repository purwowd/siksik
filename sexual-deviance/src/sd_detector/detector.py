from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from .actions import ActionThresholds
from .aggregator import aggregate_frames
from .cache import ResultCache
from .classifier import FrameClassifier
from .config import AppConfig, load_config
from .indonesian_meme import analyze_indonesian_meme
from .lgbt import analyze_lgbt
from .llama_backend import ExternalServer, LlamaServer
from .media import extract_video_frames, is_video, load_image, load_image_bytes, resize_image
from .metrics import Metrics
from .modes import DetectionMode
from .nudenet_tier import NudeNetResult, NudeNetScanner
from .prescreen import is_likely_safe, pil_to_bgr, prescreen_score
from .schema import Action, FrameAnalysis, IndonesianMemeContext, LgbtContext, MediaVerdict, NudityLevel, Orientation, Severity


@dataclass
class DetectionResult:
    verdict: MediaVerdict

    @property
    def flagged(self) -> bool:
        return self.verdict.flagged

    @property
    def action(self) -> Action:
        return self.verdict.action

    def to_dict(self) -> dict:
        return self.verdict.to_dict()


def _nudenet_frame(nudenet: NudeNetResult, prescreen_score_val: float, bgr=None) -> FrameAnalysis:
    acts = ["nudity"] if nudenet.flagged else []
    lgbt = analyze_lgbt(bgr, []) if bgr is not None else LgbtContext()
    meme = analyze_indonesian_meme(bgr, []) if bgr is not None else IndonesianMemeContext()
    return FrameAnalysis(
        severity=nudenet.severity,
        nudity=nudenet.nudity,
        orientation=Orientation.NONE,
        lgbt=lgbt,
        indonesian_meme=meme,
        acts=acts,
        confidence=nudenet.max_score if nudenet.flagged else 0.92,
        reason=nudenet.reason or "NudeNet: clean",
        prescreen_score=prescreen_score_val,
        skipped_llm=True,
    )


class ContentDetector:
    """
    Pipeline mode-aware:
    - FAST: prescreen + NudeNet (~50ms)
    - BALANCED: FAST + 1x VLM (~1-2s)
    - FULL: BALANCED + 1 center-crop orientasi (~2-4s)

    Default: connect ke llama-server sidecar (tidak spawn per request).
    """

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        config_path: str | Path = "config.yaml",
        external_server: Optional[bool] = None,
        mode: Optional[DetectionMode] = None,
    ) -> None:
        self.config = config or load_config(config_path)
        if mode is not None:
            self.config.detector.mode = mode

        if external_server is None:
            self._external = not self.config.llama.spawn_server
        else:
            self._external = external_server

        self._server: Optional[LlamaServer | ExternalServer] = None
        self._nudenet: Optional[NudeNetScanner] = None
        self._classifier: Optional[FrameClassifier] = None
        self._cache: Optional[ResultCache] = None
        self.metrics = Metrics()

    @property
    def mode(self) -> DetectionMode:
        return self.config.detector.mode

    def set_mode(self, mode: DetectionMode) -> None:
        """Ganti mode runtime — reset classifier jika berubah."""
        if mode != self.config.detector.mode:
            self.config.detector.mode = mode
            self._classifier = None

    def _get_server(self) -> LlamaServer | ExternalServer:
        if self._server is None:
            if self._external:
                c = self.config.llama
                self._server = ExternalServer(c.host, c.port)
            else:
                self._server = LlamaServer(self.config.llama)
        return self._server

    def _get_classifier(self) -> FrameClassifier:
        if self._classifier is None:
            det = self.config.detector
            meme = self.config.meme
            from .meme_ocr import OcrConfig

            self._classifier = FrameClassifier(
                self._get_server(),
                mode=det.mode,
                crop_ratio=det.orientation_crop_ratio,
                meme_config=meme,
                ocr_config=OcrConfig(
                    enabled=meme.ocr_enabled,
                    lazy=meme.ocr_lazy,
                    lang=meme.ocr_lang,
                    vlm_band_fallback=meme.ocr_vlm_band_fallback,
                    full_res=meme.ocr_full_res,
                    max_size=meme.ocr_max_size,
                    workers=meme.ocr_workers,
                ),
            )
        return self._classifier

    def _get_nudenet(self) -> NudeNetScanner:
        if self._nudenet is None:
            det = self.config.detector
            self._nudenet = NudeNetScanner(
                threshold=det.nudenet_threshold,
                inference_resolution=det.nudenet_inference_resolution,
                model_path=det.nudenet_model_path,
            )
        return self._nudenet

    def _get_cache(self) -> ResultCache:
        if self._cache is None:
            c = self.config.detector.cache
            self._cache = ResultCache(max_size=c.max_size, ttl_sec=c.ttl_sec)
        return self._cache

    def _action_thresholds(self) -> ActionThresholds:
        a = self.config.detector.action
        return ActionThresholds(
            block_explicit=a.block_explicit,
            review_suggestive=a.review_suggestive,
            review_explicit=a.review_explicit,
        )

    def _timeout_sec(self) -> float:
        t = self.config.detector.timeout
        if self.mode == DetectionMode.FAST:
            return t.fast_sec
        if self.mode == DetectionMode.FULL:
            return t.full_sec
        return t.balanced_sec

    def start(self) -> None:
        if self.mode != DetectionMode.FAST:
            self._get_server().start()
        if self.config.detector.nudenet_enabled:
            self._get_nudenet()._ensure_loaded()

    def stop(self) -> None:
        if self._server and not self._external:
            self._server.stop()

    def __enter__(self) -> ContentDetector:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    def _run_nudenet(self, bgr) -> NudeNetResult:
        if not self.config.detector.nudenet_enabled:
            return NudeNetResult(reason="NudeNet disabled")
        return self._get_nudenet().scan_bgr(bgr)

    def _analyze_frame(self, img: Image.Image, nudenet_bgr=None, ocr_bgr=None) -> FrameAnalysis:
        det = self.config.detector
        bgr = pil_to_bgr(img)
        ps_score = prescreen_score(bgr)
        nn_bgr = nudenet_bgr if nudenet_bgr is not None else bgr

        if det.prescreen_enabled and is_likely_safe(bgr, det.prescreen_threshold):
            return FrameAnalysis(
                severity=Severity.SAFE,
                nudity=NudityLevel.NONE,
                orientation=Orientation.NONE,
                lgbt=analyze_lgbt(nn_bgr, []),
                indonesian_meme=analyze_indonesian_meme(nn_bgr, []),
                acts=[],
                confidence=0.92,
                reason="Prescreen: landscape/object",
                prescreen_score=ps_score,
                skipped_llm=True,
            )

        nudenet = self._run_nudenet(nn_bgr)

        if det.mode == DetectionMode.FAST:
            return _nudenet_frame(nudenet, ps_score, bgr)

        result = self._get_classifier().classify(img, nudenet, ocr_bgr=ocr_bgr)
        result.prescreen_score = ps_score
        return result

    def _aggregate(
        self,
        path: str,
        media_type: str,
        frames: list[FrameAnalysis],
        frame_count: int,
        latency_ms: float,
        cache_hit: bool,
    ) -> MediaVerdict:
        verdict = aggregate_frames(
            path,
            media_type,
            frames,
            frame_count,
            mode=self.mode.value,
            action_thresholds=self._action_thresholds(),
            latency_ms=round(latency_ms, 2),
            cache_hit=cache_hit,
        )
        self.metrics.record_action(verdict.action.value)
        return verdict

    def _analyze_image_internal(
        self,
        img: Image.Image,
        source: str,
        nudenet_bgr=None,
        ocr_bgr=None,
    ) -> DetectionResult:
        start = time.perf_counter()
        frame = self._analyze_frame(img, nudenet_bgr=nudenet_bgr, ocr_bgr=ocr_bgr)
        latency_ms = (time.perf_counter() - start) * 1000.0
        verdict = self._aggregate(source, "image", [frame], 1, latency_ms, cache_hit=False)
        return DetectionResult(verdict=verdict)

    def _analyze_with_cache(self, data: bytes, source: str, analyze_fn) -> DetectionResult:
        det = self.config.detector
        mode_val = self.mode.value
        key = None

        if det.cache.enabled and data:
            cache = self._get_cache()
            key = cache.key(data, mode_val)
            cached = cache.get(key)
            if cached is not None:
                self.metrics.record_cache_hit()
                verdict = MediaVerdict.model_validate(cached)
                verdict.path = source
                verdict.cache_hit = True
                return DetectionResult(verdict=verdict)
            self.metrics.record_cache_miss()
        else:
            self.metrics.record_cache_miss()

        timeout = self._timeout_sec()

        with self.metrics.track(mode_val):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(analyze_fn)
                    result = future.result(timeout=timeout)
            except FuturesTimeout:
                raise TimeoutError(f"Analysis timed out after {timeout}s (mode={mode_val})")

        if det.cache.enabled and data and key is not None:
            self._get_cache().set(key, result.verdict.model_dump(mode="json"))

        return result

    def analyze_bytes(self, data: bytes, source: str = "<bytes>") -> DetectionResult:
        det = self.config.detector
        full_img = Image.open(io.BytesIO(data)).convert("RGB")
        img = resize_image(full_img, det.max_image_size)
        nudenet_bgr = pil_to_bgr(full_img)
        ocr_bgr = nudenet_bgr if self.config.meme.ocr_full_res else None

        def run() -> DetectionResult:
            return self._analyze_image_internal(img, source, nudenet_bgr=nudenet_bgr, ocr_bgr=ocr_bgr)

        return self._analyze_with_cache(data, source, run)

    def analyze_image(self, path: str | Path) -> DetectionResult:
        path = Path(path)
        data = path.read_bytes()
        det = self.config.detector
        full_img = Image.open(io.BytesIO(data)).convert("RGB")
        img = resize_image(full_img, det.max_image_size)
        nudenet_bgr = pil_to_bgr(full_img)
        ocr_bgr = nudenet_bgr if self.config.meme.ocr_full_res else None

        def run() -> DetectionResult:
            return self._analyze_image_internal(img, str(path), nudenet_bgr=nudenet_bgr, ocr_bgr=ocr_bgr)

        return self._analyze_with_cache(data, str(path), run)

    def analyze_video(self, path: str | Path) -> DetectionResult:
        path = str(path)
        det = self.config.detector
        start = time.perf_counter()
        raw_frames = extract_video_frames(
            path,
            interval_sec=det.video_sample_interval_sec,
            max_frames=det.video_max_frames,
            max_size=det.max_image_size,
            include_nudenet_bgr=True,
        )
        analyses = [
            self._analyze_frame(img, nudenet_bgr=nn_bgr)
            for _ts, img, nn_bgr in raw_frames
        ]
        latency_ms = (time.perf_counter() - start) * 1000.0
        with self.metrics.track(self.mode.value):
            verdict = self._aggregate(
                path, "video", analyses, len(raw_frames), latency_ms, cache_hit=False,
            )
        return DetectionResult(verdict=verdict)

    def analyze(self, path: str | Path) -> DetectionResult:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        if is_video(path):
            return self.analyze_video(path)
        return self.analyze_image(path)
