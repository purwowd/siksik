"""Portable zero-shot visual signals for the shared content taxonomy."""

from __future__ import annotations

import logging
import math
import threading
from collections import Counter
from pathlib import Path

from app.core.config import settings
from app.models.schemas import Layer
from app.services.content_policy import (
    CONTENT_CATEGORY_LABELS,
    DEMONSTRATION,
    EXTREMISM,
    LGBT_CONTENT,
    POLITICAL_CAMPAIGN,
    POLITICAL_MEME,
)

log = logging.getLogger(__name__)

CONTENT_VISUAL_REVISION = "general-structure-prompt-banks-v9"

# Each category is compared with a hard negative in the same forward pass.
# This is deliberately content-based and never classifies a person's identity
# or sexual orientation from appearance.
_PROMPT_BANKS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        LGBT_CONTENT,
        (
            "a clearly visible LGBTQ rainbow pride flag with colored stripes",
            "a clearly visible transgender pride flag with blue pink and white stripes",
            "an event poster or march displaying explicit LGBTQ pride words flags or symbols",
            "a clearly readable LGBTQ pride slogan beside an identifiable pride symbol",
        ),
        (
            "an ordinary rainbow in the sky without a flag",
            "colorful clothing artwork toys or application graphics that are not a pride flag",
            "an ordinary national regional or decorative flag without LGBTQ pride symbols",
        ),
    ),
    (
        POLITICAL_MEME,
        (
            "a political image macro using an ironic punchline to criticize a government politician party or public policy",
            "a parody caricature or manipulated image mocking a public official political party or government decision",
            "a before-and-after political satire contrasting an official promise with harmful or absurd consequences",
            "a visual wordplay or political cartoon using irony to criticize public policy without requiring a politician portrait",
        ),
        (
            "a neutral news screenshot reporting politics without a meme punchline",
            "an official government infographic or factual quote card",
            "an election opinion poll campaign card or ordinary political poster without satire",
            "an ordinary social media screenshot personal joke or motivational quote without a government or policy target",
        ),
    ),
    (
        POLITICAL_CAMPAIGN,
        (
            "an Indonesian election campaign poster with candidate name ballot number and party logo",
            "a candidate rally displaying campaign banners party branding and calls to vote",
            "a campaign billboard ballot paper or volunteer event supporting a specific candidate",
            "a social media endorsement explicitly asking people to vote for a candidate party or ballot number",
        ),
        (
            "a neutral election news report without a call to vote",
            "an official portrait government ceremony or public service event without campaigning",
            "an ordinary group photo or crowd without candidate numbers party logos or campaign material",
            "a political satire opinion poll or policy discussion without a call to vote or candidate endorsement",
        ),
    ),
    (
        DEMONSTRATION,
        (
            "a street protest with demand placards banners and chanting demonstrators",
            "a labor or student demonstration with megaphones speeches and protest signs",
            "a protest march facing police barriers while carrying written demands",
            "a strike sit-in blockade or public rally visibly expressing protest demands",
        ),
        (
            "a music concert or sports crowd without protest demands",
            "a political candidate campaign rally rather than a protest",
            "a ceremony religious procession queue or ordinary public gathering",
            "a traffic jam disaster evacuation or police event without protest signs or demands",
        ),
    ),
    (
        EXTREMISM,
        (
            "extremist recruitment or propaganda displaying an identifiable organization symbol",
            "a propaganda poster praising or recruiting for a terrorist extremist organization",
            "an identifiable extremist organization flag used in a supportive propaganda context",
            "a militant pledge recruitment message or glorification tied to a named extremist organization",
        ),
        (
            "a neutral news report documentary or history lesson showing extremist material critically",
            "an ordinary national flag religious event or religious calligraphy",
            "a generic military emblem fictional logo or unidentified symbol without extremist propaganda",
            "a crime report war photograph or security warning that does not praise recruit or support extremists",
        ),
    ),
)

