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

CONTENT_POLICY_REVISION = "general-category-contracts-v3"
CONTENT_FUSION_REVISION = "public-figure-context-only-v2"

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

# Shared category contract used by deterministic OCR rules and the optional
# Qwen adjudicator. Keeping confirm/reject semantics in one place prevents the
# text and VL paths from silently using different definitions.
CONTENT_CATEGORY_POLICIES: dict[str, dict[str, str]] = {
    LGBT_CONTENT: {
        "confirm": "tulisan LGBT/LGBTQ/pride atau bendera/simbol pride/trans terlihat jelas; gunakan objek eksplisit, bukan dugaan identitas",
        "reject": "pelangi alam, objek warna-warni, kedekatan orang, wajah, pakaian, atau perkiraan orientasi tanpa simbol/tulisan eksplisit",
    },
    POLITICAL_MEME: {
        "confirm": "meme, parodi, karikatur, manipulasi, permainan kata, atau kontras ironis yang menyasar pemerintah, politikus, partai, atau kebijakan publik",
        "reject": "berita faktual, jajak pendapat, kampanye, tangkapan media sosial biasa, kutipan netral, infografik resmi, atau lelucon tanpa target politik/kebijakan",
    },
    POLITICAL_CAMPAIGN: {
        "confirm": "ajakan memilih/dukung, nomor urut, logo partai, paslon/caleg, surat suara, baliho, relawan, atau rapat kampanye",
        "reject": "berita pemilu, foto pejabat, acara pemerintahan, atau kerumunan tanpa ajakan/atribut kampanye",
    },
    DEMONSTRATION: {
        "confirm": "aksi protes, mogok, sit-in, blokade, atau long march dengan poster tuntutan, orasi, megafon, maupun barikade",
        "reject": "konser, olahraga, upacara, antrean, prosesi, kampanye kandidat, atau kerumunan biasa",
    },
    INCITEMENT: {
        "confirm": "ajakan atau perintah eksplisit untuk menyerang, membakar, membunuh, mengusir, mengepung, atau menggulingkan",
        "reject": "berita, kutipan historis, penolakan, negasi, peringatan, pelaporan kasus, atau satire tanpa ajakan nyata",
    },
    EXTREMISM: {
        "confirm": "dukungan, glorifikasi, baiat, rekrutmen, propaganda, atau simbol organisasi ekstremis yang dapat diidentifikasi",
        "reject": "berita, sejarah, riset, kecaman, kontra-propaganda, serta simbol agama atau nasional biasa",
    },
    HATE_SPEECH: {
        "confirm": "kelompok yang jelas menjadi target dehumanisasi, diskriminasi, pengusiran, ancaman, atau kekerasan",
        "reject": "kritik ide/kebijakan, penghinaan individual, kutipan berita, counter-speech, negasi, atau kelompok yang tidak jelas",
    },
    POLITICAL_INSULT: {
        "confirm": "politikus, pejabat, pemerintah, atau negara menjadi target langsung makian/degradasi personal",
        "reject": "kritik kebijakan, tuduhan faktual, berita, kutipan, satire netral, atau kata hinaan yang tidak dekat dengan target",
    },
}

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
    r"\b(?:masa\s+kampanye|rapat\s+umum|nomor\s+urut|surat\s+suara|coblos|"
    r"pilih\s+(?:nomor|paslon|caleg|capres)|"
    r"dukung\s+(?:calon|paslon|caleg|capres)|caleg|capres|cawapres|paslon|"
    r"relawan\s+(?:calon|paslon|capres)|kampanye\s+(?:pemilu|pilkada|caleg|capres|paslon))\b",
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
_EXTREMISM_ENTITY_RE = re.compile(
    r"\b(?:isis|isil|daesh|al[ -]?qaeda|jamaah\s+ansharut\s+daulah|"
    r"jemaah\s+islamiyah|kelompok\s+ekstremis)\b",
    re.IGNORECASE,
)
_EXTREMISM_SUPPORT_RE = re.compile(
    r"\b(?:baiat|bergabung|dukung|sebarkan|rekrut(?:men)?|propaganda|"
    r"simpatisan|hidup|setia|berjuang\s+bersama|simbol\s+ekstremis)\b",
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
    r"\b(?:penganut\s+agama|ras|suku|etnis|"
    r"muslim|islam|kristen|katolik|hindu|buddha|yahudi|cina|tionghoa|"
    r"pribumi|pendatang|difabel|disabilitas|gay|lesbi(?:an)?|lgbtq?\+?|"
    r"transgender|queer)\b",
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
_PUBLIC_POLICY_TOPIC_RE = re.compile(
    r"\b(?:pemerintah|presiden|menteri|pejabat|dpr|partai|pemilu|kebijakan|"
    r"anggaran|pajak|subsidi|bansos|korupsi|rupiah|bbm|listrik|jalan|"
    r"infrastruktur|sekolah|kesehatan|hutan|asap|sawit|tambang|lingkungan|"
    r"investor|aparat|polisi|negara)\b",
    re.IGNORECASE,
)
_SATIRE_STRUCTURE_RE = re.compile(
    r"\b(?:"
    r"habis\s+.{1,48}?\s+terbitlah\s+|"
    r"katanya\s+.{1,64}?\s+(?:ternyata|nyatanya)\s+|"
    r"janji(?:nya)?\s+.{1,64}?\s+(?:realita|kenyataan)\s+|"
    r"(?:dulu|sebelum)\s+.{1,48}?\s+(?:sekarang|sesudah)\s+|"
    r"kalau\s+.{1,72}?\s+kenapa\s+|"
    r"ironi|sindiran|satir(?:e)?|parodi|meme"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_NEGATION_RE = re.compile(
    r"\b(?:tidak|bukan|jangan(?!\s+diam)|dilarang|menolak|mencegah|mengecam|mengutuk|"
    r"membantah|tanpa|stop|hentikan)\b",
    re.IGNORECASE,
)
_REPORTING_RE = re.compile(
    r"\b(?:berita|laporan|dilaporkan|diberitakan|menurut|mengutip|kutipan|"
    r"sejarah|penelitian|edukasi|dokumenter|tersangka|terdakwa|pelaku|"
    r"ditangkap|didakwa|divonis|persidangan|kasus)\b",
    re.IGNORECASE,
)
_DIRECT_CALL_RE = re.compile(
    r"\b(?:ayo|mari|kita|wajib|harus|segera|saatnya|serukan|ajak(?:lah)?|"
    r"lakukan|jangan\s+diam)\b",
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


def _nearby_pair(
    text: str,
    first: re.Pattern[str],
    second: re.Pattern[str],
    *,
    max_distance: int,
) -> tuple[re.Match[str], re.Match[str]] | None:
    """Return the closest ordered-or-unordered pair within a bounded span."""
    left = list(first.finditer(text))
    right = list(second.finditer(text))
    best: tuple[int, re.Match[str], re.Match[str]] | None = None
    for first_match in left:
        for second_match in right:
            distance = max(
                0,
                max(first_match.start(), second_match.start())
                - min(first_match.end(), second_match.end()),
            )
            if distance > max_distance:
                continue
            if best is None or distance < best[0]:
                best = (distance, first_match, second_match)
    return None if best is None else (best[1], best[2])


def _excluded_context(text: str, match: re.Match[str], category: str) -> bool:
    """Reject nearby negation, counter-speech, and clearly neutral reporting."""
    if category == LGBT_CONTENT:
        return False
    before = text[max(0, match.start() - 80) : match.start()]
    if _NEGATION_RE.search(before):
        return True
    window_start = max(0, match.start() - 140)
    window_end = min(len(text), match.end() + 160)
    window = text[window_start:window_end]
    report = _REPORTING_RE.search(window)
    if report is None:
        return False
    direct = _DIRECT_CALL_RE.search(window)
    # "Berita: pelaku mengajak ayo serbu" is reporting; "ayo serbu" followed
    # later by an unrelated news link remains a direct call.
    return direct is None or report.start() <= direct.start()


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
    ):
        hit = pattern.search(text)
        if hit and not _excluded_context(text, hit, category):
            matches[category] = hit

    extremist_pair = _nearby_pair(
        text,
        _EXTREMISM_ENTITY_RE,
        _EXTREMISM_SUPPORT_RE,
        max_distance=100,
    )
    if extremist_pair:
        entity, support = extremist_pair
        if not _excluded_context(text, support, EXTREMISM):
            matches[EXTREMISM] = entity

    political_insult = _nearby_pair(
        text,
        _POLITICAL_TARGET_RE,
        _INSULT_RE,
        max_distance=100,
    )
    target = political_insult[0] if political_insult else _POLITICAL_TARGET_RE.search(text)
    insult = political_insult[1] if political_insult else None
    if insult and not _excluded_context(text, insult, POLITICAL_INSULT):
        matches[POLITICAL_INSULT] = insult
    meme_cue = _MEME_RE.search(text) or _POLITICAL_MEME_CUE_RE.search(text)
    if meme_cue is None and image_context and _PUBLIC_POLICY_TOPIC_RE.search(text):
        meme_cue = _SATIRE_STRUCTURE_RE.search(text)
    if (
        meme_cue
        and (image_context or target)
        and not _excluded_context(text, meme_cue, POLITICAL_MEME)
    ):
        matches[POLITICAL_MEME] = meme_cue

    group_hate = _nearby_pair(
        text,
        _GROUP_TARGET_RE,
        _HATE_ACTION_RE,
        max_distance=100,
    ) or _nearby_pair(
        text,
        _GROUP_TARGET_RE,
        _INSULT_RE,
        max_distance=80,
    )
    if group_hate:
        _group, hate = group_hate
        if not _excluded_context(text, hate, HATE_SPEECH):
            matches[HATE_SPEECH] = hate

    output: list[dict[str, Any]] = []
    for category in CONTENT_CATEGORY_LABELS:
        match = matches.get(category)
        if match is None:
            continue
        output.append(
            {
                "category": category,
                "label": CONTENT_CATEGORY_LABELS[category],
                "confidence": {
                    INCITEMENT: 0.92,
                    HATE_SPEECH: 0.90,
                    EXTREMISM: 0.89,
                    POLITICAL_CAMPAIGN: 0.88,
                    DEMONSTRATION: 0.87,
                    LGBT_CONTENT: 0.86,
                    POLITICAL_INSULT: 0.84,
                    POLITICAL_MEME: 0.82,
                }[category],
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
            _EXTREMISM_ENTITY_RE,
            _EXTREMISM_SUPPORT_RE,
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
    if evidence.startswith("Berkas:"):
        return "sd"
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
        preserved = next(
            (item.get("label") for _, item in entries if item.get("keep_label") and item.get("label")),
            None,
        )
        combined = {
            **strongest,
            "category": category,
            "label": preserved or CONTENT_CATEGORY_LABELS[category],
            "confidence": round(
                min(0.99, max(float(item.get("confidence", 0.0)) for _, item in entries)),
                3,
            ),
            "layer_origin": layer,
            "evidence": _combined_evidence(item for _, item in entries),
        }
        combined.pop("keep_label", None)
        merged.append((first_index, combined))

    merged.sort(key=lambda value: value[0])
    # Content buckets are already unique by canonical category.  Do not apply
    # another label-only de-duplication here: legacy analyzers may legitimately
    # emit the same label with different evidence, and their behavior must stay
    # unchanged.  Existing callers retain their original label+evidence de-dupe.
    out = [finding for _, finding in merged]
    for finding in out:
        finding.pop("keep_label", None)
    return out


def confirm_visual_candidates(
    candidates: Iterable[dict[str, Any]],
    supporting_findings: Iterable[dict[str, Any]],
    *,
    reasoning_verdict: str = "unavailable",
) -> list[dict[str, Any]]:
    """Promote zero-shot visual candidates only with independent evidence.

    CLIP pair scores are useful for recall but are not calibrated probabilities.
    OCR/rule findings or a flagged Qwen decision must independently name the
    same canonical category. Observable-object fast paths exist for locally
    verified rainbow flags and very strong campaign/demonstration scenes. A
    Qwen safe verdict cannot negate those explicit signals, but still rejects
    ambiguous meme/extremism candidates.
    """
    values = [dict(item) for item in candidates]
    if not values:
        return []
    from app.core.config import settings

    if not settings.content_visual_require_confirmation:
        return values
    explicit = [
        item for item in values
        if str(item.get("visual_confirmation") or "").startswith("explicit_")
    ]
    ambiguous = [
        item for item in values
        if not str(item.get("visual_confirmation") or "").startswith("explicit_")
    ]
    if reasoning_verdict == "safe":
        return explicit
    supported = {
        category
        for item in supporting_findings
        if (category := category_for_finding(item)) is not None
    }
    return explicit + [
        item for item in ambiguous
        if category_for_finding(item) in supported
    ]


def visual_candidates_requiring_reasoning(
    candidates: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only candidates that cannot use an explicit-object fast path."""
    return [
        dict(item)
        for item in candidates
        if not str(item.get("visual_confirmation") or "").startswith("explicit_")
    ]


def apply_text_adjudication(
    findings: Iterable[dict[str, Any]],
    *,
    reasoning_verdict: str,
) -> list[dict[str, Any]]:
    """Let an explicit contextual safe verdict remove taxonomy candidates.

    Legacy exact risk-keyword findings are intentionally retained because the
    Qwen taxonomy does not cover every legacy category (for example narcotics).
    """
    values = [dict(item) for item in findings]
    if reasoning_verdict != "safe":
        return values
    return [item for item in values if category_for_finding(item) is None]


def gallery_badge(category: str) -> str | None:
    normalized = normalize_content_category(category)
    return CONTENT_CATEGORY_LABELS.get(normalized) if normalized else None
