#!/usr/bin/env python3
"""Backup SQLite WAL + staging directory for SATRIA lab data.

Usage:
  cd backend && python scripts/backup_lab_data.py
  cd backend && python scripts/backup_lab_data.py --dest /var/backups/satria
  cd backend && python scripts/backup_lab_data.py --restore /var/backups/satria/satria_YYYYMMDD_HHMMSS

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
    parser.add_argument(
        "--restore",
        type=Path,
        default=None,
        help="Restore from a timestamped backup folder created by this script",
    )
    args = parser.parse_args()

    if args.restore:
        src = args.restore
        if not src.is_dir():
            print(f"restore_fail not_a_directory={src}", file=sys.stderr)
            return 1
        db_src = src / settings.db_path.name
        if not db_src.exists():
            matches = list(src.glob("*.db"))
            db_src = matches[0] if matches else db_src
        if not db_src.exists():
            print(f"restore_fail missing_db={src}", file=sys.stderr)
            return 1
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_src, settings.db_path)
        staging_src = src / "staging"
        if staging_src.exists():
            if settings.staging_dir.exists():
                shutil.rmtree(settings.staging_dir)
            shutil.copytree(staging_src, settings.staging_dir)
        print(f"restore_ok db={settings.db_path} staging={settings.staging_dir}")
        return 0

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
