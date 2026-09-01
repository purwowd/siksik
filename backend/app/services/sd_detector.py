"""Sexual-deviance adapter: NudeNet + SmolVLM sidecar → SIKSIK findings.

Runs off the event loop (analysis workers already use ``asyncio.to_thread``).
One process-wide lock serializes VLM calls so a 6 GB GPU with sidecar ``-np 1``
is not oversubscribed. Sidecar failure is fail-soft: callers fall back to
the existing NudeNet path and must not cache a false-clean result.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.models.schemas import Layer

log = logging.getLogger(__name__)

CATEGORY_NUDITY = "ketelanjangan"
CATEGORY_LGBT = "lgbt_content"
CATEGORY_POLITICAL_MEME = "political_meme"
CATEGORY_MEME = "meme"
EVIDENCE_MAX = 320
MAPPING_REVISION = "meme-visual-v3"
_LABEL_DETAIL_MAX = 80
_REASON_PREFIXES = ("Rules:", "NudeNet:", "LLM:", "VLM:")
_ANIMATED_STILL_EXT = frozenset({".webp", ".gif"})
_WEAK_LGBT_CLOTHING = frozenset({"rainbow clothing", "rainbow outfit", "rainbow_clothing"})
_DEVICE_MARKERS = (
    "content://",
    "/sdcard",
    "/storage/emulated",
    "/mnt/sdcard",
    "file://",
)

_ORIENTATION_ID = {
    "none": "tidak terdeteksi",
    "heterosexual": "pria dan wanita",
    "gay": "dua pria",
    "lesbian": "dua wanita",
    "bisexual": "biseksual",
    "other": "lainnya",
}
_ACT_ID = {
    "kissing": "ciuman",
    "kiss": "ciuman",
    "nudity": "ketelanjangan",
    "sexual_contact": "kontak seksual",
    "bikini": "bikini",
    "lingerie": "lingerie",
    "shirtless": "tanpa atasan",
    "topless": "tanpa atasan",
}
_FLAG_ID = {
    "rainbow": "pelangi",
    "pride": "pelangi",
    "trans": "trans",
    "progress": "progress pride",
}
_FIGURE_ID = {
    "jokowi": "Jokowi",
    "prabowo": "Prabowo",
    "gibran": "Gibran",
    "megawati": "Megawati",
    "anies": "Anies",
    "ganjar": "Ganjar",
    "sandiaga": "Sandiaga",
    "mahfud": "Mahfud",
    "luhut": "Luhut",
    "erick_thohir": "Erick Thohir",
    "sri_mulyani": "Sri Mulyani",
    "purbaya": "Purbaya",
    "bahlil": "Bahlil",
    "rocky_gerung": "Rocky Gerung",
    "ridwan_kamil": "Ridwan Kamil",
    "basuki": "Ahok",
}
_SATIRE_ID = {
    "political_satire": "satire politik",
    "sarcasm": "sarkasme",
    "criticism": "kritik",
    "humor": "humor",
    "deepfake": "deepfake",
    "caption_meme": "caption",
    "ai_generated": "buatan AI",
    "caricature": "karikatur",
    "edited_photo": "foto suntingan",
}
_TOPIC_ID = {
    "palm_oil": "sawit",
    "corruption": "korupsi",
    "election": "pemilu",
    "economy": "ekonomi",
    "government": "pemerintah",
    "social_issue": "isu sosial",
}
_POLITICAL_SATIRE = frozenset({"political_satire", "criticism", "deepfake"})
_POLITICAL_TOPICS = frozenset(_TOPIC_ID)
_OVERLAY_EVIDENCE_MAX = 120
_ENGLISH_HINTS = frozenset(
    {
        "a",
        "an",
        "app",
        "are",
        "at",
        "calendar",
        "call",
        "cat",
        "different",
        "displaying",
        "group",
        "holding",
        "image",
        "in",
        "marching",
        "meme",
        "news",
        "of",
        "page",
        "people",
        "placard",
        "posts",
        "screen",
        "showing",
        "shows",
        "social",
        "staring",
        "street",
        "suggests",
        "the",
        "video",
        "with",
    }
)
_REASON_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (
            r"\ba group of people are holding a placard and marching in the street\b",
            "sekelompok orang membawa plakat dan berbaris di jalan",
        ),
        (
            r"\bpage showing different people in a video call\b",
            "halaman menampilkan beberapa orang dalam panggilan video",
        ),
        (
            r"\bscreen showing posts in a social app\b",
            "tampilan layar menampilkan unggahan di aplikasi sosial",
        ),
        (
            r"\bscreen displaying the page of a news app\b",
            "tampilan layar halaman aplikasi berita",
        ),
        (
            r"\bscreen displaying the page of(?: a)? meme\b",
            "tampilan layar halaman meme",
        ),
        (
            r"\bscreen (?:shows|showing)(?: a| the)? page of a social(?: media)? app\b",
            "tampilan layar halaman aplikasi sosial",
        ),
        (
            r"\bscreen displaying the page of a social(?: media)? app\b",
            "tampilan layar halaman aplikasi sosial",
        ),
        (r"\bscreen displaying the meme\b", "tampilan layar menampilkan meme"),
        (r"\bscreen showing meme\b", "tampilan layar berisi meme"),
        (r"\bscreen shows meme\b", "tampilan layar berisi meme"),
        (r"\bin the image suggests that\b", "pada gambar tampak bahwa"),
        (r"\ba cat staring at a calendar\b", "seekor kucing menatap kalender"),
        (r"\bsocial media app\b", "aplikasi media sosial"),
        (r"\bsocial app\b", "aplikasi sosial"),
        (r"\bnews app\b", "aplikasi berita"),
        (r"\bvideo call\b", "panggilan video"),
        (r"\bscreen showing\b", "tampilan layar menampilkan"),
        (r"\bscreen shows\b", "tampilan layar menampilkan"),
        (r"\bscreen displaying(?: the)?\b", "tampilan layar menampilkan"),
    )
)


@dataclass(frozen=True)
class SdAnalysisResult:
    findings: tuple[dict[str, Any], ...]
    cacheable: bool
    used: bool
    warning: str | None = None


@dataclass
class _DetectorState:
    attempted: bool = False
    detector: Any | None = None
    error: str | None = None


_state = _DetectorState()
_state_lock = threading.Lock()
_inference_lock = threading.Lock()


def reset_detector_state() -> None:
    """Drop process-local detector state (tests / config reload only)."""
    global _state
    with _state_lock:
        _state = _DetectorState()


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().casefold()


def staging_relative_path(path: Path) -> str:
    """Staging-relative POSIX path for operator evidence; never a device URI."""
    try:
        rel = path.resolve().relative_to(settings.staging_dir.resolve())
        text = rel.as_posix()
    except (ValueError, OSError):
        text = path.name
    lowered = text.casefold()
    if any(marker in lowered for marker in _DEVICE_MARKERS):
        return path.name
    if text.startswith("/") or ":\\" in text or text.startswith("\\\\"):
        return path.name
    return text[:240]


def _fit_evidence(parts: list[str]) -> str:
    chunks = [part.rstrip(" .") for part in parts if part and part.strip()]
    text = ". ".join(chunks)
    if text and not text.endswith("."):
        text += "."
    if len(text) <= EVIDENCE_MAX:
        return text
    return text[: EVIDENCE_MAX - 1].rstrip() + "…"


def _sexual_label(*, action: str, severity: str, nudity: str) -> str:
    if action == "block":
        base = "Diblokir: konten eksplisit"
    elif severity == "explicit":
        base = "Perlu ditinjau: konten eksplisit"
    else:
        base = "Perlu ditinjau: konten sugestif"
    if nudity == "partial":
        return f"{base}, ketelanjangan sebagian"
    if nudity == "full":
        return f"{base}, ketelanjangan penuh"
    return base


def _strip_reason_prefix(reason: str) -> str:
    text = " ".join(reason.split())
    for prefix in _REASON_PREFIXES:
        if text.casefold().startswith(prefix.casefold()):
            text = text[len(prefix) :].strip(" —-")
            break
    lowered = text.casefold()
    for lead in ("safe —", "safe -", "safe:"):
        if lowered.startswith(lead):
            text = text[len(lead) :].strip(" —-")
            break
    return text


def _looks_english(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.casefold())
    if len(words) < 2:
        return False
    return sum(1 for word in words if word in _ENGLISH_HINTS) >= 2


def _id_sentence(text: str) -> str:
    value = " ".join(text.split()).strip()
    if not value:
        return ""
    if value.isupper() and any(character.isalpha() for character in value):
        return value
    first = value[0].upper() + value[1:] if value[0].islower() else value
    if first[-1] not in ".?!":
        first += "."
    return first


def _localize_reason(reason: str) -> str:
    """Operator-facing Indonesian sentence; keep overlay/OCR tokens intact."""
    text = _strip_reason_prefix(reason)
    if not text:
        return ""
    if not _looks_english(text):
        return text
    trailing = ""
    if text[-1] in ".?!":
        trailing = text[-1]
        text = text[:-1]
    localized = text
    for pattern, replacement in _REASON_PHRASES:
        localized = pattern.sub(replacement, localized)
    localized = " ".join(localized.split())
    if trailing and localized and localized[-1] not in ".?!":
        localized += trailing
    return _id_sentence(localized)


def _clip_label_detail(text: str) -> str:
    value = " ".join(text.split())
    if len(value) <= _LABEL_DETAIL_MAX:
        return value
    return value[: _LABEL_DETAIL_MAX - 1].rstrip() + "…"


def _meme_label(
    *,
    political: bool,
    figures: list[str],
    topics: list[str],
    overlay: str,
    reason: str,
) -> str:
    base = "Meme politik" if political else "Meme"
    bits = [*figures[:2], *topics[:2]]
    head = ", ".join(bits)
    explain = _clip_label_detail(overlay or _localize_reason(reason))
    if head and explain:
        return f"{base}: {head} — {explain}"
    if head:
        return f"{base}: {head}"
    if explain:
        return f"{base}: {explain}"
    return base


def _substantive_political_meme(*, figures: list[str], topics: list[str]) -> bool:
    return bool(figures or topics)


def _translate_acts(acts: object) -> list[str]:
    if not acts:
        return []
    values = acts if isinstance(acts, (list, tuple)) else [acts]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        key = str(item or "").strip().casefold()
        if not key or key == "none":
            continue
        label = _ACT_ID.get(key, "")
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _translate_tokens(values: object, table: dict[str, str]) -> list[str]:
    if not values:
        return []
    items = values if isinstance(values, (list, tuple)) else [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip().casefold()
        if not key:
            continue
        label = table.get(key, key.replace("_", " "))
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _lgbt_context(verdict: Any) -> Any:
    return getattr(verdict, "lgbt", None)


def _meme_context(verdict: Any) -> Any:
    return getattr(verdict, "indonesian_meme", None)


def _overlay_snippet(values: object) -> str:
    items = values if isinstance(values, (list, tuple)) else [values]
    chunks: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())
        if text:
            chunks.append(text)
    snippet = " / ".join(chunks)
    if len(snippet) <= _OVERLAY_EVIDENCE_MAX:
        return snippet
    return snippet[: _OVERLAY_EVIDENCE_MAX - 1].rstrip() + "…"


def _token_keys(values: object) -> list[str]:
    if not values:
        return []
    items = values if isinstance(values, (list, tuple)) else [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _is_political_meme(*, figures: list[str], satire: list[str], topics: list[str]) -> bool:
    if figures:
        return True
    if any(item in _POLITICAL_SATIRE for item in satire):
        return True
    return any(item in _POLITICAL_TOPICS for item in topics)


def findings_from_verdict(
    verdict: Any,
    *,
    relative_path: str,
    layer: str,
) -> list[dict[str, Any]]:
    """Map a detector verdict to pending-compatible nudity, LGBT, and/or meme findings."""
    action = _enum_value(getattr(verdict, "action", "allow"))
    severity = _enum_value(getattr(verdict, "severity", "safe"))
    nudity = _enum_value(getattr(verdict, "nudity", "none"))
    orientation = _enum_value(getattr(verdict, "orientation", "none"))
    confidence = float(getattr(verdict, "confidence", 0.0) or 0.0)
    reason = " ".join(str(getattr(verdict, "reason", "") or "").split())
    reason_id = _localize_reason(reason)
    acts = _translate_acts(getattr(verdict, "acts", ()))
    berkas = relative_path.strip() or "berkas"
    findings: list[dict[str, Any]] = []

    if action in {"review", "block"}:
        evidence_parts = [f"Berkas: {berkas}"]
        evidence_parts.append(
            f"Relasi: {_ORIENTATION_ID.get(orientation, 'tidak terdeteksi')}"
        )
        if acts:
            evidence_parts.append(f"Adegan: {', '.join(acts)}")
        if reason_id:
            evidence_parts.append(f"Alasan: {reason_id[:160]}")
        findings.append(
            {
                "category": CATEGORY_NUDITY,
                "label": _sexual_label(
                    action=action, severity=severity, nudity=nudity
                ),
                "confidence": round(min(0.99, max(confidence, 0.0)), 3),
                "layer_origin": layer,
                "evidence": _fit_evidence(evidence_parts),
            }
        )

    lgbt = _lgbt_context(verdict)
    present = bool(getattr(lgbt, "present", False)) if lgbt is not None else False
    flags = _translate_tokens(getattr(lgbt, "flag_colors", ()), _FLAG_ID) if lgbt is not None else []
    symbols = _translate_tokens(getattr(lgbt, "symbols", ()), _FLAG_ID) if lgbt is not None else []
    clothing = [
        item
        for item in (
            _translate_tokens(getattr(lgbt, "clothing", ()), _FLAG_ID) if lgbt is not None else []
        )
        if item.casefold() not in _WEAK_LGBT_CLOTHING
    ]
    # Weak "present" with no flag/symbol/clothing is a VLM false positive (office photo, national flag).
    if present and action == "allow" and (flags or symbols or clothing):
        lgbt_parts = [f"Berkas: {berkas}"]
        if flags:
            lgbt_parts.append(f"Bendera: {', '.join(flags)}")
        if symbols:
            lgbt_parts.append(f"Simbol: {', '.join(symbols)}")
        if clothing:
            lgbt_parts.append(f"Pakaian: {', '.join(clothing)}")
        findings.append(
            {
                "category": CATEGORY_LGBT,
                "label": "Indikasi visual LGBT",
                "confidence": round(min(0.99, max(confidence, 0.55)), 3),
                "layer_origin": layer,
                "evidence": _fit_evidence(lgbt_parts),
                "keep_label": True,
            }
        )

    meme = _meme_context(verdict)
    is_meme = bool(getattr(meme, "is_meme", False)) if meme is not None else False
    if is_meme:
        figures = _translate_tokens(
            getattr(meme, "public_figures", ()), _FIGURE_ID
        )
        satire = _translate_tokens(getattr(meme, "satire_type", ()), _SATIRE_ID)
        topics = _translate_tokens(getattr(meme, "topics", ()), _TOPIC_ID)
        overlay = _overlay_snippet(getattr(meme, "overlay_text", ()))
        political = _is_political_meme(
            figures=_token_keys(getattr(meme, "public_figures", ())),
            satire=_token_keys(getattr(meme, "satire_type", ())),
            topics=_token_keys(getattr(meme, "topics", ())),
        )
        keep_meme = action not in {"review", "block"} or _substantive_political_meme(
            figures=figures, topics=topics
        )
        if keep_meme:
            meme_conf = float(getattr(meme, "confidence", 0.0) or 0.0)
            meme_parts = [f"Berkas: {berkas}"]
            if figures:
                meme_parts.append(f"Figur: {', '.join(figures)}")
            if satire:
                meme_parts.append(f"Jenis: {', '.join(satire)}")
            if topics:
                meme_parts.append(f"Topik: {', '.join(topics)}")
            if overlay:
                meme_parts.append(f"Teks: {overlay}")
            if reason_id:
                meme_parts.append(f"Alasan: {reason_id[:160]}")
            findings.append(
                {
                    "category": CATEGORY_POLITICAL_MEME if political else CATEGORY_MEME,
                    "label": _meme_label(
                        political=political,
                        figures=figures,
                        topics=topics,
                        overlay=overlay,
                        reason=reason_id,
                    ),
                    "confidence": round(min(0.99, max(meme_conf, confidence, 0.55)), 3),
                    "layer_origin": layer,
                    "evidence": _fit_evidence(meme_parts),
                    "keep_label": True,
                }
            )
    return findings


def _config_path() -> Path:
    configured = Path(settings.sd_config_path)
    if configured.is_file():
        return configured
    fallback = PROJECT_ROOT / "sexual-deviance" / "config.yaml"
    return fallback


def _mode_from_settings() -> Any:
    from sd_detector.modes import DetectionMode

    raw = str(settings.sd_mode or "balanced").strip().casefold()
    try:
        return DetectionMode(raw)
    except ValueError:
        return DetectionMode.BALANCED


def _build_detector() -> Any:
    from sd_detector import ContentDetector
    from sd_detector.config import load_config

    path = _config_path()
    if not path.is_file():
        raise FileNotFoundError("sd_config_missing")
    cfg = load_config(path)
    cfg.llama.host = str(settings.sd_llama_host or "127.0.0.1")
    cfg.llama.port = int(settings.sd_llama_port)
    cfg.detector.mode = _mode_from_settings()
    cfg.detector.timeout.balanced_sec = float(settings.sd_timeout_sec)
    cfg.detector.timeout.full_sec = max(
        float(settings.sd_timeout_sec),
        min(float(settings.sd_video_timeout_sec), 120.0),
    )
    detector = ContentDetector(config=cfg, external_server=True, mode=cfg.detector.mode)
    detector.start()
    return detector


def _get_detector() -> Any | None:
    global _state
    with _state_lock:
        if _state.attempted:
            return _state.detector
        _state.attempted = True
        try:
            _state.detector = _build_detector()
            _state.error = None
        except Exception as exc:
            _state.detector = None
            _state.error = type(exc).__name__
            log.warning("sd_detector_unavailable error=%s", type(exc).__name__)
        return _state.detector


def sidecar_reachable() -> bool:
    """Cheap health probe for start scripts and status; no media decode."""
    import httpx

    host = str(settings.sd_llama_host or "127.0.0.1")
    port = int(settings.sd_llama_port)
    url = f"http://{host}:{port}/health"
    try:
        response = httpx.get(url, timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def status() -> dict[str, Any]:
    return {
        "enabled": bool(settings.sd_detector_enabled),
        "mode": str(settings.sd_mode or "balanced"),
        "host": str(settings.sd_llama_host or "127.0.0.1"),
        "port": int(settings.sd_llama_port),
        "sidecar": sidecar_reachable() if settings.sd_detector_enabled else False,
        "error": _state.error,
    }


def _run_locked(path: Path, is_video: bool) -> Any:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    detector = _get_detector()
    if detector is None:
        raise RuntimeError(_state.error or "sd_detector_unavailable")
    timeout = (
        float(settings.sd_video_timeout_sec) if is_video else float(settings.sd_timeout_sec)
    )
    with _inference_lock:
        if not is_video:
            return detector.analyze_image(path)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(detector.analyze_video, path)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout as exc:
                raise TimeoutError("sd_video_timeout") from exc


def _analyze(path: Path, *, is_video: bool) -> SdAnalysisResult:
    if not settings.sd_detector_enabled:
        return SdAnalysisResult((), True, used=False)
    try:
        try:
            result = _run_locked(path, is_video)
        except Exception as exc:
            still = path.suffix.lower() in _ANIMATED_STILL_EXT
            if not (is_video and still):
                raise
            log.warning(
                "sd_video_still_fallback error=%s suffix=%s",
                type(exc).__name__,
                path.suffix.lower(),
            )
            result = _run_locked(path, False)
        verdict = getattr(result, "verdict", result)
        relative = staging_relative_path(path)
        layer = Layer.L4.value if is_video else Layer.L3.value
        findings = tuple(findings_from_verdict(verdict, relative_path=relative, layer=layer))
        action = _enum_value(getattr(verdict, "action", "allow"))
        log.info(
            "sd_detector_ok action=%s findings=%s video=%s",
            action,
            len(findings),
            int(is_video),
        )
        return SdAnalysisResult(findings, True, used=True)
    except Exception as exc:
        log.warning("sd_detector_unavailable error=%s", type(exc).__name__)
        return SdAnalysisResult((), False, used=False, warning="sd_detector_unavailable")


def analyze_image_result(path: Path) -> SdAnalysisResult:
    return _analyze(path, is_video=False)


def analyze_video_result(path: Path) -> SdAnalysisResult:
    return _analyze(path, is_video=True)
