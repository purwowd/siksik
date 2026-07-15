#!/usr/bin/env python3
"""Terapkan preset env ke backend/.env.

Contoh:
  python scripts/apply_env_preset.py mac.lab
  python scripts/apply_env_preset.py gpu.8gb
  python scripts/apply_env_preset.py gpu.demo-fast --list
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / "env"
TARGET = ROOT / ".env"


def list_presets() -> list[str]:
    return sorted(p.stem for p in ENV_DIR.glob("*.env"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SADT env preset → backend/.env")
    parser.add_argument(
        "preset",
        nargs="?",
        help="Nama file tanpa ekstensi, mis. gpu.8gb / mac.lab / gpu.demo-fast",
    )
    parser.add_argument("--list", action="store_true", help="Tampilkan preset tersedia")
    args = parser.parse_args()

    presets = list_presets()
    if args.list or not args.preset:
        print("Preset tersedia:")
        for name in presets:
            print(f"  {name}")
        if not args.preset:
            print("\nPakai: python scripts/apply_env_preset.py gpu.8gb")
            return 0 if args.list else 1

    name = args.preset.removesuffix(".env")
    src = ENV_DIR / f"{name}.env"
    if not src.is_file():
        print(f"Preset tidak ditemukan: {name}", file=sys.stderr)
        print("Tersedia:", ", ".join(presets), file=sys.stderr)
        return 1

    if TARGET.exists():
        bak = TARGET.parent / ".env.bak"
        shutil.copy2(TARGET, bak)
        print(f"Backup: {bak.name}")

    shutil.copy2(src, TARGET)
    print(f"OK → {TARGET.relative_to(ROOT)} (dari env/{name}.env)")
    print("Restart API. Setelah ganti OCR/Whisper: POST /admin/clear-hash-cache")
    if name.startswith("gpu"):
        print("Jalankan: python run.py --reload --host 127.0.0.1 --port 8000 --gpu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