# Generic political-meme prompts also score normal news cards highly because
# both contain a politician and overlaid text. This narrower pair is evaluated
# in the same CLIP forward pass and only confirms a visibly manipulated parody.
# It recovers edited memes when OCR misses stylized or sparse text without
# turning an ordinary Prabowo/Jokowi news image into a meme finding.
_MANIPULATED_POLITICAL_MEME_PROMPTS: tuple[tuple[str, ...], tuple[str, ...]] = (
    (
        "a public official face-swapped or transformed into an unrelated historical fictional or cultural character for political satire",
        "an exaggerated caricature of a politician with deliberate facial body or costume distortion used as a joke",
        "a composite political parody visibly changing a public figure's age clothing body role or identity",
        "an AI-generated mocking depiction of a government official that is visibly different from an authentic news photograph",
    ),
    (
        "an authentic neutral news photograph of a politician without satire or digital manipulation",
        "a normal factual quote card showing an unedited photograph of a public official",
        "an ordinary political opinion poll or campaign graphic using authentic unedited portraits",
        "an official portrait ceremony press conference or documentary photograph without parody",
    ),
)

# Satire does not always contain a politician's face. Environmental/policy
# memes often communicate the criticism through a visual before/after contrast
# and wordplay. This independent pair confirms that observable meme structure
# while rejecting neutral reporting, educational graphics, and opinion polls.
_EXPLICIT_POLITICAL_SATIRE_PROMPTS: tuple[tuple[str, ...], tuple[str, ...]] = (
    (
        "a visual pun changing a familiar proverb slogan or headline into criticism of government or public policy",
        "a before-and-after satire juxtaposing environmental economic infrastructure or public-service harm with a policy outcome",
        "an ironic political poster contrasting an official promise with the opposite real-world consequence",
        "a cartoon or image macro with a punchline criticizing a government decision even when no politician face is shown",
    ),
    (
        "a neutral factual news report about environment economy infrastructure or public services without satire",
        "an ordinary educational infographic documentary photograph or data chart without an ironic punchline",
        "a political opinion poll campaign card or factual government announcement without satire",
        "an unrelated joke advertisement product comparison or motivational quote without a public-policy target",
    ),
)

_model = None
_processor = None
_model_id: str | None = None
_load_failed = False
_MODEL_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def reset_model() -> None:
    global _model, _processor, _model_id, _load_failed
    _model = None
    _processor = None
    _model_id = None
    _load_failed = False


def _get_configured_model():
    global _model, _processor, _model_id, _load_failed
    model_id = (settings.content_visual_model or "").strip()
    if not model_id:
        return None, None
    if _model is not None and _model_id == model_id:
        return _model, _processor
    if _load_failed:
        return None, None
    with _MODEL_LOCK:
        if _model is not None and _model_id == model_id:
            return _model, _processor
        if _load_failed:
            return None, None
        try:
            import torch
            from transformers import AutoModel, AutoProcessor

            kwargs = {
                "local_files_only": bool(settings.content_models_local_only),
                "trust_remote_code": False,
            }
            processor = AutoProcessor.from_pretrained(model_id, **kwargs)
            dtype = torch.float16 if _device() == "cuda" else torch.float32
            model = AutoModel.from_pretrained(model_id, torch_dtype=dtype, **kwargs)
            model.to(_device())
            model.eval()
            _model = model
            _processor = processor
            _model_id = model_id
            return _model, _processor
        except Exception as exc:
            # Missing optional weights must degrade to the existing CLIP path, not
            # block a running acquisition while trying to reach the network.
            log.warning("Content visual model unavailable (%s): %s", model_id, exc)
            _load_failed = True
            return None, None


def _score_configured(path: Path, prompts: list[str]) -> list[float] | None:
    model, processor = _get_configured_model()
    if model is None or processor is None:
        return None
    try:
        import torch
        from PIL import Image

        with Image.open(path) as image_file:
            image = image_file.convert("RGB")
            image.thumbnail((384, 384))
        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        inputs = {key: value.to(_device()) for key, value in inputs.items()}
        from app.services.inference_guard import gpu_inference_slot

        with _INFER_LOCK, gpu_inference_slot(), torch.no_grad():
            output = model(**inputs)
        logits = getattr(output, "logits_per_image", None)
        if logits is None:
            return None
        return [float(value) for value in logits[0].detach().float().cpu().tolist()]
    except Exception as exc:
        log.debug("Content visual inference skip %s: %s", path.name, exc)
        return None


