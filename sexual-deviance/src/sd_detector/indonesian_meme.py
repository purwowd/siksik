from __future__ import annotations

import re
from typing import Iterable, Optional

import cv2
import numpy as np

from .meme_rules import load_meme_rules, match_figure_aliases, match_phrase_figures, match_visual_figures, resolve_public_figures
from .schema import IndonesianMemeContext

# Pejabat / figur publik yang sering muncul di meme Indonesia
PUBLIC_FIGURE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("jokowi", ("jokowi", "joko widodo", "presiden jokowi", "pak jokowi", "widodo", "presiden joko")),
    ("prabowo", ("prabowo", "prabowo subianto", "presiden prabowo", "subianto", "presiden prabowo subianto")),
    ("gibran", ("gibran", "gibran rakabuming", "wakil presiden gibran")),
    ("megawati", ("megawati", "mega ", "pdip")),
    ("anies", ("anies baswedan", "anies")),
    ("ganjar", ("ganjar pranowo", "ganjar")),
    ("sandiaga", ("sandiaga uno", "sandiaga", "sandi uno")),
    ("mahfud", ("mahfud md", "mahfud")),
    ("luhut", ("luhut binsar", "luhut")),
    ("erick_thohir", ("erick thohir", "erick tohir")),
    ("sri_mulyani", ("sri mulyani", "bu sri")),
    ("purbaya", ("purbaya", "menteri keuangan purbaya")),
    ("bahlil", ("bahlil lahadalia", "bahlil", "menteri esdm bahlil")),
    ("rocky_gerung", ("rocky gerung",)),
    ("ridwan_kamil", ("ridwan kamil", "rk ", "emil")),
    ("basuki", ("ahok", "basuki tjahaja", "basuki")),
]

MEME_FORMAT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("caption_meme", (
        "meme", "internet meme", "caption meme", "caption at the top", "caption at the bottom",
        "text at the top", "text at the bottom", "text overlay", "overlay text",
        "impact font", "white text with black outline", "top text bottom text",
    )),
    ("split_panel", ("split image", "two panel", "before and after meme", "drake meme format")),
    ("edited_photo", ("edited photo", "photoshopped", "doctored image", "manipulated image", "fake photo")),
    ("ai_generated", (
        "ai generated", "ai-generated", "ai image", "deepfake", "deep fake",
        "generated image", "synthetic image", "artificially generated",
        "unnatural composite", "digitally altered",
    )),
    ("caricature", ("caricature", "cartoon of", "political cartoon", "satirical drawing", "editorial cartoon")),
    ("screenshot_meme", ("screenshot meme", "twitter screenshot", "whatsapp forward", "chat screenshot")),
]

SATIRE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("political_satire", (
        "political satire", "political meme", "political joke", "satirical",
        "sindiran", "nyindir", "roasting", "mocking", "parody",
    )),
    ("sarcasm", ("sarcastic", "sarcasm", "ironic", "ironis", "sarkas")),
    ("criticism", ("criticiz", "kritik", "protes", "mockery", "menghina", "celaan")),
    ("humor", ("funny meme", "humor", "humor politik", "lucu", "ngakak", "candaan", "shitpost")),
    ("deepfake", ("deepfake", "deep fake", "fake kiss", "fake photo of")),
]

TOPIC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("palm_oil", ("palm oil", "oil palm", "sawit", "kelapa sawit", "hidup sawit")),
    ("corruption", ("korupsi", "corruption", "kpk", "suap", "gratifikasi")),
    ("election", ("pemilu", "pilpres", "pilgub", "pilkada", "election", "calon presiden")),
    ("economy", ("ekonomi", "inflasi", "harga naik", "subsidi", "pajak", "anggaran")),
    ("government", ("pemerintah", "menteri", "dpr", "parlemen", "kabinet", "istana")),
    ("social_issue", ("demo", "demonstrasi", "unjuk rasa", "protest")),
]

INDONESIAN_WORDS = (
    "yang", "dan", "di", "ini", "itu", "gak", "nggak", "udah", "banget", "kayak",
    "presiden", "menteri", "rakyat", "negara", "tolol", "bego", "wkwk", "wkwwk",
    "sindiran", "candaan", "lucu", "masa", "kok", "sih", "dong", "pak", "bu",
    "jangan", "peduli", "gwe", "gue", "bro", "ngocok", "hidup", "sawit", "anjing",
)

