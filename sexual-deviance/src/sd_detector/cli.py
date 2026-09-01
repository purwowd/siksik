#!/usr/bin/env python3
"""CLI entry point — `sd-detector` atau `python main.py`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .detector import ContentDetector
from .modes import DetectionMode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detector konten seksual — mode FAST / BALANCED / FULL",
    )
    parser.add_argument("media", nargs="+", help="Path gambar atau video")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path config.yaml")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in DetectionMode],
        help="Override mode (default dari config.yaml)",
    )
    parser.add_argument(
        "--spawn-server",
        action="store_true",
        help="Spawn llama-server lokal (dev tanpa sidecar)",
    )
    parser.add_argument(
        "--external-server",
        action="store_true",
        help="Connect ke llama-server sidecar (default jika spawn_server=false)",
    )
    parser.add_argument("--no-prescreen", action="store_true")
    parser.add_argument("--no-nudenet", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-meme", action="store_true", help="Nonaktifkan modul meme Indonesia")
    parser.add_argument("--json", action="store_true", help="Output JSON saja")
    parser.add_argument("--include-frames", action="store_true")
    parser.add_argument("--metrics", action="store_true", help="Tampilkan metrics setelah run")
    args = parser.parse_args(argv)

    mode = DetectionMode(args.mode) if args.mode else None
    external = False if args.spawn_server else (True if args.external_server else None)

    detector = ContentDetector(
        config_path=args.config,
        external_server=external,
        mode=mode,
    )
    if args.no_prescreen:
        detector.config.detector.prescreen_enabled = False
    if args.no_nudenet:
        detector.config.detector.nudenet_enabled = False
    if args.no_cache:
        detector.config.detector.cache.enabled = False
    if args.no_meme:
        detector.config.meme.enabled = False

    results = []
    with detector:
        for media_path in args.media:
            result = detector.analyze(media_path)
            d = result.to_dict()
            if not args.include_frames:
                d.pop("frames", None)
            results.append(d)

            if not args.json:
                v = result.verdict
                print(f"\n[{v.action.value.upper()}] {media_path}")
                print(f"  mode        : {v.mode}")
                print(f"  severity    : {v.severity.value}")
                print(f"  nudity      : {v.nudity.value}")
                print(f"  orientation : {v.orientation.value}")
                lgbt = v.lgbt
                if lgbt.present:
                    print(f"  lgbt        : present")
                    if lgbt.flag_colors:
                        print(f"  flag_colors : {', '.join(lgbt.flag_colors)}")
                    if lgbt.clothing:
                        print(f"  clothing    : {', '.join(lgbt.clothing)}")
                    if lgbt.scene:
                        print(f"  scene       : {', '.join(lgbt.scene)}")
                    if lgbt.orientation_hint != "none":
                        print(f"  lgbt_hint   : {lgbt.orientation_hint}")
                meme = v.indonesian_meme
                if meme.present:
                    print(f"  meme        : present")
                    if meme.public_figures:
                        print(f"  figures     : {', '.join(meme.public_figures)}")
                    if meme.overlay_text:
                        print(f"  overlay     : {' | '.join(meme.overlay_text[:2])}")
                    if meme.satire_type:
                        print(f"  satire      : {', '.join(meme.satire_type)}")
                    if meme.topics:
                        print(f"  topics      : {', '.join(meme.topics)}")
                    if meme.text_language != "unknown":
                        print(f"  meme_lang   : {meme.text_language}")
                print(f"  confidence  : {v.confidence:.2f}")
                if v.acts:
                    print(f"  acts        : {', '.join(v.acts)}")
                print(f"  reason      : {v.reason}")
                if v.latency_ms is not None:
                    print(f"  latency_ms  : {v.latency_ms:.0f}")
                if v.cache_hit:
                    print("  cache_hit   : true")
                if v.media_type == "video":
                    print(
                        f"  frames      : {v.frames_analyzed}/{v.frame_count} analyzed"
                        f" ({v.prescreen_skipped} prescreen-skip)"
                    )

    if args.json:
        out = results[0] if len(results) == 1 else results
        print(json.dumps(out, indent=2, ensure_ascii=False))

    if args.metrics:
        print(json.dumps(detector.metrics.snapshot(), indent=2))

    blocked = any(r["action"] == "block" for r in results)
    review = any(r["action"] == "review" for r in results)
    return 2 if blocked else (1 if review else 0)


if __name__ == "__main__":
    raise SystemExit(main())
