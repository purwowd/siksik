#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.acquisition.social_ocr import (
    _host_ocr_backend,
    _metric_from_regions,
    _metric_from_text,
    _username_from_regions,
    _username_from_ocr,
    run_social_snapshot_ocr,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    image = args.image.expanduser().resolve()
    if not image.is_file():
        raise SystemExit("snapshot does not exist")
    backend = _host_ocr_backend()
    if backend is None:
        raise SystemExit("configured host OCR backend is unavailable")
    result = run_social_snapshot_ocr(image, backend)
    if result is None:
        raise SystemExit("host OCR failed")
    regions = [
        {
            "text": value.text,
            "left": value.left,
            "top": value.top,
            "right": value.right,
            "bottom": value.bottom,
            "confidence": value.confidence,
        }
        for value in result.regions
    ]
    print(
        json.dumps(
            {
                "backend": result.backend,
                "confidence": result.confidence,
                "username": _username_from_regions(regions) or _username_from_ocr(result.text),
                "metrics": {
                    name: (
                        spatial
                        if (spatial := _metric_from_regions(regions, name)) is not None
                        else _metric_from_text(result.text, name)
                    )
                    for name in ("posts", "followers", "following")
                },
                "text": result.text,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
