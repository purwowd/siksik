from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from PIL import Image

from .indonesian_meme import (
    analyze_indonesian_meme,
    detect_meme_layout,
    enrich_layout_from_text,
    meme_needs_band_transcribe,
    meme_needs_extra_vision,
    meme_needs_ocr,
    merge_meme_contexts,
    merge_ocr_overlay,
    finalize_meme_context,
    parse_meme_json_response,
    _layout_needs_ocr_rerun,
    _layout_worth_ocr,
)
from .config import MemeConfig
from .meme_bands import build_band_strip, parse_transcribe_lines
from .meme_ocr import OcrConfig, _ocr_line_useful, extract_meme_ocr, ocr_available, ocr_lines_actionable, prepare_ocr_bgr
from .lgbt import _has_intimacy, analyze_lgbt, resolve_orientation_with_lgbt
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
        meme_config: MemeConfig | None = None,
        ocr_config: OcrConfig | None = None,
    ) -> None:
        self.server = server
        self.mode = mode
        self.crop_ratio = crop_ratio
        self.meme_config = meme_config or MemeConfig()
        self.ocr_config = ocr_config or OcrConfig(
            enabled=self.meme_config.ocr_enabled,
            lazy=self.meme_config.ocr_lazy,
            lang=self.meme_config.ocr_lang,
            vlm_band_fallback=self.meme_config.ocr_vlm_band_fallback,
            full_res=self.meme_config.ocr_full_res,
            max_size=self.meme_config.ocr_max_size,
            workers=self.meme_config.ocr_workers,
        )
        self._ocr_pool = ThreadPoolExecutor(max_workers=1)

    def _ocr_bgr(self, bgr, ocr_bgr=None):
        if self.ocr_config.full_res and ocr_bgr is not None:
            return prepare_ocr_bgr(ocr_bgr, self.ocr_config.max_size)
        return bgr

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

    def _start_ocr_future(
        self,
        bgr_vlm,
        layout: list[str],
        ocr_full=None,
    ) -> Optional[Future[list[str]]]:
        if not self.meme_config.enabled or not ocr_available(self.ocr_config):
            return None
        if not _layout_worth_ocr(layout):
            return None
        return self._ocr_pool.submit(
            extract_meme_ocr,
            bgr_vlm,
            self.ocr_config,
            layout,
            ocr_full,
        )

    def _apply_indonesian_meme(
        self,
        merged: FrameAnalysis,
        img: Image.Image,
        description: str,
        extra_texts: list[str],
        *,
        layout: Optional[list[str]] = None,
        ocr_future: Optional[Future[list[str]]] = None,
        ocr_bgr=None,
        reason: str = "",
    ) -> FrameAnalysis:
        bgr = pil_to_bgr(img)
        ocr_src = self._ocr_bgr(bgr, ocr_bgr)
        texts = [description, *extra_texts]
        if reason and reason not in texts:
            texts.append(reason)

        if not self.meme_config.enabled:
            merged.indonesian_meme = analyze_indonesian_meme(bgr, texts)
            return merged

        if layout is None:
            layout = detect_meme_layout(bgr)
        initial_layout = list(layout)
        layout = enrich_layout_from_text(initial_layout, texts)
        ctx = analyze_indonesian_meme(bgr, texts)
        visual_figures = list(ctx.public_figures)

        run_ocr = (
            ocr_available(self.ocr_config)
            and (not self.ocr_config.lazy or meme_needs_ocr(ctx, bgr, layout, texts))
        )
        if run_ocr:
            ocr_lines: list[str] = []
            needs_rerun = _layout_needs_ocr_rerun(initial_layout, layout)
            if ocr_future is not None and not needs_rerun:
                try:
                    ocr_lines = ocr_future.result()
                except Exception:
                    ocr_lines = []
            if not ocr_lines:
                ocr_lines = extract_meme_ocr(bgr, self.ocr_config, layout, ocr_src)
            elif needs_rerun and "speech_bubble" in layout:
                extra = extract_meme_ocr(bgr, self.ocr_config, ["speech_bubble"], ocr_src)
                ocr_lines = list(dict.fromkeys([*ocr_lines, *extra]))
            merge_ocr = ocr_lines and (
                ocr_lines_actionable(ocr_lines)
                or (ctx.present and any(_ocr_line_useful(ln, min_conf=35) for ln in ocr_lines))
            )
            if merge_ocr:
                texts.extend(ocr_lines)
                ctx = merge_ocr_overlay(ctx, ocr_lines, layout)

        use_vlm_band = (
            not self.ocr_config.enabled or self.ocr_config.vlm_band_fallback
        )
        if use_vlm_band and meme_needs_band_transcribe(ctx, bgr):
            strip = build_band_strip(bgr)
            if strip is not None:
                band_raw = self.server.meme_transcribe_strip(strip)
                band_lines = parse_transcribe_lines(band_raw)
                if band_lines:
                    texts.extend(band_lines)
                    ctx = analyze_indonesian_meme(bgr, texts)

        if meme_needs_extra_vision(ctx, bgr, layout, texts):
            json_ctx = parse_meme_json_response(self.server.meme_analyze_json(img))
            if json_ctx.present:
                ctx = merge_meme_contexts([ctx, json_ctx])

        ctx = finalize_meme_context(
            ctx,
            layout=layout,
            description=f"{description} {reason}".strip(),
            visual_figures=visual_figures,
        )
        merged.indonesian_meme = ctx
        return merged

    def classify(self, img: Image.Image, nudenet: NudeNetResult, ocr_bgr=None) -> FrameAnalysis:
        bgr = pil_to_bgr(img)
        ocr_src = self._ocr_bgr(bgr, ocr_bgr)
        layout = detect_meme_layout(bgr) if self.meme_config.enabled else []
        ocr_future = self._start_ocr_future(bgr, layout, ocr_src if ocr_bgr is not None else None)

        hint = ", ".join(nudenet.labels[:4]) if nudenet.flagged else ""
        description, llm_result = self.server.describe_and_classify(img, nudenet_hint=hint)
        rules_result = infer_from_description(description, nudenet)
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

        merged = self._apply_lgbt(merged, img, description, extra_texts)
        return self._apply_indonesian_meme(
            merged, img, description, extra_texts,
            layout=layout, ocr_future=ocr_future, ocr_bgr=ocr_bgr,
            reason=merged.reason or "",
        )
