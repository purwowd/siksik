"""L3/L4 vision helpers — Pillow heuristics + optional GPU/torch + ffmpeg keyframes."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.core.config import settings
from app.models.schemas import Layer

# Visual risk cues (PoC — extendable lexicon)
VISUAL_RISK_TAGS = (
    "provokasi",
    "demo",
    "unjuk",
    "presiden",
    "makar",
    "bom",
    "senjata",
    "radikal",
    "separatis",
    "narkoba",
    "judi",
    "pornografi",
    "kudeta",
    "hasut",
    "gulingkan",
)
# Note: "anti" sengaja dihapus — terlalu pendek & substring FP (anti⊂ganti)
_ANIMATED_IMAGE_EXT = frozenset({".webp", ".gif"})


@dataclass(frozen=True)
class VideoAnalysisResult:
    findings: tuple[dict[str, Any], ...]
    cacheable: bool


def _filename_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _risk_lexicon() -> list[str]:
    """Gabungan tag visual + keyword settings + video tags."""
    from app.services.lexicon import video_keyword_corpus

    tags = list(VISUAL_RISK_TAGS) + video_keyword_corpus()
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        low = t.lower().strip()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(low)
        for tok in re.findall(r"[a-z0-9]{4,}", low):
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def gpu_device_name() -> str | None:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        return None
    return None


def _analyze_pil_image(path: Path) -> list[dict]:
    """Fast CV stand-in: color/edge heuristics + EXIF text cues."""
    try:
        from PIL import Image, ExifTags, ImageFilter, ImageStat
    except ImportError:
        return []

    findings: list[dict] = []
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            # Downscale for speed
            im.thumbnail((512, 512))
            stat = ImageStat.Stat(im)
            r, g, b = stat.mean
            # High red dominance often correlates with posters/flags in synthetic/lab cues
            red_ratio = r / max((r + g + b) / 3.0, 1.0)
            edges = im.filter(ImageFilter.FIND_EDGES)
            edge_mean = ImageStat.Stat(edges).mean[0]

            exif_blob = ""
            try:
                raw = im.getexif()
                if raw:
                    parts = []
                    for k, v in raw.items():
                        tag = ExifTags.TAGS.get(k, str(k))
                        parts.append(f"{tag}={v}")
                    exif_blob = " ".join(parts).lower()
            except Exception:
                pass

            hay = f"{exif_blob} {_filename_norm(path.name)}"
            from app.services.lexicon import contains_phrase

            hit_tags = [t for t in _risk_lexicon() if contains_phrase(hay, t)]
            score = 0.0
            reasons: list[str] = []
            if hit_tags:
                score += 0.55
                reasons.append("tags=" + ",".join(hit_tags[:4]))
                if red_ratio > 1.35 and edge_mean > 18:
                    score += 0.25
                    reasons.append(f"red_dom={red_ratio:.2f},edge={edge_mean:.1f}")
                if edge_mean > 40 and (r + g + b) / 3 < 90:
                    score += 0.15
                    reasons.append("high_contrast_dark")

            if score >= 0.45:
                findings.append(
                    {
                        "category": "konten_visual",
                        "label": f"CV L3: indikasi visual ({', '.join(reasons) or 'heuristic'})",
                        "confidence": round(min(0.92, 0.55 + score * 0.3), 3),
                        "layer_origin": Layer.L3.value,
                        "evidence": f"{path.name} | {'; '.join(reasons)}"[:320],
                    }
                )
    except Exception:
        return []

    return findings


def _optional_torch_warmup() -> dict:
    """Report GPU path readiness; real CLIP/classifier can plug here later."""
    name = gpu_device_name()
    return {"torch_cuda": bool(name), "device": name}


def analyze_image_file(
    path: Path,
    *,
    precomputed_ocr_text: str | None = None,
    precomputed_ocr_backend: str | None = None,
    origin_hint: str | None = None,
    context_text: str | None = None,
) -> list[dict]:
    from app.services import clip_tokoh
    from app.services import content_policy
    from app.services import content_visual
    from app.services import gpu_stack
    from app.services import media_text
    from app.services import ocr as ocr_mod

    findings = _analyze_pil_image(path)

    # Satu pass OCR → teks untuk lexicon + fusi meme/tokoh
    ocr_text = precomputed_ocr_text or ""
    ocr_backend: str | None = precomputed_ocr_backend
    ocr_findings: list[dict] = []
    if precomputed_ocr_text is not None:
        if ocr_text:
            ocr_findings = ocr_mod.ocr_findings_from_text(
                ocr_text,
                backend=ocr_backend or "host_ocr",
            )
    elif settings.ocr_enabled or settings.media_text_enabled:
        # Legacy path when OCR flag on
        if settings.ocr_enabled:
            ocr_text, ocr_backend = ocr_mod.extract_image_text(path)
            if ocr_text:
                ocr_findings = ocr_mod.ocr_findings_from_text(ocr_text, backend=ocr_backend or "ocr")
        else:
            # media_text best-effort (EasyOCR/Paddle tanpa SADT_OCR_ENABLED)
            mt = media_text.ocr_image_best_effort(path, origin_hint=origin_hint)
            ocr_findings.extend(mt)
            # Ambil cuplikan teks dari evidence jika ada
            for f in mt:
                ev = str(f.get("evidence") or "")
                if ev and not ocr_text:
                    # evidence sering: "[easyocr] teks…"
                    ocr_text = ev.split("] ", 1)[-1] if "] " in ev else ev
                    ocr_backend = "media_text"

    combined_context = "\n".join(
        value
        for value in ((context_text or "").strip(), ocr_text.strip())
        if value
    )[:6000]
    text_signal = content_policy.should_adjudicate_text(combined_context)
    text_heavy = media_text.looks_like_text_heavy_image(path, origin_hint)
    visual_skip_ui = media_text.should_skip_generic_visual_model(path, origin_hint)
    visual_candidates = []
    if not (
        settings.content_visual_skip_text_heavy_without_signal
        and text_heavy
        and visual_skip_ui
        and not text_signal
    ):
        visual_candidates = content_visual.analyze_image(path)

    # Optional real image model only. Qwen is candidate-gated below and must
    # not run unconditionally for every gallery item.
    findings.extend(
        gpu_stack.analyze_image_gpu(
            path,
            include_ocr=False,
            include_reasoning=False,
        )
    )
    decision = None
    reasoning_findings: list[dict] = []
    ambiguous_visual = content_policy.visual_candidates_requiring_reasoning(
        visual_candidates
    )
    if (
        settings.gpu_stack_enabled
        and settings.gpu_qwen_enabled
        and (ambiguous_visual or text_signal)
    ):
        from app.services.gpu_stack import reason_qwen

        decision = reason_qwen.moderate_image_decision(
            path,
            context_text=combined_context,
            candidate_categories=[
                str(item.get("category") or "") for item in ambiguous_visual
            ],
        )
        reasoning_findings = [hit.as_finding() for hit in decision.hits]

    support = list(ocr_findings) + reasoning_findings
    promoted_visual = content_policy.confirm_visual_candidates(
        visual_candidates,
        support,
        reasoning_verdict=decision.verdict if decision is not None else "unavailable",
    )
    findings.extend(promoted_visual)
    findings.extend(reasoning_findings)

    # Identity-like CLIP prompts are another expensive zero-shot pass. Run them
    # only after explicit text or contextual reasoning identifies political
    # material; they are supporting evidence, never the initial trigger.
    political_categories = {"political_meme", "political_campaign", "political_insult"}
    supported_categories = {
        str(item.get("category") or "") for item in support + promoted_visual
    }
    tokoh_findings = (
        clip_tokoh.analyze_image_tokoh(path)
        if text_signal or bool(political_categories & supported_categories)
        else []
    )
    findings.extend(
        ocr_mod.consolidate_image_findings(
            ocr_mod.fuse_tokoh_and_text(
                path=path,
                ocr_text=ocr_text,
                ocr_backend=ocr_backend,
                tokoh_findings=tokoh_findings,
                ocr_findings=ocr_findings,
            )
        )
    )

    if findings and gpu_device_name():
        for f in findings:
            f["evidence"] = (f["evidence"] + f" | gpu={gpu_device_name()}")[:320]
    return _dedupe_findings(content_policy.merge_content_findings(findings))


def analyze_lightweight_image_file(
    path: Path,
    *,
    precomputed_ocr_text: str | None = None,
    precomputed_ocr_backend: str | None = None,
    include_reasoning: bool = False,
    origin_hint: str | None = None,
    context_text: str | None = None,
) -> list[dict]:
    """Visual/content pass without starting another OCR engine.

    Used for social snapshots with host OCR and QUICK gallery images.  It keeps
    the anti-stall OCR policy while still detecting flags, campaigns, protests,
    political memes, and (for social screenshots) Qwen contextual categories.
    """
    from app.services import content_policy, content_visual, media_text
    from app.services import ocr as ocr_mod

    findings = _analyze_pil_image(path)
    text = (precomputed_ocr_text or "").strip()
    backend = precomputed_ocr_backend or "host_ocr"
    if text:
        findings.extend(ocr_mod.ocr_findings_from_text(text, backend=backend))
    combined_context = "\n".join(
        value
        for value in ((context_text or "").strip(), text)
        if value
    )[:6000]
    text_signal = content_policy.should_adjudicate_text(combined_context)
    text_heavy = bool(text) or media_text.looks_like_text_heavy_image(path, origin_hint)
    visual_skip_ui = media_text.should_skip_generic_visual_model(path, origin_hint)
    visual_candidates = []
    if not (
        settings.content_visual_skip_text_heavy_without_signal
        and text_heavy
        and visual_skip_ui
        and not text_signal
    ):
        visual_candidates = content_visual.analyze_image(path)
    decision = None
    reasoning_findings: list[dict] = []
    ambiguous_visual = content_policy.visual_candidates_requiring_reasoning(
        visual_candidates
    )
    if (
        include_reasoning
        and settings.gpu_stack_enabled
        and settings.gpu_qwen_enabled
        and (ambiguous_visual or text_signal)
    ):
        from app.services.gpu_stack import reason_qwen

        decision = reason_qwen.moderate_image_decision(
            path,
            context_text=combined_context,
            candidate_categories=[
                str(item.get("category") or "") for item in ambiguous_visual
            ],
        )
        reasoning_findings = [hit.as_finding() for hit in decision.hits]
    promoted = content_policy.confirm_visual_candidates(
        visual_candidates,
        list(findings) + reasoning_findings,
        reasoning_verdict=decision.verdict if decision is not None else "unavailable",
    )
    findings.extend(promoted)
    findings.extend(reasoning_findings)
    return _dedupe_findings(content_policy.merge_content_findings(findings))


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for f in findings:
        key = f"{f.get('label')}|{str(f.get('evidence', ''))[:60]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def is_animated_image(path: Path) -> bool:
    """True for multi-frame WebP/GIF so recovered clips are analyzed as video (L4)."""
    if path.suffix.lower() not in _ANIMATED_IMAGE_EXT:
        return False
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return False
    try:
        with Image.open(path) as image:
            frames = int(getattr(image, "n_frames", 1) or 1)
            return bool(getattr(image, "is_animated", False)) and frames > 1
    except (OSError, UnidentifiedImageError, ValueError):
        return False


def _animated_frame_indexes(frame_count: int, limit: int) -> list[int]:
    if frame_count < 1 or limit < 1:
        return []
    requested = min(frame_count, limit)
    if requested == 1:
        return [(frame_count - 1) // 2]
    last = frame_count - 1
    return sorted({round(index * last / (requested - 1)) for index in range(requested)})


def extract_animated_image_frames(path: Path, max_frames: int) -> list[Path]:
    """Sample WebP/GIF frames via Pillow when ffmpeg cannot treat them as video."""
    if max_frames < 1 or not is_animated_image(path):
        return []
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return []
    try:
        image = Image.open(path)
    except (OSError, UnidentifiedImageError):
        return []
    out_dir: Path | None = None
    try:
        total = int(getattr(image, "n_frames", 1) or 1)
        indexes = _animated_frame_indexes(total, max_frames)
        if not indexes:
            return []
        out_dir = Path(tempfile.mkdtemp(prefix="sadt_kf_"))
        frames: list[Path] = []
        for offset, index in enumerate(indexes, start=1):
            try:
                image.seek(index)
            except EOFError:
                continue
            dest = out_dir / f"kf_{offset:02d}.jpg"
            image.convert("RGB").save(dest, format="JPEG", quality=85)
            if dest.is_file() and dest.stat().st_size > 32:
                frames.append(dest)
        if frames:
            return frames
        shutil.rmtree(out_dir, ignore_errors=True)
        return []
    except (OSError, ValueError):
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)
        return []
    finally:
        image.close()


def video_duration_s(path: Path) -> float | None:
    """Durasi media via ffprobe (detik). None jika tidak bisa dibaca."""
    if not shutil.which("ffprobe"):
        return None
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        return None
    return None


def extract_video_keyframes(path: Path, max_frames: int = 3) -> list[Path]:
    """Extract representative frames via ffmpeg (spread across duration on long clips)."""
    animated = extract_animated_image_frames(path, max_frames)
    if animated:
        return animated
    if not shutil.which("ffmpeg"):
        return []
    out_dir = Path(tempfile.mkdtemp(prefix="sadt_kf_"))
    pattern = str(out_dir / "kf_%02d.jpg")
    dur = video_duration_s(path)
    if dur and max_frames > 0:
        interval = max(1.0, float(dur) / max_frames)
        vf = f"fps=1/{interval:.6f}"
    else:
        vf = "fps=1"
    probes = [
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            vf,
            "-frames:v",
            str(max_frames),
            pattern,
        ],
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(out_dir / "kf_01.jpg"),
        ],
    ]
    for cmd in probes:
        try:
            subprocess.run(cmd, capture_output=True, timeout=90, check=False)
        except Exception:
            continue
        frames = sorted(out_dir.glob("kf_*.jpg"))[:max_frames]
        if frames:
            return frames
    shutil.rmtree(out_dir, ignore_errors=True)
    return []


def _even_frame_subset(frames: Sequence[Path], limit: int) -> list[Path]:
    values = list(frames)
    if limit <= 0 or not values:
        return []
    if len(values) <= limit:
        return values
    if limit == 1:
        return [values[len(values) // 2]]
    last = len(values) - 1
    indexes = sorted({round(index * last / (limit - 1)) for index in range(limit)})
    return [values[index] for index in indexes]


def analyze_video_file_result(path: Path) -> VideoAnalysisResult:
    from app.services import gpu_stack
    from app.services import media_text
    from app.services import nudity
    from app.services import sd_detector
    from app.services.hash_cache import get_analysis_mode
    from app.models.schemas import AcquisitionMode

    findings: list[dict] = []
    cacheable = True
    # filename / path cues against full risk lexicon
    hay = _filename_norm(f"{path.parent.name} {path.name}")
    from app.services.lexicon import contains_phrase

    hits = [t for t in _risk_lexicon() if contains_phrase(hay, t)]
    if hits:
        findings.append(
            {
                "category": "konten_visual",
                "label": f"Video nama/path: {hits[0]}",
                "confidence": 0.74,
                "layer_origin": Layer.L4.value,
                "evidence": f"{path.name} | hits={','.join(hits[:6])}"[:320],
            }
        )

    nudity_frames = (
        settings.nudity_video_frames_quick
        if get_analysis_mode() == AcquisitionMode.QUICK
        else settings.nudity_video_frames_full
    )
    analyzer_frames = (
        settings.gpu_video_keyframes
        if gpu_stack.stack_enabled()
        else max(3, settings.video_overlay_keyframes)
    )
    shared_frames = extract_video_keyframes(
        path,
        max_frames=max(int(nudity_frames), int(analyzer_frames)),
    )
    try:
        sd_outcome = sd_detector.analyze_video_result(path)
        if sd_outcome.used:
            findings.extend(sd_outcome.findings)
            cacheable = cacheable and sd_outcome.cacheable
        if (not sd_outcome.used) or (not nudity.has_nudity_finding(sd_outcome.findings)):
            nudity_outcome = (
                nudity.analyze_video_frames_result(path, shared_frames)
                if shared_frames
                else nudity.analyze_video_result(path)
            )
            findings.extend(nudity_outcome.findings)
            cacheable = cacheable and nudity_outcome.cacheable
            if sd_outcome.warning:
                cacheable = False

        selected = _even_frame_subset(shared_frames, int(analyzer_frames))
        if gpu_stack.stack_enabled():
            findings.extend(
                gpu_stack.analyze_video_gpu(
                    path,
                    frames=selected if shared_frames else None,
                )
            )
        else:
            findings.extend(
                media_text.analyze_video_enrichment(
                    path,
                    frames=selected if shared_frames else None,
                )
            )
    finally:
        for frame in shared_frames:
            try:
                frame.unlink(missing_ok=True)
            except OSError:
                pass
        if shared_frames:
            try:
                shared_frames[0].parent.rmdir()
            except OSError:
                pass

    from app.services import content_policy

    merged = _dedupe_findings(content_policy.merge_content_findings(findings))
    return VideoAnalysisResult(tuple(merged), cacheable)


def analyze_video_file(path: Path) -> list[dict]:
    """Compatibility wrapper returning findings only."""
    return list(analyze_video_file_result(path).findings)


def vision_status() -> dict:
    from app.services.ocr import ocr_status
    from app.services import gpu_stack
    from app.services import nudity
    from app.services import sd_detector
    from app.core.config import settings as cfg

    pil_ok = False
    try:
        import PIL  # noqa: F401

        pil_ok = True
    except ImportError:
        pass
    info = _optional_torch_warmup()
    info["pillow"] = pil_ok
    info["ffmpeg"] = bool(shutil.which("ffmpeg"))
    info["max_side"] = 512
    info["image_cap_quick"] = settings.image_cap_quick
    info["ocr"] = ocr_status()
    info["media_text"] = {
        "enabled": bool(cfg.media_text_enabled),
        "video_overlay_keyframes": cfg.video_overlay_keyframes,
        "video_ocr_max_frames": cfg.video_ocr_max_frames,
        "video_whisper_max_duration_s": cfg.video_whisper_max_duration_s,
        "video_whisper_transcribe_first_s": cfg.video_whisper_transcribe_first_s,
        "whisper": bool(cfg.gpu_whisper_enabled),
    }
    info["nudity"] = nudity.status()
    info["sd_detector"] = sd_detector.status()
    info["tuning"] = {
        "ocr_max_edge_px": cfg.ocr_max_edge_px,
        "ocr_sharpen": cfg.ocr_sharpen,
        "gpu_qwen_max_edge_px": cfg.gpu_qwen_max_edge_px,
        "video_cap_quick": cfg.video_cap_quick,
        "video_cap_full": cfg.video_cap_full,
        "worker_concurrency": cfg.worker_concurrency,
        "cv_batch_size": cfg.cv_batch_size,
        "clip_tokoh": cfg.clip_tokoh_enabled,
    }
    try:
        from app.services import clip_tokoh

        info["clip_tokoh"] = clip_tokoh.status()
    except Exception:
        info["clip_tokoh"] = {"available": False}
    try:
        from app.services import content_text, content_visual

        info["content_detection"] = {
            "enabled": bool(cfg.content_detection_enabled),
            "visual": content_visual.status(),
            "text": content_text.status(),
            "qwen_structured": bool(cfg.content_qwen_structured),
            "qwen_candidate_only": True,
            "bridge_fallbacks": bool(cfg.gpu_bridge_fallbacks_enabled),
        }
    except Exception:
        info["content_detection"] = {"configured": False}
    st = gpu_stack.get_stack_status()
    info["gpu_stack"] = {
        "enabled": st.enabled,
        "device": st.device,
        "backends": st.backends,
    }
    return info
