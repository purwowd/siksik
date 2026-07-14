#!/usr/bin/env python3
"""Acceptance runner untuk deploy server GPU.

Contoh di server:
  cd backend
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt -r requirements-dev.txt
  # opsional: pip install torch --index-url https://download.pytorch.org/whl/cu124
  SADT_REQUIRE_GPU=1 python scripts/run_acceptance.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], env: dict | None = None) -> int:
    print("\n>>", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="SADT PoC acceptance suite")
    parser.add_argument("--perf", action="store_true", help="ikutkan benchmark 1k/5k file")
    parser.add_argument("--require-gpu", action="store_true", help="gagal jika CUDA tidak siap")
    parser.add_argument("--sla-ms", type=int, default=120_000, help="gate performa pipeline sintetis (ms)")
    args = parser.parse_args()

    env = os.environ.copy()
    env["SADT_PERF_SLA_MS"] = str(args.sla_ms)
    if args.require_gpu:
        env["SADT_REQUIRE_GPU"] = "1"

    # 1) unit + api + accuracy
    code = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "unit or api or acceptance",
            "-q",
            "--tb=short",
        ],
        env=env,
    )
    if code != 0:
        print("\nACCEPTANCE FAILED: unit/api/accuracy", file=sys.stderr)
        return code

    # 2) GPU report (and hard gate if required)
    code = run([sys.executable, "-m", "pytest", "-m", "gpu", "-q", "-s", "--tb=short"], env=env)
    if code != 0:
        print("\nACCEPTANCE FAILED: GPU gate", file=sys.stderr)
        return code

    # 3) optional perf
    if args.perf:
        code = run(
            [sys.executable, "-m", "pytest", "-m", "perf", "-q", "--tb=short"],
            env=env,
        )
        if code != 0:
            print("\nACCEPTANCE FAILED: performance", file=sys.stderr)
            return code

    print("\n✓ ACCEPTANCE PASSED — siap deploy/running di server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
