from __future__ import annotations

import re
from typing import Optional

from .lgbt import infer_lgbt_from_text, merge_lgbt_contexts, resolve_orientation_with_lgbt
from .nudenet_tier import EXPLICIT_LABELS, NON_MODERATION_LABELS, NudeNetResult
from .schema import FrameAnalysis, LgbtContext, NudityLevel, Orientation, SEVERITY_RANK, Severity

SAFE_KEYWORDS = (
    "mountain", "landscape", "everest", "snow", "office", "computer",
    "typewriter", "museum", "keyboard", "building", "forest", "lake",
    "car", "road", "food", "cat", "dog", "flower", "architecture",
    "wedding", "bride", "groom", "family portrait", "soccer", "stadium",
    "football", "sport", "period clothing", "photograph",
)

UNSAFE_KEYWORDS = (
    "nude", "naked", "genital", "porn", "sex act", "intercourse",
    "bikini", "underwear", "lingerie", "corset", "kiss", "kissing", "topless",
    "shirtless", "bare chest", "swim trunks",
)

COUPLE_CUES = (
    "kiss", "kissing", "couple", "embrac", "hug", "hugging", "pride",
    "partner", "dating", "romantic", "lovers",
)

BEACH_SOLO_CUES = ("bikini", "swimsuit", "beach", "sunbath", "swimming", "ball", "surf")

ORIENTATION_PATTERNS = [
    (Orientation.LESBIAN, (
        r"\btwo women\b", r"\bwomen kissing\b", r"\bfemale couple\b", r"\blesbian\b",
        r"\bwomen hugging\b", r"\bwomen embracing\b", r"\bwoman kissing a woman\b",
    )),
    (Orientation.GAY, (
        r"\btwo men\b", r"\bmen kissing\b", r"\bmale couple\b", r"\bgay\b",
        r"\bmen hugging\b", r"\bmen embracing\b", r"\bman kissing a man\b",
    )),
    (Orientation.HETEROSEXUAL, (
        r"\bman kissing a woman\b", r"\bwoman kissing a man\b",
        r"\bman and woman kissing\b", r"\bheterosexual\b",
    )),
]


def _should_infer_orientation(description: str, severity: Severity) -> bool:
    """Orientasi hanya jika ada konteks pasangan — skip bikini/beach solo."""
    if severity == Severity.SAFE:
        return False
    t = description.lower()
    if any(k in t for k in BEACH_SOLO_CUES):
        return any(k in t for k in COUPLE_CUES)
    return True


def _cap_severity(description: str, severity: Severity, nudenet: NudeNetResult) -> Severity:
    """Cegah kiss/partial telanjang naik ke explicit tanpa genitalia."""
    if severity != Severity.EXPLICIT:
        return severity
    if any(l in EXPLICIT_LABELS for l in nudenet.labels):
        return severity
    t = description.lower()
    hard = ("genital", "porn", "intercourse", "sex act", "fully naked")
    if any(w in t for w in hard):
        return severity
    if "kiss" in t or "kissing" in t:
        return Severity.SUGGESTIVE
    if nudenet.flagged and not any(l in EXPLICIT_LABELS for l in nudenet.labels):
        return Severity.SUGGESTIVE
    return severity


def _higher_severity(a: Severity, b: Severity) -> Severity:
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b


def infer_orientation(description: str, severity: Severity) -> Orientation:
    if severity == Severity.SAFE:
        return Orientation.NONE
    t = description.lower()

    if any(k in t for k in ("painting", "statue", "sculpture", "classical art", "botticelli", "michelangelo", "museum")):
        return Orientation.NONE

    if re.search(r"\bman\b.{0,80}\bwoman\b", t) or re.search(r"\bwoman\b.{0,80}\bman\b", t):
        if any(k in t for k in COUPLE_CUES):
            return Orientation.HETEROSEXUAL

    for orient, patterns in ORIENTATION_PATTERNS:
        for pat in patterns:
            if re.search(pat, t):
                return orient
    if "man" in t and "woman" in t and any(k in t for k in ("kiss", "hug", "embrac")):
        return Orientation.HETEROSEXUAL
    if any(k in t for k in ("two women", "women kissing", "both women", "person1: woman", "person 1: woman")):
        return Orientation.LESBIAN
    if t.count("woman") >= 2 and "man" not in t:
        return Orientation.LESBIAN
    if any(k in t for k in ("two men", "men kissing", "both men", "person1: man", "person 1: man")):
        return Orientation.GAY
    if t.count("man") >= 2 and "woman" not in t:
        return Orientation.GAY
    if "pride" in t and "women" in t:
        return Orientation.LESBIAN
    if "pride" in t and "men" in t and "women" not in t:
        return Orientation.GAY
    return Orientation.NONE


