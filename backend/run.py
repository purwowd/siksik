#!/usr/bin/env python3
"""SADT API runner — uvicorn wrapper dengan flag --gpu (full moderation stack).

Contoh:
  python run.py --reload --host 127.0.0.1 --port 8000
  python run.py --reload --host 127.0.0.1 --port 8000 --gpu
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    argv = sys.argv[1:]
    gpu = False
    cleaned: list[str] = []
    for arg in argv:
        if arg == "--gpu":
            gpu = True
            continue
        cleaned.append(arg)

    if gpu:
        os.environ["SADT_GPU_STACK_ENABLED"] = "1"
        os.environ["SADT_OCR_ENABLED"] = "1"
        os.environ["SADT_OCR_GPU"] = "1"
        os.environ.setdefault("SADT_OCR_BACKEND", "paddleocr")
        os.environ.setdefault("SADT_GPU_OCR_BACKEND", "paddleocr")
        os.environ.setdefault("SADT_GPU_WHISPER_ENABLED", "1")
        os.environ.setdefault("SADT_GPU_SAFEWATCH_ENABLED", "1")
        os.environ.setdefault("SADT_GPU_ICM_ENABLED", "1")
        os.environ.setdefault("SADT_GPU_QWEN_ENABLED", "1")
        print("SADT · GPU STACK ON")
        print("  Video  SafeWatch   | Image ICM-Assistant | Reason Qwen2.5-VL")
        print("  Audio  Whisper     | OCR PaddleOCR")
        print("  (weights opsional via SADT_GPU_*_MODEL — lihat README)")

    if not cleaned or cleaned[0].startswith("-"):
        cleaned = ["app.main:app", *cleaned]

    sys.argv = ["uvicorn", *cleaned]
    from uvicorn.main import main as uvicorn_main

    uvicorn_main()


if __name__ == "__main__":
    main()
