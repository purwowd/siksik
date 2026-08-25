"""Taxonomy and fusion for multimodal Indonesian content findings.

The existing analyzers intentionally remain independent.  This module gives
them a small shared contract so OCR, visual zero-shot classification, and Qwen
can agree on a stable category/label and be persisted as one finding per file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.models.schemas import Layer

CONTENT_POLICY_REVISION = "id-content-v1"
CONTENT_FUSION_REVISION = "category-evidence-v1"

LGBT_CONTENT = "lgbt_content"
POLITICAL_MEME = "political_meme"
POLITICAL_CAMPAIGN = "political_campaign"
DEMONSTRATION = "demonstration"
INCITEMENT = "incitement"
EXTREMISM = "extremism"
HATE_SPEECH = "hate_speech"
POLITICAL_INSULT = "political_insult"

CONTENT_CATEGORY_LABELS: dict[str, str] = {
    LGBT_CONTENT: "LGBT text/flag",
    POLITICAL_MEME: "Meme politik",
    POLITICAL_CAMPAIGN: "Kampanye politik",
    DEMONSTRATION: "Demonstrasi",
    INCITEMENT: "Incitement / ajakan provokatif",
    EXTREMISM: "Extremism",
    HATE_SPEECH: "Ujaran kebencian",
    POLITICAL_INSULT: "Penghinaan negara/politikus",
}
CONTENT_CATEGORIES = frozenset(CONTENT_CATEGORY_LABELS)

_CATEGORY_ALIASES = {
    "lgbt": LGBT_CONTENT,
    "lgbt_text": LGBT_CONTENT,
    "lgbt_flag": LGBT_CONTENT,
    "lgbt_text_or_flag": LGBT_CONTENT,
    "pride_flag": LGBT_CONTENT,
    "transgender_flag": LGBT_CONTENT,
    "meme_politik": POLITICAL_MEME,
    "political_meme": POLITICAL_MEME,
    "campaign": POLITICAL_CAMPAIGN,
    "kampanye": POLITICAL_CAMPAIGN,
    "political_campaign": POLITICAL_CAMPAIGN,
    "demo": DEMONSTRATION,
    "demonstrasi": DEMONSTRATION,
    "demonstration_protest": DEMONSTRATION,
    "provokasi": INCITEMENT,
    "hasutan": INCITEMENT,
    "incitement_nonviolent": INCITEMENT,
    "incitement_violent": INCITEMENT,
    "radikalisme": EXTREMISM,
    "extremist_symbol": EXTREMISM,
    "extremism_text": EXTREMISM,
    "ujaran_kebencian": HATE_SPEECH,
    "hate": HATE_SPEECH,
    "state_insult": POLITICAL_INSULT,
    "politician_insult": POLITICAL_INSULT,
    "penghinaan_negara": POLITICAL_INSULT,
    "penghinaan_politikus": POLITICAL_INSULT,
}

_LGBT_RE = re.compile(
    r"(?<![\w.])(?:lgbtqia\+?|lgbtq\+?|lgbt\+?|gay|lesbi(?:an)?|biseksual|"
    r"transgender|transpuan|transpria|queer|pride\s+(?:flag|parade|month)|"
    r"bendera\s+(?:pelangi|pride|transgender))(?![\w.])",
    re.IGNORECASE,
)
_CAMPAIGN_RE = re.compile(
    r"\b(?:kampanye|masa\s+kampanye|coblos|pilih\s+(?:nomor|paslon|caleg|capres)|"
    r"dukung\s+(?:calon|paslon|caleg|capres)|caleg|capres|cawapres|paslon|"
    r"pilkada|pemilu|relawan\s+(?:calon|paslon|capres))\b",
    re.IGNORECASE,
)
_DEMONSTRATION_RE = re.compile(
    r"\b(?:demonstrasi|unjuk\s+rasa|aksi\s+massa|aksi\s+unjuk\s+rasa|"
    r"turun\s+ke\s+jalan|long\s+march|massa\s+aksi|demo\s+(?:mahasiswa|buruh|massa))\b",
    re.IGNORECASE,
)
_INCITEMENT_RE = re.compile(
    r"\b(?:ayo|mari|serukan|ajak(?:lah)?|hasut|provokasi)\b.{0,64}\b"
    r"(?:serbu|bakar|bunuh|hancurkan|gulingkan|kudeta|lawan|usir|basmi|"
    r"kepung|boikot|turun\s+ke\s+jalan)\b|"
    r"\b(?:serbu|bakar|bunuh|hancurkan|gulingkan|kudeta|basmi|kepung)\b.{0,64}"
    r"\b(?:pemerintah|negara|istana|gedung|mereka|kelompok|kaum|orang)\b|"
    r"\b(?:revolusi\s+berdarah|makar|hasutan\s+kekerasan)\b",
    re.IGNORECASE | re.DOTALL,
)
_EXTREMISM_RE = re.compile(
    r"\b(?:isis|isil|daesh|al[ -]?qaeda|jamaah\s+ansharut\s+daulah|"
    r"jemaah\s+islamiyah|propaganda\s+ekstremis|kelompok\s+ekstremis|"
    r"aksi\s+teror|baiat\s+(?:isis|daesh)|simbol\s+ekstremis)\b",
    re.IGNORECASE,
)
_POLITICAL_TARGET_RE = re.compile(
    r"\b(?:indonesia|nkri|negara|pemerintah|presiden|wakil\s+presiden|menteri|"
    r"gubernur|dpr|mpr|istana|jokowi|joko\s+widodo|prabowo|"
    r"prabowo\s+subianto|politikus|pejabat|partai)\b",
    re.IGNORECASE,
)
_INSULT_RE = re.compile(
    r"\b(?:anjing|bajingan|bangsat|sialan|tolol|goblok|bodoh|munafik|"
    r"pengkhianat|penghianat|penjahat|koruptor|boneka|firaun|diktator|"
    r"antek\s+(?:asing|aseng)|jual\s+negara|khianat\s+negara|cebong|kampret|kadrun)\b",
    re.IGNORECASE,
)
_GROUP_TARGET_RE = re.compile(
    r"\b(?:agama|ras|suku|etnis|kaum|kelompok|minoritas|mayoritas|"
    r"muslim|islam|kristen|katolik|hindu|buddha|yahudi|cina|tionghoa|"
    r"pribumi|pendatang|gay|lesbi(?:an)?|lgbtq?\+?|transgender|queer)\b",
    re.IGNORECASE,
)
_HATE_ACTION_RE = re.compile(
    r"\b(?:basmi|usir|musnahkan|habisi|bunuh|bakar|sampah|penyakit|"
    r"tidak\s+layak\s+hidup|harus\s+mati|haramkan|benci)\b",
    re.IGNORECASE,
)
_MEME_RE = re.compile(r"\b(?:meme|satir|parodi|template\s+meme)\b", re.IGNORECASE)
_POLITICAL_MEME_CUE_RE = re.compile(
    r"\b(?:ganti\s+presiden|lengserkan|turunkan|tenggelamkan|diktator|firaun|"
    r"boneka\s+asing|jual\s+negara|antek\s+(?:asing|aseng)|cebong|kampret|kadrun)\b",
    re.IGNORECASE,
)


def normalize_content_category(value: str | None) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    if normalized in CONTENT_CATEGORIES:
        return normalized
    return _CATEGORY_ALIASES.get(normalized)


def _matched_excerpt(text: str, match: re.Match[str] | None) -> str:
    compact = " ".join((text or "").replace("\x00", " ").split())
    if not compact:
        return ""
    if match is None:
        return compact[:280]
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 160)
    return " ".join(text[start:end].replace("\x00", " ").split())[:280]


def findings_from_text(
    text: str,
    *,
    backend: str,
    layer: str = Layer.L3.value,
    image_context: bool = False,
    include_model: bool = True,
) -> list[dict[str, Any]]:
    """Classify explicit Indonesian text signals into the shared taxonomy.

    This deterministic layer complements the learned models and preserves the
    exact OCR span as auditable evidence.  It never infers LGBT identity from a
    person's appearance; only explicit text is considered here.
    """
    if not text or not text.strip():
        return []

    matches: dict[str, re.Match[str]] = {}
    for category, pattern in (
        (LGBT_CONTENT, _LGBT_RE),
        (POLITICAL_CAMPAIGN, _CAMPAIGN_RE),
        (DEMONSTRATION, _DEMONSTRATION_RE),
        (INCITEMENT, _INCITEMENT_RE),
        (EXTREMISM, _EXTREMISM_RE),
    ):
        hit = pattern.search(text)
        if hit:
            matches[category] = hit

    target = _POLITICAL_TARGET_RE.search(text)
    insult = _INSULT_RE.search(text)
    if target and insult:
        matches[POLITICAL_INSULT] = insult
    meme_cue = _MEME_RE.search(text) or _POLITICAL_MEME_CUE_RE.search(text)
    if meme_cue and (image_context or target):
        matches[POLITICAL_MEME] = meme_cue

    group = _GROUP_TARGET_RE.search(text)
    hate = _HATE_ACTION_RE.search(text)
    if group and (hate or insult):
        matches[HATE_SPEECH] = hate or insult  # type: ignore[assignment]

    output: list[dict[str, Any]] = []
    for category in CONTENT_CATEGORY_LABELS:
        match = matches.get(category)
        if match is None:
            continue
        output.append(
            {
                "category": category,
                "label": CONTENT_CATEGORY_LABELS[category],
                "confidence": 0.9 if category in {INCITEMENT, POLITICAL_INSULT} else 0.86,
                "layer_origin": layer,
                "evidence": f"[{backend}] {_matched_excerpt(text, match)}"[:320],
            }
        )
    # A base IndoBERTweet checkpoint is not a classifier by itself.  This
    # adapter activates only when a fine-tuned checkpoint with matching
    # ``id2label`` metadata is explicitly configured.
    try:
        from app.core.config import settings

        if include_model and settings.content_text_model:
            from app.services import content_text

            output.extend(content_text.analyze_text(text, layer=layer))
    except Exception:
        # Optional model failures are reported by its adapter and must never
        # abort the legacy analysis flow.
        pass
    return output


def should_adjudicate_text(text: str) -> bool:
    """Cheap candidate gate before invoking the heavier Qwen text pass."""
    if not text or not text.strip():
        return False
    return any(
        pattern.search(text)
        for pattern in (
            _LGBT_RE,
            _CAMPAIGN_RE,
            _DEMONSTRATION_RE,
            _INCITEMENT_RE,
            _EXTREMISM_RE,
            _POLITICAL_TARGET_RE,
            _INSULT_RE,
            _GROUP_TARGET_RE,
            _HATE_ACTION_RE,
            _MEME_RE,
            _POLITICAL_MEME_CUE_RE,
        )
    )


_LEGACY_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        INCITEMENT,
        re.compile(
            r"\b(?:gulingkan|kudeta|revolusi\s+berdarah|hasut|provokasi|makar)\b",
            re.IGNORECASE,
        ),
    ),
    (
        EXTREMISM,
        re.compile(r"\b(?:radikal|separatis|ekstremis|extremis)\b", re.IGNORECASE),
    ),
    (POLITICAL_CAMPAIGN, re.compile(r"\b(?:ganti\s+presiden|kampanye|coblos)\b", re.IGNORECASE)),
    (DEMONSTRATION, re.compile(r"\b(?:demonstrasi|unjuk\s+rasa|demo\s+massa)\b", re.IGNORECASE)),
    (
        POLITICAL_INSULT,
        re.compile(
            r"\b(?:diktator|firaun|boneka\s+asing|pengh?ianat|khianat\s+negara|"
            r"jual\s+negara|antek\s+(?:asing|aseng)|cebong|kampret|kadrun)\b",
            re.IGNORECASE,
        ),
    ),
)


def category_for_finding(finding: dict[str, Any]) -> str | None:
    direct = normalize_content_category(str(finding.get("category") or ""))
    if direct:
        return direct
    blob = f"{finding.get('label', '')} {finding.get('evidence', '')}"
    for category, pattern in _LEGACY_HINTS:
        if pattern.search(blob):
            return category
    return None


def _detector_name(finding: dict[str, Any]) -> str:
    evidence = str(finding.get("evidence") or "")
    bracket = re.match(r"\[([^\]]+)]", evidence)
    if bracket:
        return bracket.group(1)[:40]
    label = str(finding.get("label") or "").casefold()
    for needle, name in (
        ("qwen", "qwen"),
        ("vl reasoning", "qwen"),
        ("ocr", "ocr"),
        ("clip", "visual"),
        ("siglip", "visual"),
        ("meme/poster", "ocr+visual"),
    ):
        if needle in label:
            return name
    return "analyzer"


def _combined_evidence(items: Iterable[dict[str, Any]]) -> str:
    snippets: list[str] = []
    seen: set[str] = set()
    detectors: list[str] = []
    for finding in items:
        detector = _detector_name(finding)
        if detector not in detectors:
            detectors.append(detector)
        evidence = " ".join(str(finding.get("evidence") or "").split())
        key = evidence.casefold()
        if evidence and key not in seen:
            seen.add(key)
            snippets.append(evidence)
    prefix = f"[fusion:{'+'.join(detectors)}] " if len(detectors) > 1 else ""
    return (prefix + " | ".join(snippets))[:320]


def merge_content_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one content finding per canonical category.

    Non-content findings keep their existing behavior.  Content evidence from
    multiple detectors is combined, while confidence uses the strongest source
    instead of being artificially inflated by duplicate detections.
    """
    values = [dict(item) for item in findings]
    buckets: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    passthrough: list[tuple[int, dict[str, Any]]] = []
    for index, finding in enumerate(values):
        category = category_for_finding(finding)
        if category is None:
            passthrough.append((index, finding))
            continue
        buckets.setdefault(category, []).append((index, finding))

    merged: list[tuple[int, dict[str, Any]]] = list(passthrough)
    for category, entries in buckets.items():
        first_index = min(index for index, _ in entries)
        strongest = max(entries, key=lambda value: float(value[1].get("confidence", 0.0)))[1]
        layers = [str(item.get("layer_origin") or Layer.L3.value) for _, item in entries]
        layer = max(layers, key=lambda value: int(value[1:]) if value.startswith("L") and value[1:].isdigit() else 0)
        combined = {
            **strongest,
            "category": category,
            "label": CONTENT_CATEGORY_LABELS[category],
            "confidence": round(
                min(0.99, max(float(item.get("confidence", 0.0)) for _, item in entries)),
                3,
            ),
            "layer_origin": layer,
            "evidence": _combined_evidence(item for _, item in entries),
        }
        merged.append((first_index, combined))

    merged.sort(key=lambda value: value[0])
    # Content buckets are already unique by canonical category.  Do not apply
    # another label-only de-duplication here: legacy analyzers may legitimately
    # emit the same label with different evidence, and their behavior must stay
    # unchanged.  Existing callers retain their original label+evidence de-dupe.
    return [finding for _, finding in merged]


def gallery_badge(category: str) -> str | None:
    normalized = normalize_content_category(category)
    return CONTENT_CATEGORY_LABELS.get(normalized) if normalized else None
