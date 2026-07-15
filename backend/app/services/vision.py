"""L3/L4 vision helpers — Pillow heuristics + optional GPU/torch + ffmpeg keyframes."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

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
            if red_ratio > 1.35 and edge_mean > 18:
                score += 0.35
                reasons.append(f"red_dom={red_ratio:.2f},edge={edge_mean:.1f}")
            if hit_tags:
                score += 0.45
                reasons.append("tags=" + ",".join(hit_tags[:4]))
            if edge_mean > 40 and (r + g + b) / 3 < 90:
                score += 0.2
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


def analyze_image_file(path: Path) -> list[dict]:
    from app.services import clip_tokoh
    from app.services import gpu_stack
    from app.services import media_text
    from app.services import ocr as ocr_mod

    findings = _analyze_pil_image(path)

    # Satu pass OCR → teks untuk lexicon + fusi meme/tokoh
    ocr_text = ""
    ocr_backend: str | None = None
    ocr_findings: list[dict] = []
    if settings.ocr_enabled or settings.media_text_enabled:
        # Legacy path when OCR flag on
        if settings.ocr_enabled:
            ocr_text, ocr_backend = ocr_mod.extract_image_text(path)
            if ocr_text:
                ocr_findings = ocr_mod.ocr_findings_from_text(ocr_text, backend=ocr_backend or "ocr")
        else:
            # media_text best-effort (EasyOCR/Paddle tanpa SADT_OCR_ENABLED)
            mt = media_text.ocr_image_best_effort(path)
            ocr_findings.extend(mt)
            # Ambil cuplikan teks dari evidence jika ada
            for f in mt:
                ev = str(f.get("evidence") or "")
                if ev and not ocr_text:
                    # evidence sering: "[easyocr] teks…"
                    ocr_text = ev.split("] ", 1)[-1] if "] " in ev else ev
                    ocr_backend = "media_text"

    tokoh_findings = clip_tokoh.analyze_image_tokoh(path)
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

    findings.extend(gpu_stack.analyze_image_gpu(path))
    if findings and gpu_device_name():
        for f in findings:
            f["evidence"] = (f["evidence"] + f" | gpu={gpu_device_name()}")[:320]
    return _dedupe_findings(findings)


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
    if not shutil.which("ffmpeg"):
        return []
    out_dir = Path(tempfile.mkdtemp(prefix="sadt_kf_"))
    pattern = str(out_dir / "kf_%02d.jpg")
    dur = video_duration_s(path)
    if dur and dur > 30 and max_frames > 0:
        interval = max(1, int(dur / max_frames))
        vf = f"fps=1/{interval}"
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
    return []


def analyze_video_file(path: Path) -> list[dict]:
    from app.services import gpu_stack
    from app.services import media_text

    findings: list[dict] = []
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

    if gpu_stack.stack_enabled():
        findings.extend(gpu_stack.analyze_video_gpu(path))
        return _dedupe_findings(findings)

    # ASR (Whisper) + keyframe visual + on-screen OCR — satu pass
    findings.extend(media_text.analyze_video_enrichment(path))
    return _dedupe_findings(findings)


def vision_status() -> dict:
    from app.services.ocr import ocr_status
    from app.services import gpu_stack
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
        "video_whisper_max_duration_s": cfg.video_whisper_max_duration_s,
        "video_whisper_transcribe_first_s": cfg.video_whisper_transcribe_first_s,
        "whisper": bool(cfg.gpu_whisper_enabled),
    }
    info["tuning"] = {
        "ocr_max_edge_px": cfg.ocr_max_edge_px,
        "ocr_sharpen": cfg.ocr_sharpen,
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
    st = gpu_stack.get_stack_status()
    info["gpu_stack"] = {
        "enabled": st.enabled,
        "device": st.device,
        "backends": st.backends,
    }
    return info
