from __future__ import annotations

import atexit
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

from .config import LlamaConfig
from .media import image_to_base64
from .prompt import (
    CLASSIFY_FROM_DESC_PROMPT,
    DESCRIBE_PROMPT,
    DESCRIBE_CLASSIFY_PROMPT,
    FOLLOWUP_DESCRIBE_PROMPT,
    GENDER_COUNT_PROMPT,
    LGBT_VISION_PROMPT,
    INDONESIAN_MEME_VISION_PROMPT,
    INDONESIAN_MEME_JSON_PROMPT,
    MEME_BAND_TRANSCRIBE_PROMPT,
    ORIENTATION_PROMPT,
    ORIENTATION_VISION_PROMPT,
    RETRY_PROMPT,
    parse_classification,
    parse_describe_classify,
    _ENUM_FIELDS,
)
from .schema import FrameAnalysis, NudityLevel, Orientation, Severity


class LlamaServer:
    def __init__(self, cfg: LlamaConfig) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self.base_url = f"http://{cfg.host}:{cfg.port}"

    def _build_cmd(self) -> list[str]:
        cmd = [
            self.cfg.server_bin,
            "--host", self.cfg.host,
            "--port", str(self.cfg.port),
            "--ctx-size", str(self.cfg.ctx_size),
            "-ngl", str(self.cfg.n_gpu_layers),
            "-t", str(self.cfg.threads),
        ]
        if self.cfg.model_path:
            cmd.extend(["-m", self.cfg.model_path])
            if self.cfg.mmproj_path:
                cmd.extend(["--mmproj", self.cfg.mmproj_path])
        else:
            cmd.extend(["-hf", self.cfg.model_hf])
        return cmd

    def start(self, timeout: float = 180.0) -> None:
        if self._proc and self._proc.poll() is None:
            return
        server = Path(self.cfg.server_bin)
        if not server.exists():
            raise FileNotFoundError(f"llama-server tidak ditemukan: {server}\nJalankan: ./scripts/setup.sh")
        self._proc = subprocess.Popen(self._build_cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        atexit.register(self.stop)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode(errors="replace")
                raise RuntimeError(f"llama-server gagal start:\n{err[-2000:]}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server tidak ready")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _chat(self, messages: list, max_tokens: int = 300) -> str:
        payload = {
            "model": "vlm",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            resp = client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    def _vision(self, img: Image.Image, prompt: str, max_tokens: int = 200) -> str:
        b64 = image_to_base64(img)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }]
        return self._chat(messages, max_tokens)

    def describe_image(self, img: Image.Image) -> str:
        return self._vision(img, DESCRIBE_PROMPT, max_tokens=150).strip()

    def describe_and_classify(self, img: Image.Image, nudenet_hint: str = "") -> tuple[str, FrameAnalysis]:
        hint = f"\nBody scan hint: {nudenet_hint}" if nudenet_hint else ""
        raw = self._vision(img, DESCRIBE_CLASSIFY_PROMPT + hint, max_tokens=280).strip()
        description, data = parse_describe_classify(raw)

        if data.get("confidence", 0) < 0.55:
            try:
                retry = self._chat(
                    [{"role": "user", "content": RETRY_PROMPT.format(description=description)}],
                    max_tokens=200,
                )
                retry_data = parse_classification(retry)
                if retry_data.get("confidence", 0) >= data.get("confidence", 0):
                    data = retry_data
            except Exception:
                pass

        if data.get("orientation") == "none" and data.get("severity") != "safe":
            try:
                orient_raw = self._chat(
                    [{"role": "user", "content": ORIENTATION_PROMPT.format(description=description)}],
                    max_tokens=80,
                )
                orient_data = parse_classification(orient_raw)
                if orient_data.get("orientation", "none") != "none":
                    data["orientation"] = orient_data["orientation"]
            except Exception:
                pass

        reason = str(data.get("reason") or "").strip()
        if not reason or reason.lower() in _ENUM_FIELDS["severity"]:
            reason = description[:100]

        llm = FrameAnalysis(
            severity=Severity(data.get("severity", "safe")),
            nudity=NudityLevel(data.get("nudity", "none")),
            orientation=Orientation(data.get("orientation", "none")),
            acts=data.get("acts", []),
            confidence=float(data.get("confidence", 0.5)),
            reason=reason,
        )
        return description, llm

    def followup_describe(self, img: Image.Image) -> str:
        return self._vision(img, FOLLOWUP_DESCRIBE_PROMPT, max_tokens=100).strip()

    def gender_count_describe(self, img: Image.Image) -> str:
        return self._vision(img, GENDER_COUNT_PROMPT, max_tokens=120).strip()

    def lgbt_describe(self, img: Image.Image) -> str:
        return self._vision(img, LGBT_VISION_PROMPT, max_tokens=100).strip()

    def meme_describe(self, img: Image.Image) -> str:
        return self._vision(img, INDONESIAN_MEME_VISION_PROMPT, max_tokens=220).strip()

    def meme_transcribe_strip(self, strip: Image.Image) -> str:
        return self._vision(strip, MEME_BAND_TRANSCRIBE_PROMPT, max_tokens=120).strip()

    def meme_analyze_json(self, img: Image.Image) -> str:
        return self._vision(img, INDONESIAN_MEME_JSON_PROMPT, max_tokens=220).strip()

    def infer_orientation_vision(self, img: Image.Image) -> Orientation:
        try:
            raw = self._vision(img, ORIENTATION_VISION_PROMPT, max_tokens=80)
            data = parse_classification(raw)
            return Orientation(data.get("orientation", "none"))
        except Exception:
            return Orientation.NONE

    def classify_from_description(
        self,
        description: str,
        nudenet_hint: str = "",
        img: Optional[Image.Image] = None,
    ) -> FrameAnalysis:
        hint_line = f"Body scan hint: {nudenet_hint}" if nudenet_hint else ""
        prompt = CLASSIFY_FROM_DESC_PROMPT.format(description=description, hint=hint_line)
        content = self._chat([{"role": "user", "content": prompt}], max_tokens=250)
        data = parse_classification(content)

        if data.get("confidence", 0) < 0.6:
            try:
                retry = self._chat(
                    [{"role": "user", "content": RETRY_PROMPT.format(description=description)}],
                    max_tokens=200,
                )
                retry_data = parse_classification(retry)
                if retry_data.get("confidence", 0) >= data.get("confidence", 0):
                    data = retry_data
            except Exception:
                pass

        if data.get("orientation") == "none" and data.get("severity") != "safe":
            try:
                orient_raw = self._chat(
                    [{"role": "user", "content": ORIENTATION_PROMPT.format(description=description)}],
                    max_tokens=80,
                )
                orient_data = parse_classification(orient_raw)
                if orient_data.get("orientation", "none") != "none":
                    data["orientation"] = orient_data["orientation"]
            except Exception:
                pass

        if data.get("orientation") == "none" and data.get("severity") != "safe" and img is not None:
            vis_orient = self.infer_orientation_vision(img)
            if vis_orient != Orientation.NONE:
                data["orientation"] = vis_orient.value

        return FrameAnalysis(
            severity=Severity(data.get("severity", "safe")),
            nudity=NudityLevel(data.get("nudity", "none")),
            orientation=Orientation(data.get("orientation", "none")),
            acts=data.get("acts", []),
            confidence=float(data.get("confidence", 0.5)),
            reason=str(data.get("reason", description[:100])),
        )

    def classify_image(self, img: Image.Image, nudenet_hint: str = "") -> FrameAnalysis:
        description = self.describe_image(img)
        return self.classify_from_description(description, nudenet_hint, img=img)


