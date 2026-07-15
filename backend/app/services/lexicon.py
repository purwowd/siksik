"""Risk lexicon matching — word-boundary (hindari FP: anti⊂ganti, bom⊂noscobom)."""

from __future__ import annotations

import re

from app.core.config import settings
from app.models.schemas import Layer

# Token / frasa terlalu generik — jangan match sendiri (masih OK sebagai bagian frasa penuh)
_SKIP_SOLO_TOKENS = frozenset(
    {
        "anti",  # anti⊂ganti; pakai frasa "anti pemerintah" / "anti presiden"
        "ganti",  # terlalu umum; pakai frasa "ganti presiden"
        "online",
        "anak",
        "ilegal",
        "kali",
    }
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def contains_phrase(haystack: str, phrase: str) -> bool:
    """True jika phrase muncul sebagai kata/frasa utuh (bukan substring)."""
    hay = normalize_text(haystack)
    p = normalize_text(phrase)
    if not hay or not p:
        return False
    parts = [re.escape(w) for w in p.split() if w]
    if not parts:
        return False
    body = r"[\s\-_/\.]+".join(parts)
    pat = rf"(?<![a-z0-9]){body}(?![a-z0-9])"
    return bool(re.search(pat, hay))


def match_keywords(
    text: str,
    keywords: list[str] | None = None,
    *,
    min_token_len: int = 4,
    allow_token_fallback: bool = True,
) -> list[str]:
    """Kembalikan label yang match (frasa penuh dulu, lalu token ≥min_token_len)."""
    kws = keywords if keywords is not None else settings.risk_keywords
    hits: list[str] = []
    seen: set[str] = set()
    for kw in kws:
        low = kw.lower().strip()
        if not low:
            continue
        if contains_phrase(text, low):
            if low not in seen:
                seen.add(low)
                hits.append(kw)
            continue
        if not allow_token_fallback:
            continue
        for tok in re.findall(r"[a-z0-9]+", low):
            if len(tok) < min_token_len or tok in _SKIP_SOLO_TOKENS:
                continue
            if contains_phrase(text, tok) and tok not in seen:
                seen.add(tok)
                hits.append(tok)
                break
    return hits


def category_for_keyword(kw: str) -> str:
    return (
        "perilaku_menyimpang"
        if kw in ("narkoba", "judi online", "pornografi anak")
        else "anti_pemerintah"
    )


def findings_from_text(
    text: str,
    *,
    label_prefix: str,
    layer: str,
    confidence: float,
    backend: str | None = None,
    keywords: list[str] | None = None,
    extra_tags: list[str] | None = None,
) -> list[dict]:
    """Map teks → finding dicts seragam (OCR / ASR / path)."""
    if not text or not text.strip():
        return []
    corpus = list(keywords) if keywords is not None else list(settings.risk_keywords)
    if extra_tags:
        corpus = corpus + list(extra_tags)
    matched = match_keywords(text, corpus)
    out: list[dict] = []
    seen: set[str] = set()
    for m in matched:
        key = m.lower()
        if key in seen:
            continue
        seen.add(key)
        evidence = text[:280]
        if backend:
            evidence = f"[{backend}] {evidence}"
        out.append(
            {
                "category": category_for_keyword(m),
                "label": f"{label_prefix}: {m}",
                "confidence": confidence,
                "layer_origin": layer,
                "evidence": evidence[:320],
            }
        )
    return out


def layer_l3() -> str:
    return Layer.L3.value


def layer_l4() -> str:
    return Layer.L4.value


def layer_l1() -> str:
    return Layer.L1.value


def video_keyword_corpus() -> list[str]:
    """Gabungan lexicon utama + tag khusus video (ASR / nama file)."""
    seen: set[str] = set()
    out: list[str] = []
    for kw in list(settings.risk_keywords) + list(settings.video_risk_keywords):
        low = kw.lower().strip()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(kw)
    return out


def meme_hate_corpus() -> list[str]:
    """Ujaran/sindiran politik di meme + risk keywords."""
    seen: set[str] = set()
    out: list[str] = []
    for kw in list(settings.risk_keywords) + list(settings.meme_hate_keywords):
        low = kw.lower().strip()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(kw)
    return out


def meme_insult_corpus() -> list[str]:
    return list(settings.meme_insult_keywords)


def tokoh_name_hits(text: str) -> list[str]:
    return match_keywords(text, list(settings.tokoh_keywords), allow_token_fallback=True)


def hate_or_sindiran_hits(text: str, *, include_insults: bool = False) -> list[str]:
    """Frasa penuh saja — hindari FP: 'anti presidens' → token 'presiden' di teks netral."""
    corpus = meme_hate_corpus()
    if include_insults:
        corpus = corpus + meme_insult_corpus()
    return match_keywords(text, corpus, allow_token_fallback=False)
