from __future__ import annotations

import json
import re

DESCRIBE_PROMPT = """Describe this image in 1-2 sentences for content moderation.
Must include: number of people, apparent gender (man/woman) of each person, clothing (including rainbow/pride/LGBT shirts or flags), actions (kissing/hugging/dancing), any pride/LGBT rainbow flags or flag colors visible, setting, any nudity.
Plain text only, no JSON."""

LGBT_VISION_PROMPT = """Look at LGBT/pride visual cues in this image only.
Reply plain text, one short sentence listing what you see:
- pride/rainbow/LGBT/trans/bi/lesbian/gay flags or flag colors
- rainbow or pride clothing/accessories
- pride parade or LGBT event context
If none visible, say "no lgbt cues"."""

CLASSIFY_FROM_DESC_PROMPT = """Classify this image description for adult content moderation.
Description: {description}
{hint}
Output ONLY JSON:
{{"severity":"safe|suggestive|explicit","nudity":"none|partial|full","orientation":"none|heterosexual|gay|lesbian|bisexual|other","acts":[],"confidence":0.0,"reason":""}}"""

ORIENTATION_PROMPT = """From this description, identify gender pairing if romantic/intimate content present.
Description: {description}
JSON only: {{"orientation":"none|heterosexual|gay|lesbian|bisexual|other","confidence":0.0}}"""

FOLLOWUP_DESCRIBE_PROMPT = """Look carefully at every person in this image.
State: how many men, how many women, what are they doing (kissing/hugging/dancing)?
One sentence, plain text."""

GENDER_COUNT_PROMPT = """For each person visible in this image, state their apparent gender (man or woman).
Format: "Person1: man/woman, Person2: man/woman. Action: kissing/hugging/dancing/none."
Be specific about gender. Plain text only."""

ORIENTATION_VISION_PROMPT = """Look at the people in this image. What is their gender pairing for any romantic/intimate interaction?
Reply JSON only: {{"orientation":"none|heterosexual|gay|lesbian|bisexual|other","confidence":0.0}}
gay=two men, lesbian=two women, heterosexual=man+woman, none=no people or no intimacy."""

RETRY_PROMPT = """Convert to moderation JSON from description:
{description}
JSON: {{"severity":"...","nudity":"...","orientation":"...","acts":[],"confidence":0.0,"reason":""}}"""

_ENUM_FIELDS = {
    "severity": {"safe", "suggestive", "explicit"},
    "nudity": {"none", "partial", "full"},
    "orientation": {"none", "heterosexual", "gay", "lesbian", "bisexual", "other"},
}

_ACT_KEYWORDS = {
    "kissing": ["kiss", "kissing"],
    "nudity": ["nude", "naked", "nudity", "topless"],
    "sexual_contact": ["sexual", "intercourse", "making love"],
    "lingerie": ["lingerie", "underwear"],
    "bikini": ["bikini", "swimsuit"],
}


def _pick_enum(field: str, text: str, default: str) -> str:
    pattern = rf'"{field}"\s*:\s*"(\w+)"'
    m = re.search(pattern, text, re.IGNORECASE)
    if m and m.group(1).lower() in _ENUM_FIELDS[field]:
        return m.group(1).lower()
    return default


def _pick_confidence(text: str) -> float:
    m = re.search(r'"confidence"\s*:\s*([0-9.]+)', text)
    if m:
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    return 0.5


def _pick_reason(text: str) -> str:
    m = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else ""


