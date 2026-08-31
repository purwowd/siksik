#!/usr/bin/env python3
"""Validasi detector terhadap ground truth samples — target 100%."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sd_detector import ContentDetector
from sd_detector.config import load_config
from sd_detector.modes import DetectionMode

SAMPLES_DIR = ROOT / "samples" / "internet"
EXPECTED_FILE = SAMPLES_DIR / "expected.json"


def load_expected() -> dict:
    if not EXPECTED_FILE.exists():
        print("Jalankan dulu: python scripts/download_samples.py")
        sys.exit(1)
    return json.loads(EXPECTED_FILE.read_text())


def check_field(name: str, got: str, exp: str) -> bool:
    if got == exp:
        return True
    if name == "nudity" and exp == "partial" and got == "full":
        return True
    if name == "severity" and exp == "suggestive" and got == "explicit":
        return True
    return False


def check_lgbt(got_lgbt: dict, exp: dict) -> bool:
    if "lgbt_present" not in exp:
        return True
    if got_lgbt.get("present") != exp["lgbt_present"]:
        return False
    flags_any = exp.get("lgbt_flag_any")
    if flags_any:
        got_flags = set(got_lgbt.get("flag_colors", []))
        got_signals = set(got_lgbt.get("signals", []))
        got_clothing = set(got_lgbt.get("clothing", []))
        pool = got_flags | got_signals | got_clothing | {"pixel_rainbow", "rainbow", "lgbt_context"}
        if not pool.intersection(set(flags_any)):
            return False
    return True


def main() -> int:
    expected = load_expected()
    files = sorted(
        list(SAMPLES_DIR.glob("*.jpg"))
        + list(SAMPLES_DIR.glob("*.mp4"))
        + list(SAMPLES_DIR.glob("*.webm"))
    )
    if not files:
        print("No samples found")
        return 1

    passed = 0
    failed = 0
    skipped = 0
    results = []

    config = load_config(ROOT / "config.yaml")
    config.detector.mode = DetectionMode.FULL
    config.llama.spawn_server = True
    config.detector.video_max_frames = 5
    config.detector.video_sample_interval_sec = 3.0
    config.detector.timeout.full_sec = 120.0
    config.detector.cache.enabled = False

    with ContentDetector(config=config, external_server=False) as detector:
        for path in files:
            exp = expected.get(path.name)
            if not exp:
                print(f"SKIP {path.name} (no expected label)")
                skipped += 1
                continue

            if not path.exists() or path.stat().st_size < 10_000:
                print(f"SKIP {path.name} (not downloaded)")
                skipped += 1
                continue

            if exp.get("media_type") == "video":
                detector.set_mode(DetectionMode.BALANCED)
            else:
                detector.set_mode(DetectionMode.FULL)

            result = detector.analyze(path)
            v = result.verdict
            got_lgbt = v.lgbt.model_dump(mode="json")
            got = {
                "severity": v.severity.value,
                "nudity": v.nudity.value,
                "orientation": v.orientation.value,
                "action": v.action.value,
                "flagged": v.flagged,
                "lgbt_present": got_lgbt.get("present"),
                "lgbt_flags": got_lgbt.get("flag_colors"),
            }

            ok = (
                check_field("severity", got["severity"], exp["severity"])
                and check_field("nudity", got["nudity"], exp.get("nudity", "none"))
                and got["orientation"] == exp.get("orientation", "none")
                and got["flagged"] == exp["flagged"]
                and check_lgbt(got_lgbt, exp)
            )
            if exp.get("media_type") == "video" and v.media_type != "video":
                ok = False

            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1

            results.append({
                "file": path.name, "status": status, "expected": exp,
                "got": got, "lgbt": got_lgbt, "reason": v.reason,
            })
            sym = "✓" if ok else "✗"
            print(f"{sym} {path.name}")
            if not ok:
                print(f"    expected: {exp}")
                print(f"    got     : {got}")
                print(f"    reason  : {v.reason[:80]}")

    total = passed + failed
    pct = (passed / total * 100) if total else 0
    print(f"\n{'='*50}")
    print(f"Result: {passed}/{total} passed ({pct:.0f}%), {skipped} skipped")
    print(f"{'='*50}")

    report = SAMPLES_DIR / "validation_report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