def _score(path: Path, prompts: list[str]) -> tuple[list[float] | None, str]:
    configured = (settings.content_visual_model or "").strip()
    if configured:
        scores = _score_configured(path, prompts)
        if scores is not None:
            return scores, configured.split("/")[-1]
    try:
        from app.services.clip_tokoh import score_image_prompts

        return score_image_prompts(path, prompts), settings.clip_tokoh_model.split("/")[-1]
    except Exception as exc:
        log.debug("Content visual fallback unavailable: %s", exc)
        return None, "unavailable"


def _paired_probability(positive: float, negative: float) -> float:
    delta = max(-30.0, min(30.0, positive - negative))
    return 1.0 / (1.0 + math.exp(-delta))


def _softmax_shares(logits: list[float]) -> list[float]:
    if not logits:
        return []
    peak = max(logits)
    weights = [math.exp(max(-60.0, min(0.0, value - peak))) for value in logits]
    denominator = sum(weights) or 1.0
    return [value / denominator for value in weights]


def _ensemble_score(values: list[float]) -> float:
    """Average the two strongest paraphrases so one odd prompt cannot dominate."""
    if not values:
        return float("-inf")
    strongest = sorted(values, reverse=True)[: min(2, len(values))]
    return sum(strongest) / len(strongest)


def _specialized_probability(positives: list[float], negatives: list[float]) -> float:
    """Score a bank of distinct explicit subtypes against its hardest negative.

    Unlike category paraphrases, each positive here describes a different
    observable structure (for example face-swap versus visual wordplay). One
    very strong subtype is sufficient, but it still has to beat every hard
    negative. This improves unseen-format recall without averaging unrelated
    satire forms together.
    """
    if not positives or not negatives:
        return 0.0
    return _paired_probability(max(positives), max(negatives))


def _category_threshold(category: str) -> float:
    return {
        LGBT_CONTENT: float(settings.content_visual_threshold_lgbt),
        POLITICAL_MEME: float(settings.content_visual_threshold_political_meme),
        POLITICAL_CAMPAIGN: float(settings.content_visual_threshold_political_campaign),
        DEMONSTRATION: float(settings.content_visual_threshold_demonstration),
        EXTREMISM: float(settings.content_visual_threshold_extremism),
    }[category]