def _pick_acts(text: str) -> list[str]:
    m = re.search(r'"acts"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    acts: list[str] = []
    if m:
        tags = re.findall(r'"([^"]+)"', m.group(1))
        valid = {"kissing", "nudity", "sexual_contact", "lingerie", "bikini", "none"}
        acts = [t.lower() for t in tags if t.lower() in valid and t.lower() != "none"]
    if acts:
        return acts
    tl = text.lower()
    for act, keywords in _ACT_KEYWORDS.items():
        if any(k in tl for k in keywords):
            acts.append(act)
    return acts


def regex_parse(text: str) -> dict | None:
    if "severity" not in text:
        return None
    return {
        "severity": _pick_enum("severity", text, "safe"),
        "nudity": _pick_enum("nudity", text, "none"),
        "orientation": _pick_enum("orientation", text, "none"),
        "acts": _pick_acts(text),
        "confidence": _pick_confidence(text),
        "reason": _pick_reason(text) or "Parsed from model output",
    }


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON: {text[:200]}")
    raw = text[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*}", "}", raw)
        fixed = re.sub(r",\s*]", "]", fixed)
        fixed = fixed.replace("'", '"')
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            parsed = regex_parse(raw)
            if parsed:
                return parsed
            raise


def heuristic_parse(text: str) -> dict:
    parsed = regex_parse(text)
    if parsed and parsed.get("reason"):
        return parsed

    t = text.lower()
    safe_hints = ("mountain", "landscape", "everest", "office", "computer", "typewriter",
                  "museum", "keyboard", "building", "forest", "lake", "flower")
    unsafe_hints = ("nude", "naked", "bikini", "kiss", "genital", "sexual", "porn", "breast")
    if any(w in t for w in safe_hints) and not any(w in t for w in unsafe_hints):
        return {"severity": "safe", "nudity": "none", "orientation": "none",
                "acts": [], "confidence": 0.85, "reason": text[:100]}

    severity = "safe"
    if any(w in t for w in ("explicit", "porn", "genital", "sex act")):
        severity = "explicit"
    elif any(w in t for w in ("suggestive", "bikini", "underwear", "kiss", "nude", "topless", "hugging")):
        severity = "suggestive"

    nudity = "none"
    if any(w in t for w in ("genital", "fully naked", "full nudity")):
        nudity = "full"
    elif any(w in t for w in ("partial", "topless", "bikini", "nude", "naked")):
        nudity = "partial"

    orientation = "none"
    if severity != "safe":
        if any(w in t for w in ("lesbian", "two women", "women kissing")):
            orientation = "lesbian"
        elif any(w in t for w in ("gay", "two men", "men kissing")):
            orientation = "gay"
        elif ("man" in t and "woman" in t) or "man kissing" in t:
            orientation = "heterosexual"

    return {
        "severity": severity, "nudity": nudity, "orientation": orientation,
        "acts": _pick_acts(text), "confidence": 0.7 if severity != "safe" else 0.5,
        "reason": text[:100],
    }


def _normalize_reason(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "; ".join(_normalize_reason(v) for v in value if v)
    if isinstance(value, dict):
        for key in ("reason", "text", "description", "title"):
            if key in value:
                return _normalize_reason(value[key])
    return str(value)[:120] if value else ""


def _normalize_data(data: dict) -> dict:
    data["reason"] = _normalize_reason(data.get("reason", ""))
    sev = str(data.get("severity", "safe")).lower()
    data["severity"] = sev if sev in _ENUM_FIELDS["severity"] else "safe"
    nud = str(data.get("nudity", "none")).lower()
    data["nudity"] = nud if nud in _ENUM_FIELDS["nudity"] else "none"
    orient = str(data.get("orientation", "none")).lower()
    data["orientation"] = orient if orient in _ENUM_FIELDS["orientation"] else "none"
    if data["severity"] == "safe":
        data["orientation"] = "none"
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        data["confidence"] = 0.5
    acts = data.get("acts", [])
    flat: list[str] = []
    if isinstance(acts, list):
        for item in acts:
            if isinstance(item, str):
                flat.append(item.lower())
    valid = {"kissing", "nudity", "sexual_contact", "lingerie", "bikini"}
    data["acts"] = [a for a in flat if a in valid]
    return data


def parse_classification(text: str) -> dict:
    try:
        return _normalize_data(extract_json(text))
    except (ValueError, json.JSONDecodeError):
        return _normalize_data(heuristic_parse(text))
