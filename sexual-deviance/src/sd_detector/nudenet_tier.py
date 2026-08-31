from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schema import NudityLevel, Severity

EXPLICIT_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
}

SUGGESTIVE_LABELS = {
    "FEMALE_BREAST_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "BELLY_EXPOSED",
    "ARMPITS_EXPOSED",
}


NON_MODERATION_LABELS = {
    "FACE_FEMALE",
    "FACE_MALE",
    "FEMALE_BREAST_COVERED",
    "MALE_BREAST_COVERED",
    "FEMALE_GENITALIA_COVERED",
    "MALE_GENITALIA_COVERED",
    "FEET_COVERED",
    "BELLY_COVERED",
    "ARMPITS_COVERED",
    "BUTTOCKS_COVERED",
    "FEET_EXPOSED",
}


@dataclass
class NudeNetResult:
    labels: list[str] = field(default_factory=list)
    max_score: float = 0.0
    severity: Severity = Severity.SAFE
    nudity: NudityLevel = NudityLevel.NONE
    flagged: bool = False
    reason: str = ""


class NudeNetScanner:
    def __init__(
        self,
        threshold: float = 0.30,
        inference_resolution: int = 640,
        model_path: Optional[str] = None,
    ) -> None:
        self.threshold = threshold
        self.inference_resolution = inference_resolution
        self.model_path = model_path
        self._detector = None

    def _ensure_loaded(self) -> None:
        if self._detector is None:
            from nudenet import NudeDetector
            from pathlib import Path
            if self.model_path and Path(self.model_path).exists():
                self._detector = NudeDetector(
                    model_path=self.model_path,
                    inference_resolution=self.inference_resolution,
                )
            else:
                self._detector = NudeDetector(inference_resolution=self.inference_resolution)

    def _scan_once(self, bgr, resolution: int) -> NudeNetResult:
        from nudenet import NudeDetector
        from pathlib import Path

        if self.model_path and Path(self.model_path).exists():
            detector = NudeDetector(
                model_path=self.model_path,
                inference_resolution=resolution,
            )
        else:
            detector = NudeDetector(inference_resolution=resolution)

        detections = detector.detect(bgr)
        labels: list[str] = []
        max_score = 0.0
        for det in detections:
            label = det.get("class", "")
            score = float(det.get("score", 0.0))
            if score >= self.threshold:
                labels.append(label)
                max_score = max(max_score, score)

        if not labels:
            return NudeNetResult(reason="NudeNet: clean")

        mod_labels = [l for l in labels if l not in NON_MODERATION_LABELS]
        if not mod_labels:
            return NudeNetResult(
                labels=labels,
                max_score=max_score,
                reason=f"NudeNet: non-moderation only ({', '.join(labels[:2])})",
            )

        severity = Severity.SUGGESTIVE
        nudity = NudityLevel.PARTIAL
        if any(l in EXPLICIT_LABELS for l in mod_labels):
            severity = Severity.EXPLICIT
            nudity = NudityLevel.FULL

        return NudeNetResult(
            labels=mod_labels, max_score=max_score,
            severity=severity, nudity=nudity, flagged=True,
            reason=f"NudeNet: {', '.join(mod_labels[:3])} ({max_score:.2f})",
        )

    def scan_bgr(self, bgr) -> NudeNetResult:
        self._ensure_loaded()
        result = self._scan_once(bgr, self.inference_resolution)
        if (
            not result.flagged
            and self.inference_resolution < 640
        ):
            retry = self._scan_once(bgr, 640)
            if retry.flagged:
                return retry
        return result
