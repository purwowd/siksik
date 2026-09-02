#!/usr/bin/env python3
"""
Pull shared documents (PDF/Office/text) from an iOS device over AFC.

Best-effort allowlisted roots only (Downloads, Books, Documents). Bounded count.
Device must be unlocked and trusted.

Authorization: Authorized security research / own-device lab use only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pull_shared_docs")

DOC_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".pages",
    ".numbers",
    ".key",
}
ROOTS = (
    "/Downloads",
    "/Books",
    "/Documents",
    "/File Provider Storage",
)
MAX_DEPTH = 3


@dataclass
class DocFile:
    remote_path: str
    size: int
    mtime: datetime

    @property
    def name(self) -> str:
        return Path(self.remote_path).name


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    return datetime.fromtimestamp(float(value)).astimezone()


async def _walk(afc, root: str, depth: int, found: list[DocFile]) -> None:
    if depth > MAX_DEPTH:
        return
    try:
        if not await afc.exists(root):
            return
        names = await afc.listdir(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skip %s: %s", root, exc)
        return

    for name in names:
        if name in (".", "..") or name.startswith("."):
            continue
        remote = f"{root.rstrip('/')}/{name}"
        try:
            is_dir = await afc.isdir(remote)
        except Exception:  # noqa: BLE001
            continue
        if is_dir:
            await _walk(afc, remote, depth + 1, found)
            continue
        if Path(name).suffix.lower() not in DOC_EXTS:
            continue
        try:
            info = await afc.stat(remote)
        except Exception:  # noqa: BLE001
            continue
        found.append(
            DocFile(
                remote_path=remote,
                size=int(info.get("st_size") or 0),
                mtime=_to_datetime(info["st_mtime"]),
            )
        )


async def _pull(afc, items: list[DocFile], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, item in enumerate(items, 1):
        stamp = item.mtime.strftime("%Y%m%d_%H%M%S")
        local_path = out_dir / f"{stamp}_{item.name}"
        if local_path.exists():
            local_path = out_dir / f"{stamp}_{i:03d}_{item.name}"
        logger.info("[%d/%d] %s (%.1f MB)", i, len(items), item.name, item.size / (1024 * 1024))
        try:
            await afc.pull(item.remote_path, str(local_path), progress_bar=False)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Gagal download %s: %s", item.remote_path, exc)
    return ok


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tarik dokumen bersama dari iOS (AFC).")
    p.add_argument(
        "-n",
        "--count",
        type=int,
        default=30,
        help="Maks file (default 30; 0 = semua)",
    )
    p.add_argument("-o", "--output", type=Path, required=True, help="Folder output lokal")
    p.add_argument("--not-before-epoch-s", type=float, default=0.0)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService
    except ImportError:
        logger.error("pymobiledevice3 belum terpasang di venv ios-media-puller.")
        return 2

    from wsl_usbmux import lockdown_usbmux_kwargs

    if args.count < 0:
        return 2

    lockdown = await create_using_usbmux(
        serial=os.environ.get("UDID") or None,
        **lockdown_usbmux_kwargs(),
    )
    found: list[DocFile] = []
    async with AfcService(lockdown) as afc:
        for root in ROOTS:
            await _walk(afc, root, 0, found)
        if args.not_before_epoch_s > 0:
            found = [item for item in found if item.mtime.timestamp() >= args.not_before_epoch_s]
        found.sort(key=lambda d: d.mtime, reverse=True)
        selected = found if args.count == 0 else found[: args.count]
        logger.info("Kandidat dokumen: %d → ambil %d", len(found), len(selected))
        if not selected:
            return 0
        return 0 if await _pull(afc, selected, args.output) >= 0 else 1


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