class ExternalServer:
    def __init__(self, host: str, port: int) -> None:
        self._inner = LlamaServer(LlamaConfig(
            server_bin="", model_hf="", model_path=None, mmproj_path=None,
            host=host, port=port, ctx_size=4096, n_gpu_layers=0, threads=1,
            spawn_server=False,
        ))

    def start(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(f"{self._inner.base_url}/health", timeout=2.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise ConnectionError(f"Tidak bisa connect ke {self._inner.base_url}")

    def stop(self) -> None:
        pass

    def describe_image(self, img: Image.Image) -> str:
        return self._inner.describe_image(img)

    def describe_and_classify(self, img: Image.Image, nudenet_hint: str = "") -> tuple[str, FrameAnalysis]:
        return self._inner.describe_and_classify(img, nudenet_hint)

    def followup_describe(self, img: Image.Image) -> str:
        return self._inner.followup_describe(img)

    def gender_count_describe(self, img: Image.Image) -> str:
        return self._inner.gender_count_describe(img)

    def lgbt_describe(self, img: Image.Image) -> str:
        return self._inner.lgbt_describe(img)

    def meme_describe(self, img: Image.Image) -> str:
        return self._inner.meme_describe(img)

    def meme_transcribe_strip(self, strip: Image.Image) -> str:
        return self._inner.meme_transcribe_strip(strip)

    def meme_analyze_json(self, img: Image.Image) -> str:
        return self._inner.meme_analyze_json(img)

    def classify_from_description(self, description: str, nudenet_hint: str = "", img: Optional[Image.Image] = None) -> FrameAnalysis:
        return self._inner.classify_from_description(description, nudenet_hint, img=img)

    def classify_image(self, img: Image.Image, nudenet_hint: str = "") -> FrameAnalysis:
        return self._inner.classify_image(img, nudenet_hint)

    def infer_orientation_vision(self, img: Image.Image) -> Orientation:
        return self._inner.infer_orientation_vision(img)
