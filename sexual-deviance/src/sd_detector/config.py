from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .modes import DetectionMode


@dataclass
class LlamaConfig:
    server_bin: str
    model_hf: str
    model_path: Optional[str]
    mmproj_path: Optional[str]
    host: str
    port: int
    ctx_size: int
    n_gpu_layers: int
    threads: int
    spawn_server: bool


@dataclass
class ActionConfig:
    block_explicit: float
    review_suggestive: float
    review_explicit: float


@dataclass
class CacheConfig:
    enabled: bool
    max_size: int
    ttl_sec: float


@dataclass
class TimeoutConfig:
    fast_sec: float
    balanced_sec: float
    full_sec: float


@dataclass
class DetectorConfig:
    mode: DetectionMode
    prescreen_enabled: bool
    prescreen_threshold: float
    nudenet_enabled: bool
    nudenet_model_path: Optional[str]
    nudenet_threshold: float
    nudenet_inference_resolution: int
    llm_always_on_flagged: bool
    llm_skip_if_prescreen_safe: bool
    video_sample_interval_sec: float
    video_max_frames: int
    max_image_size: int
    aggregation: str
    orientation_crop_ratio: float
    action: ActionConfig
    cache: CacheConfig
    timeout: TimeoutConfig


@dataclass
class AppConfig:
    llama: LlamaConfig
    detector: DetectorConfig


def _parse_mode(raw: str) -> DetectionMode:
    try:
        return DetectionMode(raw.lower())
    except ValueError:
        return DetectionMode.BALANCED


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    llama_raw = raw.get("llama", {})
    det_raw = raw.get("detector", {})
    action_raw = det_raw.get("action", {})
    cache_raw = det_raw.get("cache", {})
    timeout_raw = det_raw.get("timeout", {})

    return AppConfig(
        llama=LlamaConfig(
            server_bin=llama_raw.get("server_bin", "./llama.cpp/build/bin/llama-server"),
            model_hf=llama_raw.get("model_hf", "ggml-org/SmolVLM-500M-Instruct-GGUF"),
            model_path=llama_raw.get("model_path"),
            mmproj_path=llama_raw.get("mmproj_path"),
            host=llama_raw.get("host", "127.0.0.1"),
            port=int(llama_raw.get("port", 8080)),
            ctx_size=int(llama_raw.get("ctx_size", 4096)),
            n_gpu_layers=int(llama_raw.get("n_gpu_layers", 99)),
            threads=int(llama_raw.get("threads", 4)),
            spawn_server=bool(llama_raw.get("spawn_server", False)),
        ),
        detector=DetectorConfig(
            mode=_parse_mode(det_raw.get("mode", "balanced")),
            prescreen_enabled=bool(det_raw.get("prescreen_enabled", True)),
            prescreen_threshold=float(det_raw.get("prescreen_threshold", 0.10)),
            nudenet_enabled=bool(det_raw.get("nudenet_enabled", True)),
            nudenet_model_path=det_raw.get("nudenet_model_path"),
            nudenet_threshold=float(det_raw.get("nudenet_threshold", 0.30)),
            nudenet_inference_resolution=int(det_raw.get("nudenet_inference_resolution", 640)),
            llm_always_on_flagged=bool(det_raw.get("llm_always_on_flagged", True)),
            llm_skip_if_prescreen_safe=bool(det_raw.get("llm_skip_if_prescreen_safe", True)),
            video_sample_interval_sec=float(det_raw.get("video_sample_interval_sec", 2.0)),
            video_max_frames=int(det_raw.get("video_max_frames", 30)),
            max_image_size=int(det_raw.get("max_image_size", 512)),
            aggregation=det_raw.get("aggregation", "max_severity"),
            orientation_crop_ratio=float(det_raw.get("orientation_crop_ratio", 0.50)),
            action=ActionConfig(
                block_explicit=float(action_raw.get("block_explicit", 0.65)),
                review_suggestive=float(action_raw.get("review_suggestive", 0.55)),
                review_explicit=float(action_raw.get("review_explicit", 0.40)),
            ),
            cache=CacheConfig(
                enabled=bool(cache_raw.get("enabled", True)),
                max_size=int(cache_raw.get("max_size", 2048)),
                ttl_sec=float(cache_raw.get("ttl_sec", 3600)),
            ),
            timeout=TimeoutConfig(
                fast_sec=float(timeout_raw.get("fast_sec", 5.0)),
                balanced_sec=float(timeout_raw.get("balanced_sec", 30.0)),
                full_sec=float(timeout_raw.get("full_sec", 60.0)),
            ),
        ),
    )
