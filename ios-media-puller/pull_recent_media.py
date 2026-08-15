#!/usr/bin/env python3
"""
Pull recent photos and videos from a connected iOS device (AFC / DCIM).

Uses pymobiledevice3 over USB. Device must be unlocked and trusted.
"Recent" = files in DCIM sorted by modification time (newest first).

Authorization: Authorized security research / own-device lab use only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pull_recent_media")

PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng", ".tif", ".tiff", ".gif", ".webp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp"}
DCIM_ROOT = "/DCIM"


def _device_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass
class MediaFile:
    remote_path: str
    size: int
    mtime: datetime
    kind: str  # photo | video

    @property
    def name(self) -> str:
        return Path(self.remote_path).name


def _classify(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    return datetime.fromtimestamp(float(value)).astimezone()


async def _walk_dcim(afc) -> list[MediaFile]:
    found: list[MediaFile] = []

    if not await afc.exists(DCIM_ROOT):
        logger.error("Folder %s tidak ada di device", DCIM_ROOT)
        return found

    entries = await afc.listdir(DCIM_ROOT)
    folders: list[str] = []
    for name in entries:
        if name in (".", ".."):
            continue
        remote = f"{DCIM_ROOT}/{name}"
        if await afc.isdir(remote):
            # Skip Apple internal helpers; keep *APPLE camera rolls
            if name.startswith(".") and "APPLE" not in name.upper():
                continue
            folders.append(remote)

    if not folders:
        folders = [DCIM_ROOT]

    for folder in folders:
        try:
            names = await afc.listdir(folder)
        except Exception as exc:
            logger.warning("Skip scope DCIM: %s", type(exc).__name__)
            continue

        for name in names:
            if name in (".", "..") or name.startswith("."):
                continue
            remote = f"{folder}/{name}"
            kind = _classify(name)
            if kind is None:
                continue
            if await afc.isdir(remote):
                continue
            try:
                info = await afc.stat(remote)
            except Exception:
                continue
            mtime = _to_datetime(info["st_mtime"])
            size = int(info.get("st_size") or 0)
            found.append(MediaFile(remote_path=remote, size=size, mtime=mtime, kind=kind))

    return found


def _filter_media(
    items: list[MediaFile],
    media_type: str,
    count: int,
    since: datetime | None,
) -> list[MediaFile]:
    filtered = items
    if media_type == "photo":
        filtered = [m for m in filtered if m.kind == "photo"]
    elif media_type == "video":
        filtered = [m for m in filtered if m.kind == "video"]

    if since is not None:
        # compare naive-local vs aware carefully
        since_cmp = since if since.tzinfo else since.astimezone()
        filtered = [
            m
            for m in filtered
            if (m.mtime if m.mtime.tzinfo else m.mtime.astimezone()) >= since_cmp
        ]

    filtered.sort(key=lambda m: m.mtime, reverse=True)
    if count <= 0:
        return filtered
    return filtered[:count]


async def _pull(afc, items: list[MediaFile], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, item in enumerate(items, 1):
        stamp = item.mtime.strftime("%Y%m%d_%H%M%S")
        local_name = f"{stamp}_{item.name}"
        local_path = out_dir / local_name
        if local_path.exists():
            local_path = out_dir / f"{stamp}_{i:03d}_{item.name}"

        size_mb = item.size / (1024 * 1024)
        logger.info(
            "[%d/%d] %s  %.1f MB",
            i,
            len(items),
            item.kind.upper(),
            size_mb,
        )
        try:
            await afc.pull(item.remote_path, str(local_path), progress_bar=False)
            ok += 1
        except Exception as exc:
            logger.error("Gagal download item %d: %s", i, type(exc).__name__)
    return ok


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Tarik foto/video terbaru dari iOS (DCIM via AFC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  .venv/bin/python pull_recent_media.py
  .venv/bin/python pull_recent_media.py -n 50
  .venv/bin/python pull_recent_media.py -n 30 --days 7
  .venv/bin/python pull_recent_media.py --type photo -n 20 -o ./downloads
  .venv/bin/python pull_recent_media.py --type video -n 10
""",
    )
    p.add_argument(
        "-n",
        "--count",
        type=int,
        default=20,
        help="Jumlah file terbaru (default: 20; 0 = semua)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Hanya media dalam N hari terakhir (opsional)",
    )
    p.add_argument(
        "--not-before-epoch-s",
        type=float,
        default=None,
        help="Cutoff waktu absolut Unix epoch (dipakai flow SIKSIK).",
    )
    p.add_argument(
        "--type",
        choices=("all", "photo", "video"),
        default="all",
        help="Filter jenis media (default: all)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Folder output (default: ./output/media_YYYYMMDD_HHMMSS)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService
    except ImportError:
        logger.error("pymobiledevice3 belum terpasang.")
        logger.error("Jalankan: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        return 2

    if args.count < 0:
        logger.error("--count harus >= 0 (0 = semua)")
        return 2

    since = None
    if args.days is not None:
        if args.days < 1:
            logger.error("--days harus >= 1")
            return 2
        since = datetime.now().astimezone() - timedelta(days=args.days)
        logger.info("Filter: sejak %s", since.strftime("%Y-%m-%d %H:%M"))
    if args.not_before_epoch_s is not None:
        if args.not_before_epoch_s < 946_684_800:
            logger.error("--not-before-epoch-s tidak valid")
            return 2
        since = datetime.fromtimestamp(args.not_before_epoch_s).astimezone()
        logger.info("Filter cutoff: sejak %s", since.strftime("%Y-%m-%d %H:%M"))

    out_dir = args.output
    if out_dir is None:
        out_dir = Path("output") / f"media_{time.strftime('%Y%m%d_%H%M%S')}"

    t0 = time.time()
    try:
        lockdown = await create_using_usbmux(serial=os.environ.get("UDID") or None)
    except Exception as exc:
        logger.error("Tidak bisa konek ke device: %s", type(exc).__name__)
        logger.error("Pastikan: USB terhubung, Trust OK, device unlocked.")
        return 1

    try:
        logger.info(
            "Device iOS tersambung | version %s | ref %s",
            lockdown.product_version,
            _device_ref(lockdown.udid),
        )

        async with AfcService(lockdown) as afc:
            logger.info("Scan DCIM untuk foto/video...")
            all_media = await _walk_dcim(afc)
            logger.info("Ditemukan %d file media di DCIM", len(all_media))

            selected = _filter_media(all_media, args.type, args.count, since)
            if not selected:
                logger.warning("Tidak ada media yang cocok dengan filter.")
                return 0

            logger.info("Download %d file terbaru", len(selected))
            ok = await _pull(afc, selected, out_dir)
    finally:
        try:
            await lockdown.close()
        except Exception as exc:
            logger.warning("Lockdown close gagal: %s", type(exc).__name__)

    elapsed = time.time() - t0
    logger.info("Selesai: %d/%d file | %.1fs", ok, len(selected), elapsed)
    return 0 if ok == len(selected) else 1


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
