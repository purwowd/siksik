#!/usr/bin/env python3
"""Hapus staging satu sesi dan tulis berita acara penghapusan.

Usage:
  cd backend && python scripts/wipe_session_staging.py --session-id UUID --actor admin
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Wipe SATRIA session staging + certificate")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--actor", default="admin", help="Nama petugas pada berita acara")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Folder berita acara (default: data/wipe-certificates)",
    )
    args = parser.parse_args()
    session_id = args.session_id.strip()
    if not session_id:
        print("wipe_fail empty_session_id", file=sys.stderr)
        return 1

    staging = (settings.staging_dir / session_id).resolve()
    staging_root = settings.staging_dir.resolve()
    if staging != staging_root and staging_root not in staging.parents:
        print(f"wipe_fail path_escape={staging}", file=sys.stderr)
        return 1

    file_count = 0
    bytes_removed = 0
    if staging.exists():
        for path in staging.rglob("*"):
            if path.is_file():
                file_count += 1
                bytes_removed += path.stat().st_size
        shutil.rmtree(staging)

    cert_dir = args.out or (settings.data_dir / "wipe-certificates")
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert = {
        "kind": "satria_session_wipe",
        "session_id": session_id,
        "actor": args.actor.strip() or "admin",
        "wiped_at": _utc(),
        "staging_path": str(staging),
        "files_removed": file_count,
        "bytes_removed": bytes_removed,
        "note": "Staging sesi dihapus. Rekaman DB sesi tidak dihapus otomatis.",
    }
    cert_path = cert_dir / f"wipe_{session_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    cert_path.write_text(json.dumps(cert, indent=2, ensure_ascii=False), encoding="utf-8")

    if settings.db_path.exists():
        conn = sqlite3.connect(settings.db_path)
        try:
            conn.execute(
                """
                INSERT INTO audit_events (id, session_id, actor, action, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    cert["actor"],
                    "session_wiped",
                    f"{file_count} berkas · {cert_path.name}",
                    _utc(),
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            print(f"wipe_warn audit_insert_failed={exc}", file=sys.stderr)
        finally:
            conn.close()

    print(f"wipe_ok session_id={session_id} files={file_count} cert={cert_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