def _is_pride_merch_only(description: str, nudenet: NudeNetResult) -> bool:
    t = description.lower()
    if any(l in EXPLICIT_LABELS for l in nudenet.labels):
        return False
    mod_labels = [l for l in nudenet.labels if l not in NON_MODERATION_LABELS]
    if mod_labels:
        return False
    has_merch = (
        any(k in t for k in (
            "rainbow flag", "pride flag", "lgbt flag", "holds up a flag", "holding a flag",
            "waving a flag", "waving flag", "rainbow shirt", "rainbow t-shirt", "pride shirt",
            "pride t-shirt", "pride ride t-shirt", "rainbow pride shirt", "rainbow flags",
        ))
        or ("rainbow" in t and any(k in t for k in ("shirt", "t-shirt", "flag", "merch", "banner")))
        or ("pride" in t and any(k in t for k in ("shirt", "t-shirt", "flag", "merch")))
    )
    if any(k in t for k in ("nude beach", "naturist", "nudist", "nude people", "naked people", "walking in the water")):
        return False
    if "water" in t and any(k in t for k in ("men", "women", "people", "beach")):
        return False
    return (
        has_merch
        and any(k in t for k in ("pride", "rainbow", "lgbt"))
        and not any(k in t for k in ("kiss", "kissing", "nude", "naked", "bikini", "topless", "statue", "sculpture"))
    )


def infer_acts(description: str) -> list[str]:
    t = description.lower()
    acts: list[str] = []
    if "kiss" in t:
        acts.append("kissing")
    if "bikini" in t or "swimsuit" in t:
        acts.append("bikini")
    if "corset" in t or "lingerie" in t:
        acts.append("lingerie")
    if "shirtless" in t or "bare chest" in t or "swim trunks" in t:
        acts.append("shirtless")
    if any(k in t for k in ("nude", "naked", "topless")):
        acts.append("nudity")
    if any(k in t for k in ("sexual", "intercourse")):
        acts.append("sexual_contact")
    return acts


BEACH_COVERED_LABELS = (
    "FEMALE_GENITALIA_COVERED",
    "BUTTOCKS_COVERED",
    "FEMALE_BREAST_COVERED",
    "BELLY_COVERED",
)


def _nudenet_beach_suggestive(description: str, nudenet: NudeNetResult) -> bool:
    if not any(l in nudenet.labels for l in BEACH_COVERED_LABELS):
        return False
    t = description.lower()
    return any(k in t for k in ("beach", "swim", "pool", "bikini", "swimsuit", "sunbath", "water"))


def infer_from_description(description: str, nudenet: NudeNetResult) -> FrameAnalysis:
    t = description.lower()

    if any(k in t for k in SAFE_KEYWORDS) and not any(k in t for k in UNSAFE_KEYWORDS):
        if not nudenet.flagged and not _nudenet_beach_suggestive(description, nudenet):
            return FrameAnalysis(
                severity=Severity.SAFE, nudity=NudityLevel.NONE,
                orientation=Orientation.NONE, lgbt=infer_lgbt_from_text(description),
                acts=[], confidence=0.88,
                reason=f"Rules: safe — {description[:80]}",
            )

    severity = Severity.SAFE
    nudity = NudityLevel.NONE

    if nudenet.flagged:
        severity = nudenet.severity
        nudity = nudenet.nudity

    if any(k in t for k in ("genital", "porn", "sex act", "intercourse")):
        severity = Severity.EXPLICIT
        nudity = NudityLevel.FULL
    elif any(k in t for k in ("nude man", "nude woman", "fully naked")):
        severity = _higher_severity(severity, Severity.EXPLICIT)
        nudity = NudityLevel.FULL
    if any(k in t for k in ("nude beach", "naturist", "nudist", "nude people", "naked people")):
        severity = _higher_severity(severity, Severity.SUGGESTIVE)
        nudity = NudityLevel.PARTIAL if nudity == NudityLevel.NONE else nudity
        if severity == Severity.EXPLICIT and not any(l in EXPLICIT_LABELS for l in nudenet.labels):
            severity = Severity.SUGGESTIVE
            nudity = NudityLevel.PARTIAL
    elif any(k in t for k in ("bikini", "underwear", "lingerie", "corset", "kiss", "kissing", "topless", "nude", "naked", "hugging", "embracing", "shirtless", "swim trunks", "bare chest")):
        severity = _higher_severity(severity, Severity.SUGGESTIVE)
        if nudity == NudityLevel.NONE:
            nudity = NudityLevel.PARTIAL
    elif "couple" in t and any(k in t for k in ("danc", "kiss", "hug", "embrac")):
        severity = _higher_severity(severity, Severity.SUGGESTIVE)
    elif any(k in t for k in ("painting", "classical art", "botticelli", "birth of venus", "sculpture", "michelangelo", "statue")):
        severity = _higher_severity(severity, Severity.SUGGESTIVE)
        nudity = NudityLevel.PARTIAL

    if _nudenet_beach_suggestive(description, nudenet):
        severity = _higher_severity(severity, Severity.SUGGESTIVE)
        if nudity == NudityLevel.NONE:
            nudity = NudityLevel.PARTIAL

    acts = infer_acts(description)
    if nudenet.flagged and "nudity" not in acts:
        acts.append("nudity")

    # Pride merch / bendera saja — bukan suggestive tanpa nudity/intimacy
    pride_only = _is_pride_merch_only(description, nudenet)
    if pride_only and severity != Severity.SAFE:
        severity = Severity.SAFE
        nudity = NudityLevel.NONE

    orientation = (
        infer_orientation(description, severity)
        if _should_infer_orientation(description, severity)
        else Orientation.NONE
    )
    lgbt = infer_lgbt_from_text(description)
    orientation = resolve_orientation_with_lgbt(orientation, lgbt, [description], severity)
    nudity = _cap_nudity(description, nudity, nudenet)

    return FrameAnalysis(
        severity=severity, nudity=nudity, orientation=orientation,
        lgbt=lgbt, acts=acts, confidence=0.85,
        reason=f"Rules: {description[:100]}",
    )