POSITIVE_MEME_CUES = (
    "meme", "caption", "text overlay", "satire", "sindiran", "deepfake",
    "ai generated", "ai-generated", "photoshopped", "edited photo", "parody",
    "speech bubble", "impact font", "political joke", "caption_meme",
)

NEGATIVE_MEME_CUES = (
    "no meme", "not a meme", "regular photo", "news photo", "official portrait",
    "press conference photo", "bukan meme", "no indonesian meme cues",
)


def _match_patterns(text: str, patterns: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    t = text.lower()
    found: list[str] = []
    for label, keys in patterns:
        if any(k in t for k in keys):
            found.append(label)
    return found


def _detect_language(text: str) -> str:
    t = text.lower()
    id_hits = sum(1 for w in INDONESIAN_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    en_hints = ("the", "president", "minister", "meme", "caption", "political")
    en_hits = sum(1 for w in en_hints if w in t)
    if id_hits >= 2 and en_hits >= 1:
        return "mixed"
    if id_hits >= 1:
        return "id"
    if en_hits >= 2:
        return "en"
    return "unknown"


def _get_figure_patterns() -> list[tuple[str, tuple[str, ...]]]:
    rules = load_meme_rules()
    patterns = rules.figure_patterns()
    return patterns if patterns else PUBLIC_FIGURE_PATTERNS


def _get_format_patterns() -> list[tuple[str, tuple[str, ...]]]:
    rules = load_meme_rules()
    patterns = rules.format_pattern_list()
    return patterns if patterns else MEME_FORMAT_PATTERNS


def _get_satire_patterns() -> list[tuple[str, tuple[str, ...]]]:
    rules = load_meme_rules()
    patterns = rules.satire_pattern_list()
    return patterns if patterns else SATIRE_PATTERNS


def _get_topic_patterns() -> list[tuple[str, tuple[str, ...]]]:
    rules = load_meme_rules()
    patterns = rules.topic_pattern_list()
    return patterns if patterns else TOPIC_PATTERNS


def parse_meme_json_response(raw: str) -> IndonesianMemeContext:
    from .prompt import extract_json

    try:
        data = extract_json(raw)
    except (ValueError, TypeError):
        return infer_meme_from_text(raw)

    overlay = data.get("overlay_text") or []
    if isinstance(overlay, str):
        overlay = [overlay] if overlay.strip() else []
    figures = data.get("public_figures") or []
    if isinstance(figures, str):
        figures = [figures] if figures.strip() else []
    satire = data.get("satire_type") or []
    if isinstance(satire, str):
        satire = [satire] if satire.strip() else []
    topics = data.get("topics") or []
    if isinstance(topics, str):
        topics = [topics] if topics.strip() else []

    is_meme = bool(data.get("is_meme"))
    present = is_meme or bool(figures) or bool(overlay)
    conf = 0.72 if is_meme and figures else 0.65 if present else 0.0

    return IndonesianMemeContext(
        present=present,
        is_meme=is_meme,
        has_text_overlay=bool(data.get("has_text_overlay")) or bool(overlay),
        text_language=str(data.get("text_language") or "unknown"),
        overlay_text=[str(x) for x in overlay if str(x).strip()],
        public_figures=sorted(set(str(x).lower() for x in figures if str(x).strip())),
        satire_type=sorted(set(str(x) for x in satire if str(x).strip())),
        topics=sorted(set(str(x) for x in topics if str(x).strip())),
        signals=["vlm_json"] if present else [],
        confidence=conf,
    )


def _extract_public_figures(text: str) -> list[str]:
    figures = list(match_visual_figures(text))
    figures.extend(_match_patterns(text, _get_figure_patterns()))
    t = text.lower()

    fig_line = re.search(
        r"figures?[:\s]+(.+?)(?:\n|\.\s|\d+\.\s|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if fig_line:
        line = fig_line.group(1).lower()
        if "none" not in line or len(line) > 8:
            figures.extend(match_visual_figures(fig_line.group(1)))

    if re.search(r"\bjoko\b", t) and "jokowi" not in figures:
        figures.append("jokowi")
    if "widodo" in t and "jokowi" not in figures:
        figures.append("jokowi")
    if "subianto" in t and "prabowo" not in figures:
        figures.append("prabowo")

    return sorted(set(figures))


def _extract_overlay_text(*texts: str) -> list[str]:
    combined = " ".join(texts)
    snippets: list[str] = []

    for m in re.finditer(r'"([^"]{3,160})"|\'([^\']{3,160})\'', combined):
        snippet = (m.group(1) or m.group(2) or "").strip()
        if snippet and not snippet.lower().startswith("overlay"):
            snippets.append(snippet)

    for pattern in (
        r"(?:text|caption|teks|tulisan|transcri(?:be|ption))\s*(?:reads?|says?|berbunyi|bertuliskan)?[:\s]+(.{3,160}?)(?:\.|$|\n)",
        r"(?:overlay|meme)\s+(?:text|caption|teks)[:\s]+(.{3,160}?)(?:\.|$|\n)",
        r"(?:top|bottom)\s+(?:text|caption)[:\s]+(.{3,160}?)(?:\.|$|\n)",
        r"TEXT:\s*(.+?)(?:\n|TYPE:|$)",
        r"visible (?:text|caption|overlay)[:\s]+(.{3,160}?)(?:\.|$|\n)",
        r"speech bubble(?:\s+with|\s+saying|\s+containing)?\s+(?:indonesian\s+text|text)?[:\s]*(.{3,160}?)(?:\.|$|\n)",
        r"with the words?\s+[\"'](.{3,160})[\"']",
        r"caption\s+[\"'](.{3,160})[\"']",
        r"saying\s+[\"'](.{3,160})[\"']",
        r"meme of\s+([A-Za-z0-9*][A-Za-z0-9* ]{2,80}?)(?:\s+with|\s+showing|\s+and|\s*$|\.)",
    ):
        for m in re.finditer(pattern, combined, re.IGNORECASE):
            snippet = m.group(1).strip(" \"'")
            if len(snippet) >= 3 and "none" not in snippet.lower()[:12]:
                snippets.append(snippet)

    for m in re.finditer(
        r"\b([A-Z][A-Z*]{2,}(?:\s+[A-Z][A-Z*]{2,}){0,6})\b",
        combined,
    ):
        snippet = m.group(1).strip()
        if len(snippet) >= 4 and snippet not in ("MEME", "TEXT", "TYPE", "FIGURES"):
            snippets.append(snippet)

    deduped: list[str] = []
    seen: set[str] = set()
    skip_generic = {
        "on the image", "in the image", "in the foreground", "on the image.",
        "none", "unknown", "overlay/caption (top/bottom text)?",
        "indonesian text", "text in indonesian", "some indonesian text",
        "foreign text", "text overlay", "caption text", "overlay text",
        "unknown text", "illegible text", "unreadable text", "visible text",
        "some text", "written text", "text on the image", "text on image",
    }
    from .meme_ocr import is_vlm_description_overlay

    for s in snippets:
        cleaned = re.sub(r"\s+", " ", s).strip()
        key = cleaned.lower()
        if is_vlm_description_overlay(cleaned):
            continue
        if key not in seen and len(cleaned) >= 3 and key not in skip_generic:
            seen.add(key)
            deduped.append(cleaned[:160])
    return deduped[:8]


def _is_plain_news_photo(text: str) -> bool:
    t = text.lower()
    if any(k in t for k in POSITIVE_MEME_CUES):
        return False
    if any(k in t for k in NEGATIVE_MEME_CUES):
        return True
    news_only = (
        any(k in t for k in ("official portrait", "press photo", "news photo", "formal photo"))
        and not any(k in t for k in ("meme", "caption", "text overlay", "sindiran", "overlay", "satire"))
    )
    return news_only


def _infer_contextual_figures(
    text: str,
    topics: list[str],
    overlay: list[str],
    *,
    present: bool,
) -> list[str]:
    """Figur dari alias config + sinyal format (bukan frasa viral hardcoded)."""
    if not present:
        return []
    combined = f"{text} {' '.join(overlay)}"
    figures: list[str] = []
    t = combined.lower()

    if "kiss" in t and any(k in t for k in ("uniform", "military", "each other")):
        if "two men" in t or "2 men" in t or ("men" in t and "military" in t):
            figures.extend(["jokowi", "prabowo"])
    if "costume" in t and any(k in t for k in ("palm", "sawit", "tree", "pineapple", "leaves", "frond", "coconut")):
        figures.append("prabowo")
    if any(k in t for k in ("headdress", "coconut", "palm tree", "sawit")):
        if "prabowo" not in figures:
            figures.append("prabowo")

    return sorted(set(figures))


def _detect_costume_satire(text: str) -> bool:
    t = text.lower()
    outfit = any(k in t for k in ("costume", "headdress", "outfit", "dressed as", "wearing a"))
    prop = any(k in t for k in ("palm", "sawit", "tree", "pineapple", "leaves", "frond", "coconut"))
    return outfit and prop


def _detect_political_kiss_fake(text: str) -> bool:
    t = text.lower()
    return (
        "kiss" in t
        and any(k in t for k in ("uniform", "military", "each other"))
        and ("two men" in t or "2 men" in t or ("men" in t and "military" in t))
    )


def _detect_historical_cartoon(text: str) -> bool:
    t = text.lower()
    if "painting" not in t and "drawing" not in t:
        return False
    return any(k in t for k in (
        "pudding", "period costume", "cutting a pudding", "turkey",
        "nutcracker", "soldiers sitting", "sitting at a table",
    ))


def infer_meme_from_text(*texts: str) -> IndonesianMemeContext:
    combined = " ".join(texts).strip()
    if not combined:
        return IndonesianMemeContext()

    if _is_plain_news_photo(combined):
        return IndonesianMemeContext()

    public_figures = _extract_public_figures(combined)
    meme_formats = _match_patterns(combined, _get_format_patterns())
    satire_types = _match_patterns(combined, _get_satire_patterns())
    topics = _match_patterns(combined, _get_topic_patterns())
    overlay_text = _extract_overlay_text(combined)

    t = combined.lower()
    edited_or_ai = bool(set(meme_formats) & {"edited_photo", "ai_generated", "caricature"})
    multi_figure_satire = (
        len(public_figures) >= 2
        and any(k in t for k in ("kissing", "kiss", "cium", "deepfake", "ai generated", "ai-generated"))
    )

    costume_satire = _detect_costume_satire(combined)
    political_kiss_fake = _detect_political_kiss_fake(combined)
    historical_cartoon = _detect_historical_cartoon(combined)

    has_text_overlay = bool(overlay_text) or any(
        k in t for k in (
            "text overlay", "caption", "overlay text", "teks", "tulisan",
            "top text", "bottom text", "impact font", "meme format",
            "speech bubble", "speech-bubble",
        )
    )
    has_figures = bool(public_figures)
    has_satire = bool(satire_types)
    has_topics = bool(topics)
    is_meme = bool(meme_formats) or (
        has_text_overlay and (has_figures or has_satire or has_topics)
    ) or (
        "meme" in t and (has_figures or has_satire or has_text_overlay)
    ) or (
        has_figures and (edited_or_ai or has_satire)
    ) or multi_figure_satire or (
        edited_or_ai and any(k in t for k in ("presiden", "president", "politician", "minister", "pejabat"))
    ) or costume_satire or political_kiss_fake or historical_cartoon

    if multi_figure_satire and "political_satire" not in satire_types:
        satire_types.append("political_satire")
    if edited_or_ai and public_figures and "political_satire" not in satire_types:
        satire_types.append("political_satire")
    if costume_satire:
        if "political_satire" not in satire_types:
            satire_types.append("political_satire")
        if "ai_generated" not in meme_formats:
            meme_formats.append("ai_generated")
    if political_kiss_fake:
        if "political_satire" not in satire_types:
            satire_types.append("political_satire")
        if "deepfake" not in satire_types:
            satire_types.append("deepfake")
        if "edited_photo" not in meme_formats:
            meme_formats.append("edited_photo")

    if historical_cartoon:
        if "caricature" not in meme_formats:
            meme_formats.append("caricature")
        if "political_satire" not in satire_types:
            satire_types.append("political_satire")

    if any(k in t for k in ("cartoon", "caricature", "satirical drawing", "editorial cartoon")):
        if "caricature" not in meme_formats:
            meme_formats.append("caricature")
        if "political_satire" not in satire_types:
            satire_types.append("political_satire")

    signals: list[str] = []
    for group in (public_figures, meme_formats, satire_types, topics):
        signals.extend(group)
    if has_text_overlay:
        signals.append("text_overlay")
    if is_meme:
        signals.append("meme_format")

    present = is_meme or (
        has_figures and (has_text_overlay or has_satire or edited_or_ai)
    )

    if not present and has_text_overlay and "speech bubble" in t:
        if any(k in t for k in ("indonesian", "presiden", "president", "jokowi", "widodo", "political", "uu")):
            is_meme = True
            present = True
            if "political_satire" not in satire_types:
                satire_types.append("political_satire")

    public_figures = sorted(set(public_figures + _infer_contextual_figures(
        combined, topics, overlay_text, present=present,
    )))

    if overlay_text and match_phrase_figures(" ".join(overlay_text)):
        present = True
        is_meme = True
        if not public_figures:
            public_figures = resolve_public_figures(
                visual=public_figures, overlay=overlay_text, layout=None,
            )

    if present and is_meme and not public_figures and "speech bubble" in t:
        if any(k in t for k in ("man", "presiden", "president", "jokowi", "widodo", "shirt")):
            public_figures.append("jokowi")
    public_figures = sorted(set(public_figures))

    confidence = 0.55
    if is_meme and public_figures and has_text_overlay:
        confidence = 0.88
    elif is_meme and public_figures:
        confidence = 0.78
    elif present:
        confidence = 0.65

    return IndonesianMemeContext(
        present=present,
        is_meme=is_meme,
        has_text_overlay=has_text_overlay,
        text_language=_detect_language(combined) if present else "unknown",
        overlay_text=overlay_text,
        public_figures=sorted(set(public_figures)),
        satire_type=sorted(set(satire_types)),
        topics=sorted(set(topics)),
        signals=sorted(set(signals)),
        confidence=confidence if present else 0.0,
    )


def detect_meme_layout(bgr: np.ndarray) -> list[str]:
    """Heuristic: template meme (teks atas/bawah) dari kontras band horizontal."""
    if bgr is None or bgr.size == 0:
        return []

    h, w = bgr.shape[:2]
    if h < 64 or w < 64:
        return []

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    band_h = max(8, h // 8)
    top = gray[:band_h, :]
    bottom = gray[h - band_h :, :]
    mid = gray[band_h : h - band_h, :]

    def _edge_density(region: np.ndarray) -> float:
        edges = cv2.Canny(region, 80, 160)
        return float(edges.mean()) / 255.0

    top_edges = _edge_density(top)
    bottom_edges = _edge_density(bottom)
    mid_edges = _edge_density(mid)
    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())
    mid_mean = float(mid.mean())

    found: list[str] = []
    if top_edges > 0.08 and mid_edges < top_edges * 0.65:
        found.append("top_text_band")
    if bottom_edges > 0.09 and mid_edges < bottom_edges * 0.62:
        found.append("bottom_text_band")

    # Classic impact meme: black caption bars above/below photo
    dark_top = top_mean < mid_mean * 0.55
    dark_bottom = bottom_mean < mid_mean * 0.55
    if dark_top and dark_bottom:
        found.extend(["top_text_band", "bottom_text_band", "caption_bars"])

    if found:
        if "caption_bars" in found or (
            "top_text_band" in found and "bottom_text_band" in found
        ):
            found.append("meme_layout")
    return found


def _layout_worth_ocr(layout: Optional[list[str]]) -> bool:
    """Layout cukup kuat untuk OCR — hindari false positive band tunggal."""
    if not layout:
        return False
    s = set(layout)
    if "caption_bars" in s or "speech_bubble" in s:
        return True
    return "top_text_band" in s and "bottom_text_band" in s


def _layout_needs_ocr_rerun(initial: list[str], enriched: list[str]) -> bool:
    """OCR parallel invalid hanya jika region crop benar-benar berubah."""
    init, enr = set(initial), set(enriched)
    if enr <= init:
        return False
    added = enr - init
    if added <= {"speech_bubble"} and init & {"caption_bars", "top_text_band", "bottom_text_band"}:
        return False
    return bool(added)


def enrich_layout_from_text(layout: list[str], texts: list[str]) -> list[str]:
    """Tambah hint layout dari deskripsi VLM (speech bubble, dll.)."""
    out = list(layout)
    t = " ".join(texts).lower()
    if "speech bubble" in t and "speech_bubble" not in out:
        out.append("speech_bubble")
    return out


def merge_meme_contexts(contexts: Iterable[IndonesianMemeContext]) -> IndonesianMemeContext:
    items = [c for c in contexts if c.present]
    if not items:
        return IndonesianMemeContext()

    overlay: list[str] = []
    figures: set[str] = set()
    satire: set[str] = set()
    topics: set[str] = set()
    signals: set[str] = set()
    langs: list[str] = []
    confidences: list[float] = []

    for ctx in items:
        overlay.extend(ctx.overlay_text)
        figures.update(ctx.public_figures)
        satire.update(ctx.satire_type)
        topics.update(ctx.topics)
        signals.update(ctx.signals)
        if ctx.text_language not in ("unknown", ""):
            langs.append(ctx.text_language)
        confidences.append(ctx.confidence)

    overlay_dedup: list[str] = []
    seen: set[str] = set()
    for line in overlay:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            overlay_dedup.append(line)

    lang = "unknown"
    if langs:
        lang = max(set(langs), key=langs.count)

    is_meme = any(c.is_meme for c in items)
    has_text = any(c.has_text_overlay for c in items) or bool(overlay_dedup)

    return IndonesianMemeContext(
        present=True,
        is_meme=is_meme,
        has_text_overlay=has_text,
        text_language=lang,
        overlay_text=overlay_dedup[:8],
        public_figures=sorted(figures),
        satire_type=sorted(satire),
        topics=sorted(topics),
        signals=sorted(signals),
        confidence=max(confidences) if confidences else 0.0,
    )


def analyze_indonesian_meme(
    bgr: Optional[np.ndarray],
    texts: list[str],
) -> IndonesianMemeContext:
    text_ctx = infer_meme_from_text(*texts)

    layout: list[str] = []
    if bgr is not None:
        layout = detect_meme_layout(bgr)

    if layout and not text_ctx.present:
        strong_layout = "caption_bars" in layout or (
            "top_text_band" in layout and "bottom_text_band" in layout
        )
        if strong_layout:
            satire = sorted(set(text_ctx.satire_type + ["political_satire"]))
            update: dict = {
                "present": True,
                "is_meme": True,
                "has_text_overlay": True,
                "satire_type": satire,
                "signals": sorted(set(text_ctx.signals + layout)),
                "confidence": max(text_ctx.confidence, 0.65),
            }
            if text_ctx.public_figures:
                update["confidence"] = max(text_ctx.confidence, 0.75)
            text_ctx = text_ctx.model_copy(update=update)
        else:
            text_ctx = text_ctx.model_copy(update={
                "signals": sorted(set(text_ctx.signals + layout)),
            })
    elif layout and text_ctx.present:
        satire = sorted(set(text_ctx.satire_type + ["political_satire"]))
        text_ctx = text_ctx.model_copy(update={
            "satire_type": satire,
            "signals": sorted(set(text_ctx.signals + layout)),
            "confidence": min(0.95, max(text_ctx.confidence, 0.65) + 0.05),
            "has_text_overlay": text_ctx.has_text_overlay or True,
        })

    if text_ctx.present:
        ctx_figures = _infer_contextual_figures(
            " ".join(texts) + " " + " ".join(text_ctx.signals),
            list(text_ctx.topics),
            list(text_ctx.overlay_text),
            present=True,
        )
        if ctx_figures:
            merged_figures = sorted(set(text_ctx.public_figures + ctx_figures))
            conf = text_ctx.confidence
            if merged_figures and text_ctx.has_text_overlay:
                conf = max(conf, 0.82)
            elif merged_figures:
                conf = max(conf, 0.75)
            text_ctx = text_ctx.model_copy(update={
                "public_figures": merged_figures,
                "confidence": conf,
            })

    return text_ctx


def meme_needs_ocr(
    ctx: IndonesianMemeContext,
    bgr: Optional[np.ndarray],
    layout: Optional[list[str]] = None,
    texts: Optional[list[str]] = None,
) -> bool:
    """Jalankan OCR hanya jika layout/teks belum cukup — hemat ~1s di foto biasa."""
    if bgr is None:
        return False

    if layout is None:
        layout = detect_meme_layout(bgr)

    from .meme_ocr import overlay_is_weak

    t = " ".join(texts or []).lower()
    weak_overlay = overlay_is_weak(ctx.overlay_text, layout)

    if weak_overlay:
        if "speech bubble" in t or "speech_bubble" in (layout or []):
            return True
        if layout and ctx.present:
            return True
        if ctx.present and ctx.is_meme:
            return True

    if ctx.present and ctx.is_meme and ctx.public_figures and ctx.confidence >= 0.75:
        return False
    if ctx.present and ctx.public_figures and ctx.overlay_text and not weak_overlay:
        return False

    if not layout and not ctx.present:
        return False

    if layout and not ctx.public_figures:
        if not ctx.present and not ctx.is_meme:
            return _text_suggests_meme_vision(texts or [])
        return True
    if layout and ctx.present and weak_overlay:
        return True
    if ctx.present and ctx.is_meme and not ctx.public_figures:
        return True
    return False


def _text_suggests_meme_vision(texts: list[str]) -> bool:
    t = " ".join(texts).lower()
    if any(k in t for k in POSITIVE_MEME_CUES):
        return True
    return any(k in t for k in ("speech bubble", "headdress", "coconut", "deepfake", "photoshopped"))


def _synthetic_ai_overlay(combined: str, ctx: IndonesianMemeContext) -> list[str]:
    """Metadata overlay untuk AI/deepfake tanpa teks terbaca."""
    if ctx.overlay_text:
        return ctx.overlay_text
    t = combined.lower()
    tags: list[str] = []
    if _detect_political_kiss_fake(combined):
        tags.append("deepfake jokowi prabowo kiss")
    elif _detect_costume_satire(combined):
        tags.append("ai sawit costume")
    elif any(k in t for k in ("deepfake", "ai generated", "ai-generated", "synthetic")):
        tags.append("ai generated political meme")
    elif "edited_photo" in ctx.signals or "ai_generated" in ctx.signals:
        tags.append("edited political meme")
    return tags


def finalize_meme_context(
    ctx: IndonesianMemeContext,
    *,
    layout: Optional[list[str]] = None,
    description: str = "",
    visual_figures: Optional[list[str]] = None,
) -> IndonesianMemeContext:
    """Polish akhir: overlay bersih, figur tepat, metadata AI/deepfake."""
    from .meme_ocr import clean_overlay_lines, finalize_ocr_lines, _ocr_phrase_hit

    overlay = clean_overlay_lines(finalize_ocr_lines(list(ctx.overlay_text)))
    phrase_only = [ln for ln in overlay if _ocr_phrase_hit(ln)]
    layout_set = set(layout or ())
    if phrase_only and ("speech_bubble" in layout_set or len(phrase_only) >= len(overlay) // 2 + 1):
        overlay = clean_overlay_lines(phrase_only + [
            ln for ln in overlay if ln not in phrase_only and _ocr_phrase_hit(ln)
        ])
    visual = list(visual_figures if visual_figures is not None else ctx.public_figures)

    if not overlay and ctx.is_meme:
        overlay = _synthetic_ai_overlay(description, ctx.model_copy(update={"overlay_text": overlay}))

    figures = resolve_public_figures(visual=visual, overlay=overlay, layout=layout)
    blob = " ".join(overlay)
    topics = sorted(set(ctx.topics + _match_patterns(blob, _get_topic_patterns())))
    satire = sorted(set(ctx.satire_type + _match_patterns(blob, _get_satire_patterns())))

    if overlay and not ctx.has_text_overlay:
        has_text = True
    else:
        has_text = ctx.has_text_overlay or bool(overlay)

    conf = ctx.confidence
    if figures and overlay:
        conf = max(conf, 0.88)
    elif figures and ctx.is_meme:
        conf = max(conf, 0.82)

    return ctx.model_copy(update={
        "overlay_text": overlay[:8],
        "public_figures": figures,
        "topics": topics,
        "satire_type": satire,
        "has_text_overlay": has_text,
        "confidence": conf,
    })


def meme_needs_extra_vision(
    ctx: IndonesianMemeContext,
    bgr: Optional[np.ndarray],
    layout: Optional[list[str]] = None,
    texts: Optional[list[str]] = None,
) -> bool:
    """Panggil VLM JSON hanya jika teks+OCR belum cukup."""
    if ctx.present and ctx.is_meme and ctx.public_figures and ctx.confidence >= 0.75:
        return False
    if ctx.present and ctx.is_meme and ctx.public_figures and ctx.satire_type and ctx.confidence >= 0.72:
        return False
    if ctx.present and ctx.is_meme and ctx.confidence >= 0.82:
        return False
    if ctx.present and ctx.is_meme and ctx.public_figures:
        if "deepfake" in ctx.satire_type or "ai_generated" in ctx.signals:
            return False

    if layout is None and bgr is not None:
        layout = detect_meme_layout(bgr)

    if texts and _text_suggests_meme_vision(texts) and not ctx.public_figures:
        return True
    if not ctx.present and not layout:
        return False
    if ctx.present and not ctx.public_figures:
        return True
    if not ctx.present and layout:
        return True
    return ctx.confidence < 0.70


def merge_ocr_overlay(
    ctx: IndonesianMemeContext,
    ocr_lines: list[str],
    layout: Optional[list[str]] = None,
) -> IndonesianMemeContext:
    if not ocr_lines:
        return ctx

    from .meme_ocr import (
        _ocr_line_useful,
        _ocr_phrase_hit,
        _dedupe_overlay_lines,
        _ocr_line_quality,
        finalize_ocr_lines,
        overlay_is_placeholder,
        is_vlm_description_overlay,
        clean_overlay_lines,
    )

    ocr_lines = finalize_ocr_lines(ocr_lines)
    min_conf = 38.0 if ctx.present else 45.0
    filtered = [ln for ln in ocr_lines if _ocr_phrase_hit(ln) or _ocr_line_useful(ln, min_conf=min_conf)]
    if not filtered and ctx.present:
        filtered = [ln for ln in ocr_lines if _ocr_line_useful(ln, min_conf=32)]
    filtered = [ln for ln in filtered if _ocr_line_quality(ln) or _ocr_phrase_hit(ln)]
    if not filtered:
        return ctx

    filtered.sort(key=lambda ln: (not _ocr_phrase_hit(ln), -len(ln)))

    ocr_strong = bool(filtered) and all(
        _ocr_phrase_hit(ln) or _ocr_line_quality(ln) for ln in filtered
    )
    base_overlay = [] if overlay_is_placeholder(ctx.overlay_text) or ocr_strong else [
        ln for ln in ctx.overlay_text if not is_vlm_description_overlay(ln)
    ]
    overlay: list[str] = []
    seen: set[str] = set()
    for line in filtered + base_overlay:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            overlay.append(line)
    overlay = clean_overlay_lines(_dedupe_overlay_lines(finalize_ocr_lines(overlay)))[:8]
    # Prefer phrase-bearing OCR lines over generic VLM noise
    phrase_lines = [ln for ln in overlay if _ocr_phrase_hit(ln)]
    if phrase_lines:
        overlay = clean_overlay_lines(_dedupe_overlay_lines(phrase_lines + [
            ln for ln in overlay if ln not in phrase_lines and _ocr_line_quality(ln)
        ]))[:8]

    blob = " ".join(overlay)
    visual = list(ctx.public_figures)
    figures = resolve_public_figures(visual=visual, overlay=overlay, layout=layout)
    topics = sorted(set(ctx.topics + _match_patterns(blob, _get_topic_patterns())))
    satire = sorted(set(ctx.satire_type + _match_patterns(blob, _get_satire_patterns())))
    lang = _detect_language(blob) if blob.strip() else ctx.text_language
    has_phrase = bool(match_figure_aliases(blob)) or _ocr_phrase_hit(blob)
    if not has_phrase and not ctx.present:
        return ctx

    signals = sorted(set(ctx.signals + ["ocr_overlay"]))
    conf = ctx.confidence
    if figures:
        conf = max(conf, 0.82 if overlay else 0.75)
    elif has_phrase:
        conf = max(conf, 0.75)
    elif overlay and ctx.present:
        conf = max(conf, 0.68)

    return ctx.model_copy(update={
        "present": ctx.present or has_phrase or bool(figures),
        "is_meme": ctx.is_meme or has_phrase or bool(figures),
        "has_text_overlay": ctx.has_text_overlay or bool(overlay),
        "overlay_text": overlay[:8],
        "public_figures": figures,
        "satire_type": satire,
        "topics": topics,
        "text_language": lang if lang != "unknown" else ctx.text_language,
        "signals": signals,
        "confidence": conf,
    })


def meme_needs_band_transcribe(
    ctx: IndonesianMemeContext,
    bgr: Optional[np.ndarray],
) -> bool:
    """Fallback VLM transcribe jika OCR gagal."""
    if bgr is None:
        return False
    from .meme_bands import build_band_strip

    if build_band_strip(bgr) is None:
        return False
    return not ctx.overlay_text or (ctx.present and not ctx.public_figures)