def _has_multicolor_flag_signal(path: Path) -> bool:
    """Cheap prerequisite for pride/trans-flag visual candidates.

    Text-only LGBT mentions remain covered by OCR/content policy. This gate
    prevents low-saturation application chrome and monochrome documents from
    escalating to a costly VL adjudicator merely because CLIP won a prompt
    pair.
    """
    try:
        from PIL import Image

        with Image.open(path) as source:
            image = source.convert("HSV")
            image.thumbnail((96, 96))
            pixels = list(image.getdata())
    except Exception:
        # Unit adapters and uncommon formats should retain the normal
        # candidate/confirmation behavior when pixels cannot be inspected.
        return True
    if not pixels:
        return False
    colorful_hues = [
        hue
        for hue, saturation, value in pixels
        if saturation >= 55 and value >= 55
    ]
    colorful_ratio = len(colorful_hues) / len(pixels)
    hue_bins = {hue // 32 for hue in colorful_hues}
    return colorful_ratio >= 0.04 and len(hue_bins) >= 2


def _flag_stripe_score(path: Path) -> float:
    """Return a local rainbow-flag stripe score without identifying a person.

    The previous whole-image axis score confused buildings, streets, and large
    vehicles with a striped flag. Sliding rectangular windows require several
    adjacent, locally coherent hue bands in one compact region instead.
    """
    try:
        from PIL import Image

        with Image.open(path) as source:
            image = source.convert("HSV")
            image.thumbnail((128, 128))
            width, height = image.size
            pixels = list(image.getdata())
    except Exception:
        return 0.0
    if not pixels or width < 8 or height < 8:
        return 0.0

    def pixel_bin(pixel: tuple[int, int, int]) -> int | None:
        hue, saturation, value = pixel
        if saturation < 45 or value < 55:
            return None
        return hue // 22

    bins = [pixel_bin(pixel) for pixel in pixels]

    def axis_score(lines: list[list[int | None]]) -> float:
        labels: list[int | None] = []
        purities: list[float] = []
        for line in lines:
            values = [value for value in line if value is not None]
            if len(values) < max(3, int(len(line) * 0.45)):
                labels.append(None)
                continue
            dominant, count = Counter(values).most_common(1)[0]
            purity = count / len(values)
            if purity < 0.62:
                labels.append(None)
                continue
            labels.append(dominant)
            purities.append(purity)
        coherent = [value for value in labels if value is not None]
        coverage = len(coherent) / max(1, len(lines))
        if coverage < 0.48 or len(set(coherent)) < 3:
            return 0.0
        compressed: list[int] = []
        for value in labels:
            if value is not None and (not compressed or compressed[-1] != value):
                compressed.append(value)
        transitions = min(1.0, max(0, len(compressed) - 1) / 3.0)
        diversity = min(1.0, len(set(coherent)) / 4.0)
        purity_score = sum(purities) / len(purities)
        return coverage * diversity * transitions * purity_score

    best = 0.0
    # Width:height near common flag proportions. Both axes are evaluated so a
    # portrait/rotated flag remains detectable.
    for window_width, window_height in (
        (16, 10),
        (24, 14),
        (32, 18),
        (48, 28),
        (64, 36),
        (96, 54),
    ):
        if window_width > width or window_height > height:
            continue
        step_x = max(2, window_width // 4)
        step_y = max(2, window_height // 4)
        x_values = list(range(0, width - window_width + 1, step_x))
        y_values = list(range(0, height - window_height + 1, step_y))
        if x_values[-1] != width - window_width:
            x_values.append(width - window_width)
        if y_values[-1] != height - window_height:
            y_values.append(height - window_height)
        for y0 in y_values:
            for x0 in x_values:
                rows = [
                    [bins[y * width + x] for x in range(x0, x0 + window_width)]
                    for y in range(y0, y0 + window_height)
                ]
                columns = [
                    [bins[y * width + x] for y in range(y0, y0 + window_height)]
                    for x in range(x0, x0 + window_width)
                ]
                best = max(best, axis_score(rows), axis_score(columns))
    return round(best, 4)


def analyze_image(path: Path) -> list[dict]:
    if not settings.content_detection_enabled or not settings.content_visual_enabled:
        return []
    category_prompts = [
        prompt
        for _category, positives, negatives in _PROMPT_BANKS
        for prompt in (*positives, *negatives)
    ]
    manipulated_positives, manipulated_negatives = _MANIPULATED_POLITICAL_MEME_PROMPTS
    satire_positives, satire_negatives = _EXPLICIT_POLITICAL_SATIRE_PROMPTS
    prompts = (
        category_prompts
        + list(manipulated_positives)
        + list(manipulated_negatives)
        + list(satire_positives)
        + list(satire_negatives)
    )
    logits, backend = _score(path, prompts)
    if logits is None or len(logits) != len(prompts):
        return []

    category_scores: list[tuple[str, float, float, str]] = []
    offset = 0
    for category, positives, negatives in _PROMPT_BANKS:
        positive_values = logits[offset : offset + len(positives)]
        offset += len(positives)
        negative_values = logits[offset : offset + len(negatives)]
        offset += len(negatives)
        best_prompt = positives[max(
            range(len(positive_values)),
            key=positive_values.__getitem__,
        )]
        category_scores.append(
            (
                category,
                _ensemble_score(positive_values),
                _ensemble_score(negative_values),
                best_prompt,
            )
        )

    manipulated_positive_values = logits[
        len(category_prompts) : len(category_prompts) + len(manipulated_positives)
    ]
    manipulated_negative_start = len(category_prompts) + len(manipulated_positives)
    manipulated_negative_values = logits[
        manipulated_negative_start : manipulated_negative_start
        + len(manipulated_negatives)
    ]
    manipulated_meme_probability = _specialized_probability(
        manipulated_positive_values,
        manipulated_negative_values,
    )
    satire_positive_start = manipulated_negative_start + len(manipulated_negatives)
    satire_positive_values = logits[
        satire_positive_start : satire_positive_start + len(satire_positives)
    ]
    satire_negative_values = logits[satire_positive_start + len(satire_positives) :]
    explicit_satire_probability = _specialized_probability(
        satire_positive_values,
        satire_negative_values,
    )

    candidates: list[tuple[float, float, dict]] = []
    base_threshold = float(settings.content_visual_threshold)
    aggregate_logits = [
        value
        for _category, positive, negative, _prompt in category_scores
        for value in (positive, negative)
    ]
    shares = _softmax_shares(aggregate_logits)
    min_share = float(settings.content_visual_min_share)
    stripe_score: float | None = None
    for index, (category, positive, negative, positive_prompt) in enumerate(category_scores):
        probability = _paired_probability(positive, negative)
        positive_share = shares[index * 2] if len(shares) == len(aggregate_logits) else 0.0
        threshold = max(base_threshold, _category_threshold(category))
        if probability < threshold or positive_share < min_share:
            continue
        if category == LGBT_CONTENT and not _has_multicolor_flag_signal(path):
            continue
        visual_confirmation = "ambiguous"
        if category == LGBT_CONTENT and settings.content_visual_fast_path_enabled:
            stripe_score = _flag_stripe_score(path)
            if (
                probability >= float(settings.content_visual_strong_threshold)
                and positive_share >= float(settings.content_visual_strong_min_share)
                and stripe_score >= float(settings.content_visual_flag_stripe_threshold)
            ):
                visual_confirmation = "explicit_flag"
        elif (
            settings.content_visual_fast_path_enabled
            and category == DEMONSTRATION
            and probability >= float(settings.content_visual_fast_demonstration_threshold)
            and positive_share >= float(settings.content_visual_min_share)
        ):
            visual_confirmation = "explicit_demonstration"
        elif (
            settings.content_visual_fast_path_enabled
            and category == POLITICAL_MEME
            and probability >= float(settings.content_visual_strong_threshold)
            and manipulated_meme_probability
            >= float(settings.content_visual_fast_manipulated_meme_threshold)
        ):
            visual_confirmation = "explicit_manipulated_political_meme"
        elif (
            settings.content_visual_fast_path_enabled
            and category == POLITICAL_MEME
            and probability >= float(settings.content_visual_strong_threshold)
            and explicit_satire_probability
            >= float(settings.content_visual_fast_satire_meme_threshold)
        ):
            visual_confirmation = "explicit_political_satire"
        candidates.append(
            (
                probability,
                positive_share,
                {
                    "category": category,
                    "label": CONTENT_CATEGORY_LABELS[category],
                    # This remains a candidate confidence, not a calibrated
                    # final probability. Final persistence requires support.
                    "confidence": round(min(0.94, probability), 3),
                    "layer_origin": Layer.L3.value,
                    # Internal policy hint. Persistence only consumes the
                    # standard finding fields, but the confirmation layer uses
                    # this to avoid a multi-second Qwen call for explicit flags.
                    "visual_confirmation": visual_confirmation,
                    "evidence": (
                        f"[visual-candidate:{backend}] {path.name} | "
                        f"pair={probability:.3f} share={positive_share:.3f} "
                        f"stripe={(stripe_score or 0.0):.3f} "
                        f"manipulated={manipulated_meme_probability:.3f} "
                        f"satire={explicit_satire_probability:.3f} | "
                        f"{positive_prompt}"
                    )[:320],
                },
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        finding
        for _, _, finding in candidates[: int(settings.content_visual_max_candidates)]
    ]


def status() -> dict:
    configured_model = (settings.content_visual_model or "").strip()
    fallback = settings.clip_tokoh_model if settings.clip_tokoh_enabled else None
    return {
        "name": "content-visual",
        "configured": bool(settings.content_detection_enabled and settings.content_visual_enabled),
        "model": configured_model or fallback,
        "local_files_only": bool(settings.content_models_local_only),
        "threshold": float(settings.content_visual_threshold),
        "category_thresholds": {
            category: _category_threshold(category)
            for category, _positives, _negatives in _PROMPT_BANKS
        },
        "min_share": float(settings.content_visual_min_share),
        "max_candidates": int(settings.content_visual_max_candidates),
        "requires_confirmation": bool(settings.content_visual_require_confirmation),
        "fast_path": bool(settings.content_visual_fast_path_enabled),
        "strong_threshold": float(settings.content_visual_strong_threshold),
        "strong_min_share": float(settings.content_visual_strong_min_share),
        "flag_stripe_threshold": float(settings.content_visual_flag_stripe_threshold),
        "fast_demonstration_threshold": float(settings.content_visual_fast_demonstration_threshold),
        "fast_manipulated_meme_threshold": float(
            settings.content_visual_fast_manipulated_meme_threshold
        ),
        "fast_satire_meme_threshold": float(
            settings.content_visual_fast_satire_meme_threshold
        ),
        "device": _device(),
        "revision": CONTENT_VISUAL_REVISION,
    }