def _cap_nudity(description: str, nudity: NudityLevel, nudenet: NudeNetResult) -> NudityLevel:
    """Kurangi false positive NudeNet pada ciuman/pegangan dengan pakaian."""
    t = description.lower()
    has_nude_words = any(w in t for w in (
        "nude", "naked", "topless", "bikini", "breast", "genital",
        "shirtless", "swim trunks", "bare chest",
    ))
    if any(k in t for k in ("painting", "sculpture", "art", "venus", "david", "classical", "botticelli")):
        return nudity if nudity != NudityLevel.NONE else NudityLevel.PARTIAL
    if has_nude_words:
        return nudity
    if "danc" in t:
        return NudityLevel.NONE
    if not nudenet.flagged:
        return NudityLevel.NONE if nudity != NudityLevel.FULL else nudity
    strong = any(l in EXPLICIT_LABELS for l in nudenet.labels)
    if strong:
        return nudity
    soft_only = all(
        l in ("BELLY_EXPOSED", "ARMPITS_EXPOSED", "FEET_EXPOSED", "MALE_BREAST_EXPOSED")
        for l in nudenet.labels
    )
    if soft_only or "kiss" in t:
        return NudityLevel.NONE
    return nudity


def merge_results(
    llm: FrameAnalysis,
    rules: FrameAnalysis,
    nudenet: NudeNetResult,
    description: str,
) -> FrameAnalysis:
    sev = _higher_severity(llm.severity, rules.severity)
    if nudenet.flagged:
        nn_sev = nudenet.severity
        if nn_sev == Severity.EXPLICIT and not any(l in EXPLICIT_LABELS for l in nudenet.labels):
            nn_sev = Severity.SUGGESTIVE
        sev = _higher_severity(sev, nn_sev)
    sev = _cap_severity(description, sev, nudenet)

    nudity_rank = {NudityLevel.NONE: 0, NudityLevel.PARTIAL: 1, NudityLevel.FULL: 2}
    nudity = llm.nudity
    for candidate in (rules.nudity, nudenet.nudity if nudenet.flagged else NudityLevel.NONE):
        if nudity_rank[candidate] > nudity_rank[nudity]:
            nudity = candidate
    nudity = _cap_nudity(description, nudity, nudenet)
    if _is_pride_merch_only(description, nudenet):
        sev = Severity.SAFE
        nudity = NudityLevel.NONE
    t = description.lower()
    if any(k in t for k in ("nude beach", "naturist", "nudist")) and sev == Severity.EXPLICIT:
        if not any(l in EXPLICIT_LABELS for l in nudenet.labels):
            sev = Severity.SUGGESTIVE
            nudity = NudityLevel.PARTIAL

    orientation = llm.orientation
    if orientation == Orientation.NONE:
        orientation = rules.orientation
    if orientation == Orientation.NONE and sev != Severity.SAFE and _should_infer_orientation(description, sev):
        orientation = infer_orientation(description, sev)

    lgbt = merge_lgbt_contexts([llm.lgbt, rules.lgbt])
    orientation = resolve_orientation_with_lgbt(orientation, lgbt, [description], sev)

    acts = sorted(set(llm.acts + rules.acts))
    confidence = max(llm.confidence, rules.confidence)
    reason = rules.reason if SEVERITY_RANK[rules.severity] >= SEVERITY_RANK[llm.severity] else llm.reason

    return FrameAnalysis(
        severity=sev, nudity=nudity, orientation=orientation,
        lgbt=lgbt, acts=acts, confidence=confidence, reason=reason,
    )
