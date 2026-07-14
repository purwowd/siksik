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
    "anti",
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
)


def _filename_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _risk_lexicon() -> list[str]:
    """Gabungan tag visual + keyword settings (frasa + token ≥4 huruf)."""
    tags = list(VISUAL_RISK_TAGS)
    for kw in settings.risk_keywords:
        low = kw.lower().strip()
        if low:
            tags.append(low)
        for tok in re.findall(r"[a-z0-9]{4,}", low):
            tags.append(tok)
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
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
            hit_tags = [t for t in _risk_lexicon() if t in hay]
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
    from app.services import ocr as ocr_mod
    from app.services import gpu_stack

    findings = _analyze_pil_image(path)
    findings.extend(ocr_mod.analyze_image_ocr(path))
    findings.extend(gpu_stack.analyze_image_gpu(path))
    if findings and gpu_device_name():
        for f in findings:
            f["evidence"] = (f["evidence"] + f" | gpu={gpu_device_name()}")[:320]
    return findings


def extract_video_keyframes(path: Path, max_frames: int = 3) -> list[Path]:
    """Extract representative frames via ffmpeg (prefer fps=1 over 1/10)."""
    if not shutil.which("ffmpeg"):
        return []
    out_dir = Path(tempfile.mkdtemp(prefix="sadt_kf_"))
    pattern = str(out_dir / "kf_%02d.jpg")
    probes = [
        # 1 fps — lebih cocok video pendek daripada fps=1/10
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            "fps=1",
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

    findings: list[dict] = []
    # filename / path cues against full risk lexicon
    hay = _filename_norm(f"{path.parent.name} {path.name}")
    hits = [t for t in _risk_lexicon() if t in hay]
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
        # Full GPU path: SafeWatch + Whisper + ICM/OCR/Qwen on keyframes
        findings.extend(gpu_stack.analyze_video_gpu(path))
        return findings

    frames = extract_video_keyframes(path, max_frames=3)
    for fr in frames:
        for f in analyze_image_file(fr):
            f["label"] = f"Video keyframe: {f['label']}"
            f["layer_origin"] = Layer.L4.value
            findings.append(f)
        try:
            fr.unlink(missing_ok=True)
        except OSError:
            pass
    if frames:
        try:
            frames[0].parent.rmdir()
        except OSError:
            pass
    return findings


def vision_status() -> dict:
    from app.services.ocr import ocr_status
    from app.services import gpu_stack

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
    st = gpu_stack.get_stack_status()
    info["gpu_stack"] = {
        "enabled": st.enabled,
        "device": st.device,
        "backends": st.backends,
    }
    return info
