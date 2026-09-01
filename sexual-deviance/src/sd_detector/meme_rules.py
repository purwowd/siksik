from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

# Fallback jika meme_rules.yaml tidak ada
_BUILTIN_FIGURES: dict[str, tuple[str, ...]] = {
    "jokowi": ("jokowi", "joko widodo", "widodo"),
    "prabowo": ("prabowo", "prabowo subianto", "subianto"),
    "gibran": ("gibran", "gibran rakabuming"),
    "anies": ("anies", "anies baswedan"),
    "ganjar": ("ganjar", "ganjar pranowo"),
    "bahlil": ("bahlil", "bahlil lahadalia"),
}


@dataclass
class MemeRules:
    figures: dict[str, tuple[str, ...]] = field(default_factory=dict)
    phrase_figures: dict[str, str] = field(default_factory=dict)
    ocr_aliases: dict[str, str] = field(default_factory=dict)
    format_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    satire_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    topic_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> MemeRules:
        def _tuple_map(section: dict) -> dict[str, tuple[str, ...]]:
            out: dict[str, tuple[str, ...]] = {}
            for key, vals in (section or {}).items():
                if isinstance(vals, list):
                    out[str(key)] = tuple(str(v).lower() for v in vals)
            return out

        figures = _tuple_map(raw.get("figures"))
        if not figures:
            figures = dict(_BUILTIN_FIGURES)

        phrase_figures: dict[str, str] = {}
        for phrase, fig in (raw.get("phrase_figures") or {}).items():
            phrase_figures[str(phrase).lower()] = str(fig).lower()

        ocr_aliases: dict[str, str] = {}
        for alias, canonical in (raw.get("ocr_aliases") or {}).items():
            ocr_aliases[str(alias).lower()] = str(canonical)

        return cls(
            figures=figures,
            phrase_figures=phrase_figures,
            ocr_aliases=ocr_aliases,
            format_patterns=_tuple_map(raw.get("format_patterns")),
            satire_patterns=_tuple_map(raw.get("satire_patterns")),
            topic_patterns=_tuple_map(raw.get("topic_patterns")),
        )

    def figure_patterns(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(k, v) for k, v in self.figures.items()]

    def format_pattern_list(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(k, v) for k, v in self.format_patterns.items()]

    def satire_pattern_list(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(k, v) for k, v in self.satire_patterns.items()]

    def topic_pattern_list(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(k, v) for k, v in self.topic_patterns.items()]


def _default_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "meme_rules.yaml"


@lru_cache(maxsize=4)
def load_meme_rules(path: Optional[str] = None) -> MemeRules:
    rules_path = Path(path) if path else _default_rules_path()
    if rules_path.is_file():
        with open(rules_path) as f:
            raw = yaml.safe_load(f) or {}
        return MemeRules.from_dict(raw)
    return MemeRules.from_dict({"figures": {k: list(v) for k, v in _BUILTIN_FIGURES.items()}})


def _norm_ocr(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _fuzzy_phrase_match(text: str, phrase: str) -> bool:
    if phrase in text.lower():
        return True
    from difflib import SequenceMatcher

    tn = _norm_ocr(text)
    pn = _norm_ocr(phrase)
    if not pn or not tn:
        return False
    if len(pn) < 10:
        return pn in tn
    min_len = max(4, len(pn) // 3)
    if len(tn) < min_len:
        return False
    if pn in tn:
        return True
    if len(tn) >= 5 and tn in pn:
        return True
    if SequenceMatcher(None, pn, tn).ratio() >= 0.78:
        return True
    window = len(pn) + 4
    for i in range(max(1, len(tn) - len(pn) + 1)):
        chunk = tn[i : i + window]
        if SequenceMatcher(None, pn, chunk).ratio() >= 0.86:
            return True
    return False


def match_phrase_figures(text: str, rules: Optional[MemeRules] = None) -> list[str]:
    rules = rules or load_meme_rules()
    t = text.lower()
    found: list[str] = []
    for phrase, fig_id in rules.phrase_figures.items():
        if phrase in t or _fuzzy_phrase_match(text, phrase):
            found.append(fig_id)
    return sorted(set(found))


def lookup_ocr_alias(line: str, rules: Optional[MemeRules] = None) -> Optional[str]:
    rules = rules or load_meme_rules()
    low = line.lower()
    norm = _norm_ocr(line)
    for alias, canonical in sorted(rules.ocr_aliases.items(), key=lambda x: -len(x[0])):
        if alias in low:
            return canonical
        if _norm_ocr(alias) in norm:
            return canonical
    return None


def match_visual_figures(text: str, rules: Optional[MemeRules] = None) -> list[str]:
    """Figur dari nama/alias pejabat saja — bukan frasa viral overlay."""
    rules = rules or load_meme_rules()
    t = text.lower()
    found: list[str] = []
    for fig_id, aliases in rules.figures.items():
        if any(alias in t for alias in aliases):
            found.append(fig_id)
    if re_joko(t) and "jokowi" not in found:
        found.append("jokowi")
    if "subianto" in t and "prabowo" not in found:
        found.append("prabowo")
    return sorted(set(found))


def resolve_public_figures(
    *,
    visual: list[str],
    overlay: list[str],
    layout: Optional[list[str]] = None,
) -> list[str]:
    """Gabung figur visual + overlay; caption meme pakai bar bawah sebagai subjek foto."""
    visual_set = set(visual)
    if not overlay:
        return sorted(visual_set)

    layout_set = set(layout or ())
    if "caption_bars" in layout_set and len(overlay) >= 2:
        for line in (overlay[-1], overlay[0]):
            pf = match_phrase_figures(line)
            if pf:
                primary = pf[0]
                if visual_set:
                    if primary in visual_set:
                        return sorted(visual_set)
                    if len(visual_set) == 1:
                        return sorted(visual_set)
                return sorted({primary})

    phrase_figs = match_phrase_figures(" ".join(overlay))
    if not visual_set:
        return sorted(set(phrase_figs))
    if len(visual_set) == 1 and not phrase_figs:
        return sorted(visual_set)
    if len(visual_set) == 1:
        v = next(iter(visual_set))
        if v in phrase_figs:
            return sorted(visual_set)
        return sorted(visual_set)
    overlap = visual_set & set(phrase_figs)
    return sorted(visual_set | overlap)


def match_figure_aliases(text: str, rules: Optional[MemeRules] = None) -> list[str]:
    rules = rules or load_meme_rules()
    t = text.lower()
    found: list[str] = list(match_phrase_figures(text, rules))
    for fig_id, aliases in rules.figures.items():
        if any(alias in t for alias in aliases):
            found.append(fig_id)
    if re_joko(t) and "jokowi" not in found:
        found.append("jokowi")
    if "subianto" in t and "prabowo" not in found:
        found.append("prabowo")
    return sorted(set(found))


def re_joko(t: str) -> bool:
    return bool(re.search(r"\bjoko\b", t)) or "widodo" in t
