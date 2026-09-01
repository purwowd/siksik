#!/usr/bin/env python3
"""Validasi benchmark meme Indonesia — internet viral (43+) + user_memes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sd_detector import ContentDetector
from sd_detector.config import load_config
from sd_detector.modes import DetectionMode

INTERNET = ROOT / "samples" / "internet"
USER = ROOT / "samples" / "user_memes"


def check_meme(got: dict, exp: dict) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if got.get("present") != exp.get("meme_present", True):
        issues.append(f"present {got.get('present')} != {exp.get('meme_present')}")
    if exp.get("meme_is_meme") and not got.get("is_meme"):
        issues.append("is_meme=False")
    exp_figs = set(exp.get("meme_figure_any", []))
    got_figs = set(got.get("public_figures", []))
    if exp_figs and not exp_figs & got_figs:
        issues.append(f"figures exp={exp_figs} got={got_figs}")
    exp_sat = set(exp.get("meme_satire_any", []))
    got_sat = set(got.get("satire_type", []))
    if exp_sat and not exp_sat & got_sat:
        issues.append(f"satire exp={exp_sat} got={got_sat}")
    return not issues, issues


def main() -> int:
    internet_exp = json.loads((INTERNET / "expected.json").read_text())
    user_exp_path = USER / "expected.json"
    user_exp = json.loads(user_exp_path.read_text()) if user_exp_path.exists() else {}

    files: list[tuple[Path, dict]] = []
    for path in sorted(INTERNET.glob("*meme*.jpg")):
        exp = internet_exp.get(path.name)
        if exp and exp.get("meme_present"):
            files.append((path, exp))
    for path in sorted(USER.glob("*.png")):
        exp = user_exp.get(path.name)
        if exp:
            files.append((path, exp))

    if not files:
        print("No meme benchmark files found. Run: python scripts/download_samples.py")
        return 1

    config = load_config(ROOT / "config.yaml")
    config.detector.mode = DetectionMode.BALANCED
    config.detector.cache.enabled = False
    config.llama.spawn_server = False

    passed = failed = 0
    results = []

    with ContentDetector(config=config, external_server=True) as detector:
        detector.set_mode(DetectionMode.BALANCED)
        for path, exp in files:
            if not path.exists() or path.stat().st_size < 5000:
                print(f"SKIP {path.name} (missing)")
                continue
            v = detector.analyze(path).verdict
            got = v.indonesian_meme.model_dump(mode="json")
            ok, issues = check_meme(got, exp)
            sym = "✓" if ok else "✗"
            print(f"{sym} {path.name}" + (f"  {issues}" if issues else ""))
            if ok:
                passed += 1
            else:
                failed += 1
            results.append({
                "file": str(path.relative_to(ROOT)),
                "status": "PASS" if ok else "FAIL",
                "expected": exp,
                "got": {
                    "figures": got.get("public_figures"),
                    "overlay": got.get("overlay_text"),
                    "satire": got.get("satire_type"),
                    "is_meme": got.get("is_meme"),
                },
                "latency_ms": v.latency_ms,
            })

    total = passed + failed
    pct = (passed / total * 100) if total else 0
    print(f"\n{'='*50}")
    print(f"Meme benchmark: {passed}/{total} passed ({pct:.0f}%)")
    print(f"{'='*50}")

    report = ROOT / "samples" / "meme_benchmark_report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
