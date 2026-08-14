#!/usr/bin/env python3
"""Run a privacy-safe Android recovery smoke check from SIKSIK code only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.android_recovery.gateway import RecoveryAdbGateway
from app.acquisition.android_recovery.service import (
    AndroidRecoveryService,
    cleanup_recovery_staging,
    load_valid_manifest,
)
from app.acquisition.errors import AcquisitionError
from app.core.config import settings
from app.models.schemas import AcquisitionMode


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate native SIKSIK Android recovery without displaying media.",
    )
    parser.add_argument(
        "--mode",
        choices=("quick", "full", "both"),
        default="both",
    )
    return parser.parse_args()


def _smoke_root() -> Path:
    data_root = settings.data_dir.expanduser().resolve()
    root = (data_root / "tmp" / "android-recovery-smoke").resolve()
    if not root.is_relative_to(data_root) or root == data_root:
        raise RuntimeError("smoke staging is outside the configured SIKSIK data directory")
    return root


def _manifest_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _reset_staging(staging: Path) -> None:
    cleanup_recovery_staging(staging)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def _run() -> int:
    requested = _arguments().mode
    modes = (
        (AcquisitionMode.QUICK, AcquisitionMode.FULL)
        if requested == "both"
        else (AcquisitionMode(requested),)
    )
    transport = AsyncAdbTransport(
        settings.adb_path,
        timeout_seconds=settings.adb_command_timeout_s,
        output_limit_bytes=settings.android_recovery_output_limit_bytes,
    )
    device = await transport.select_device(None)
    gateway = RecoveryAdbGateway(
        transport,
        output_limit_bytes=settings.android_recovery_output_limit_bytes,
    )
    service = AndroidRecoveryService(gateway)
    root = _smoke_root()
    results: list[dict[str, object]] = []
    exit_code = 0

    async def progress(*_args, **_fields) -> None:
        return None

    try:
        for mode in modes:
            staging = root / mode.value
            await asyncio.to_thread(_reset_staging, staging)
            try:
                run = await service.recover(
                    session_id=f"android-recovery-smoke-{mode.value}",
                    serial=device.serial,
                    mode=mode,
                    staging=staging,
                    on_progress=progress,
                    request_id=None,
                )
                manifest = await asyncio.to_thread(load_valid_manifest, staging)
                if manifest is None:
                    raise RuntimeError("recovery manifest verification failed")
                source_counts = Counter(item.source for item in manifest.artifacts)
                results.append(
                    {
                        "mode": mode.value,
                        "status": manifest.status,
                        "artifacts": run.item_count,
                        "bytes": manifest.stats.bytes_captured,
                        "candidates": manifest.stats.candidates_discovered,
                        "cache_sources_scanned": manifest.stats.cache_sources_scanned,
                        "cache_candidates_recovered": (
                            manifest.stats.cache_candidates_recovered
                        ),
                        "sources": dict(sorted(source_counts.items())),
                        "warning_count": len(manifest.warnings),
                        "manifest_sha256": await asyncio.to_thread(
                            _manifest_digest,
                            staging / "_android_recovery" / "manifest-v1.json",
                        ),
                    }
                )
            except AcquisitionError as exc:
                exit_code = 1
                results.append(
                    {
                        "mode": mode.value,
                        "status": "error",
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    }
                )
            finally:
                await asyncio.to_thread(_reset_staging, staging)
                await asyncio.to_thread(_remove_tree, staging)
    finally:
        await asyncio.to_thread(_remove_tree, root)

    print(
        json.dumps(
            {
                "device_state": device.state,
                "modes": results,
                "temporary_artifacts_cleaned": not root.exists(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return exit_code


def main() -> int:
    try:
        return asyncio.run(_run())
    except AcquisitionError as exc:
        payload = {
            "device_state": "unavailable",
            "error_category": exc.category.value,
            "retryable": exc.retryable,
        }
    except (OSError, RuntimeError, ValueError):
        payload = {
            "device_state": "error",
            "error_category": "smoke_validation_failed",
            "retryable": False,
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
