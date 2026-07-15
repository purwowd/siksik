#!/usr/bin/env python3
"""One-shot comprehensive analysis of staging dump 1b0965e3… — prints JSON."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.config import settings
from app.services import analysis, media_text, vision
from app.services import ocr as ocr_mod
from app.services.gpu_stack import audio_whisper

ROOT = Path("data/staging/1b0965e3-5e4a-44e7-be35-1737d81f0d4a")
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm"}

ORACLE_TEXT = {
    "Screenshot_20260710_212545_Attacker_191.jpg": (
        "Extract Complete Choose your dictionary method Manual Pick Automatic Pick Agent "
        "PHANTOM STRIKE Brute Force"
    ),
    "id-11134207-7r991-llk54ugij23069.jpeg": (
        "RODA AMPAT RACING DISTRO SERIBU KALI GANTI PRESIDEN "
        "KALO KITA MALAS HIDUP YA TETEP SUSAH KERJA KAWAN"
    ),
    "G9bJkiyasAAbAn0.jpg": "INI DADA KALO DIBELAH ISINYA SAWIT SEMUA Prabowo",
    "i-have-no-one-to-send-this-to-v0-j58l2c08h3ag1.jpeg": (
        "WASPADA TERKENA PENYAKIT SAWIT GILA"
    ),
    "images.jpeg": (
        "MOMEN PRABOWO RAYAKAN ULANG TAHUN SESKAB TEDDY BERI KUE HINGGA TIUP LILIN iNews NEWS"
    ),
}


def main() -> None:
    files = sorted(
        p for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXT | VID_EXT
    )
    report: dict = {
        "session": str(ROOT.name),
        "engines": {
            "ffmpeg": vision.vision_status().get("ffmpeg"),
            "ocr": ocr_mod.ocr_status(),
            "whisper": audio_whisper.status(),
            "media_text_enabled": settings.media_text_enabled,
        },
        "files": [],
        "totals": {"ms": 0, "findings": 0},
    }
    object.__setattr__(settings, "gpu_whisper_model", "tiny")
    t_all = time.perf_counter()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        source = path.parent.name
        mime = "video/mp4" if path.suffix.lower() in VID_EXT else f"image/{path.suffix.lstrip('.')}"
        t0 = time.perf_counter()
        findings = analysis.analyze_content(path, mime, source, path.name, settings.risk_keywords)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        oracle_hits = []
        if path.name in ORACLE_TEXT:
            oracle_hits = ocr_mod.ocr_findings_from_text(ORACLE_TEXT[path.name], backend="oracle-visual")
        report["files"].append(
            {
                "path": rel,
                "bytes": path.stat().st_size,
                "ms": ms,
                "pipeline_findings": findings,
                "oracle_if_ocr_worked": oracle_hits,
                "is_screenshot": media_text.looks_like_chat_or_screenshot(path),
                "text_heavy_heuristic": media_text.looks_like_text_heavy_image(path)
                if path.suffix.lower() in IMG_EXT
                else None,
            }
        )
        report["totals"]["findings"] += len(findings)
        print(f"[{ms:>8.0f} ms] {rel} → {len(findings)} findings", flush=True)
    report["totals"]["ms"] = round((time.perf_counter() - t_all) * 1000, 1)
    out = Path("data/staging/_comprehensive_report.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nTOTAL {report['totals']['ms']} ms, findings={report['totals']['findings']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
