#!/usr/bin/env python3
"""Backup SQLite WAL + staging directory for SATRIA lab data.

Usage:
  cd backend && python scripts/backup_lab_data.py
  cd backend && python scripts/backup_lab_data.py --dest /var/backups/satria

Cron example (daily 02:00):
  0 2 * * * cd /opt/satria/backend && .venv/bin/python scripts/backup_lab_data.py --dest /var/backups/satria >> /var/log/satria-backup.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402


def backup_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup SATRIA lab DB + staging")
    parser.add_argument(
        "--dest",
        type=Path,
        default=settings.data_dir.parent / "backups",
        help="Root backup directory (timestamped subfolder created)",
    )
    parser.add_argument("--keep", type=int, default=14, help="Retain newest N backup folders")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = args.dest / f"satria_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    db_src = settings.db_path
    db_dst = out_dir / db_src.name
    backup_sqlite(db_src, db_dst)

    staging_src = settings.staging_dir
    staging_dst = out_dir / "staging"
    if staging_src.exists():
        shutil.copytree(staging_src, staging_dst, dirs_exist_ok=True)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_src),
        "staging_path": str(staging_src),
        "files": [p.name for p in out_dir.iterdir()],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Prune old backups
    if args.keep > 0 and args.dest.exists():
        backups = sorted(
            [p for p in args.dest.iterdir() if p.is_dir() and p.name.startswith("satria_")],
            key=lambda p: p.name,
            reverse=True,
        )
        for old in backups[args.keep :]:
            shutil.rmtree(old, ignore_errors=True)

    print(f"backup_ok path={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
