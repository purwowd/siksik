from __future__ import annotations

import re
from collections import Counter

from PIL import Image

from .lgbt import analyze_lgbt, resolve_orientation_with_lgbt
from .llama_backend import ExternalServer, LlamaServer
from .modes import DetectionMode
from .nudenet_tier import NudeNetResult
from .prescreen import pil_to_bgr
from .rules import (
    COUPLE_CUES,
    _should_infer_orientation,
    infer_from_description,
    infer_orientation,
    merge_results,
)
from .lgbt import _has_intimacy
from .schema import FrameAnalysis, LgbtContext, Orientation, Severity

ART_KEYWORDS = ("statue", "sculpture", "painting", "botticelli", "michelangelo", "venus", "museum", "classical art")


def _center_crop(img: Image.Image, ratio: float = 0.55) -> Image.Image:
    w, h = img.size
    nw, nh = max(1, int(w * ratio)), max(1, int(h * ratio))
    left, top = (w - nw) // 2, (h - nh) // 2
    return img.crop((left, top, left + nw, top + nh))


def _orientation_contradicts(orient: Orientation, *texts: str) -> bool:
    combined = " ".join(texts).lower()
    if orient == Orientation.LESBIAN:
        if "man" in combined and not re.search(r"woman.{0,120}woman", combined):
            if "two women" not in combined and combined.count("woman") < 2:
                return True
    if orient == Orientation.GAY:
        if "woman" in combined and not re.search(r"man.{0,120}man", combined):
            if "two men" not in combined and combined.count("man") < 2:
                return True
        if re.search(r"\bman\b.{0,80}\bwoman\b", combined) or re.search(r"\bwoman\b.{0,80}\bman\b", combined):
            if any(k in combined for k in COUPLE_CUES):
                return True
    return False


def _same_gender_from_text(text: str) -> Orientation | None:
    t = text.lower()
    if any(k in t for k in ART_KEYWORDS):
        return None
    if re.search(r"woman.{0,120}woman", t):
        return Orientation.LESBIAN
    if re.search(r"man.{0,120}man", t) and "woman" not in t:
        return Orientation.GAY
    if t.count("woman") >= 2 and "man" not in t:
        return Orientation.LESBIAN
    if t.count("man") >= 2 and "woman" not in t:
        return Orientation.GAY
    return None


class FrameClassifier:
    def __init__(
        self,
        server: LlamaServer | ExternalServer,
        mode: DetectionMode = DetectionMode.BALANCED,
        crop_ratio: float = 0.50,
    ) -> None:
        self.server = server
        self.mode = mode
        self.crop_ratio = crop_ratio

    def _vote_orientation(self, texts: list[str], severity: Severity) -> Orientation:
        for text in texts:
            same = _same_gender_from_text(text)
            if same is not None:
                return same

        votes: list[Orientation] = []
        for text in texts:
            orient = infer_orientation(text, severity)
            if orient != Orientation.NONE:
                votes.append(orient)

        if not votes:
            return Orientation.NONE

        for preferred in (Orientation.LESBIAN, Orientation.GAY, Orientation.HETEROSEXUAL):
            if preferred in votes:
                return preferred
        return Counter(votes).most_common(1)[0][0]

    def _resolve_orientation_full(
        self, img: Image.Image, description: str, severity: Severity, current: Orientation,
    ) -> tuple[Orientation, list[str]]:
        extra_texts: list[str] = []
        if severity == Severity.SAFE:
            return Orientation.NONE, extra_texts

        dl = description.lower()
        if not _has_intimacy(description) and any(k in dl for k in ("water", "beach", "walking")):
            if "rainbow" in dl or "pride" in dl:
                return Orientation.NONE, extra_texts

        if any(k in dl for k in ART_KEYWORDS):
            return Orientation.NONE, extra_texts

        if not _should_infer_orientation(description, severity):
            return Orientation.NONE, extra_texts

        crop = _center_crop(img, self.crop_ratio)
        crop_texts = [
            self.server.gender_count_describe(crop),
            self.server.describe_image(crop),
            description,
        ]
        extra_texts.extend(crop_texts[:2])

        if "two people" in dl and "kiss" in dl and "man" not in dl and "woman" not in dl:
            for text in crop_texts[:2]:
                same = _same_gender_from_text(text)
                if same is not None:
                    return same, extra_texts

        crop_orient = self._vote_orientation(crop_texts, severity)

        if _orientation_contradicts(crop_orient, description, crop_texts[0], crop_texts[1]):
            crop_orient = Orientation.NONE

        gc = crop_texts[0].lower()
        if crop_orient == Orientation.HETEROSEXUAL and (
            "two women" in gc or (gc.count("woman") >= 2 and "man" not in gc)
        ):
            return Orientation.LESBIAN, extra_texts
        if crop_orient == Orientation.HETEROSEXUAL and (
            "two men" in gc or (gc.count("man") >= 2 and "woman" not in gc)
        ):
            return Orientation.GAY, extra_texts

        if crop_orient not in (Orientation.NONE, Orientation.HETEROSEXUAL):
            return crop_orient, extra_texts

        if current not in (Orientation.NONE, Orientation.HETEROSEXUAL):
            return current, extra_texts

        followup = self.server.followup_describe(img)
        extra_texts.append(followup)
        alt = self._vote_orientation([followup, description, gc], severity)
        if alt != Orientation.NONE and not _orientation_contradicts(alt, description, followup):
            return alt, extra_texts

        final = crop_orient if crop_orient != Orientation.NONE else infer_orientation(description, severity)
        return final, extra_texts

    def _apply_lgbt(
        self,
        merged: FrameAnalysis,
        img: Image.Image,
        description: str,
        extra_texts: list[str],
    ) -> FrameAnalysis:
        bgr = pil_to_bgr(img)
        texts = [description, *extra_texts]

        if self.mode == DetectionMode.FULL:
            lgbt_desc = self.server.lgbt_describe(img)
            texts.append(lgbt_desc)

        lgbt = analyze_lgbt(bgr, texts)
        merged.lgbt = lgbt
        merged.orientation = resolve_orientation_with_lgbt(
            merged.orientation, lgbt, texts, merged.severity,
        )
        return merged

    def classify(self, img: Image.Image, nudenet: NudeNetResult) -> FrameAnalysis:
        description = self.server.describe_image(img)
        rules_result = infer_from_description(description, nudenet)
        hint = ", ".join(nudenet.labels[:4]) if nudenet.flagged else ""
        llm_result = self.server.classify_from_description(description, nudenet_hint=hint, img=img)
        merged = merge_results(llm_result, rules_result, nudenet, description)

        extra_texts: list[str] = []
        if self.mode == DetectionMode.FULL:
            merged.orientation, extra_texts = self._resolve_orientation_full(
                img, description, merged.severity, merged.orientation,
            )
        elif merged.severity != Severity.SAFE and _should_infer_orientation(description, merged.severity):
            merged.orientation = infer_orientation(description, merged.severity)
            if merged.orientation == Orientation.NONE:
                merged.orientation = rules_result.orientation
        else:
            merged.orientation = Orientation.NONE

        return self._apply_lgbt(merged, img, description, extra_texts)
