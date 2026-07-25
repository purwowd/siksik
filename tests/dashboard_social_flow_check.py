from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ALLOWED_SCOPES = {
    "own_profile",
    "own_posts",
    "own_tweets",
    "own_story_archive",
    "own_comments",
    "own_replies",
}
ACTIVE_SOCIAL_PACKAGES = {
    "com.instagram.android",
    "com.twitter.android",
    "com.facebook.katana",
}
SCOPES_BY_PACKAGE = {
    "com.instagram.android": {
        "own_profile",
        "own_posts",
        "own_story_archive",
        "own_comments",
    },
    "com.twitter.android": {"own_profile", "own_tweets", "own_replies"},
    "com.facebook.katana": {
        "own_profile",
        "own_posts",
        "own_comments",
        "own_story_archive",
    },
}
CANONICAL_MIME = "application/vnd.siksik.crawl-record+json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--expected-scope", action="append", default=[])
    parser.add_argument("--require-screenshot", action="store_true")
    parser.add_argument("--require-profile-data", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    if not args.db.is_file():
        raise SystemExit("database tidak ditemukan")
    expected = set(args.expected_scope)
    if not expected <= ALLOWED_SCOPES:
        raise SystemExit("expected scope tidak valid")
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        transfer = connection.execute(
            "SELECT state, record_count, artifact_count, receipt_id "
            "FROM crawl_transfers WHERE session_id = ?",
            (args.session_id,),
        ).fetchone()
        if transfer is None or transfer["state"] != "committed":
            raise SystemExit("transfer sesi belum committed")
        records = connection.execute(
            "SELECT record_id, source_app, social_scope, canonical_json FROM crawl_records "
            "WHERE session_id = ? AND source_kind = 'visible_ui'",
            (args.session_id,),
        ).fetchall()
        scopes = {row["social_scope"] for row in records}
        if None in scopes or not scopes <= ALLOWED_SCOPES:
            raise SystemExit("ditemukan scope sosial di luar boundary")
        if any(
            row["social_scope"] not in SCOPES_BY_PACKAGE.get(row["source_app"], set())
            for row in records
        ):
            raise SystemExit("scope sosial tidak cocok dengan aplikasi sumber")
        if any(row["source_app"] not in ACTIVE_SOCIAL_PACKAGES for row in records):
            raise SystemExit("sesi memuat target sosial di luar fokus Instagram/X")
        if expected and not expected <= scopes:
            raise SystemExit("scope sosial yang diharapkan belum teringest")
        social_notifications = connection.execute(
            "SELECT COUNT(*) AS total FROM crawl_records "
            "WHERE session_id = ? AND source_kind = 'notification' "
            "AND source_app IN (?, ?, ?)",
            (args.session_id, *SCOPES_BY_PACKAGE),
        ).fetchone()
        if (social_notifications["total"] or 0) != 0:
            raise SystemExit("notification aplikasi sosial melewati boundary account-owned")
        ledger = connection.execute(
            "SELECT COUNT(*) AS records FROM crawl_records WHERE session_id = ?",
            (args.session_id,),
        ).fetchone()
        artifacts = connection.execute(
            "SELECT COUNT(*) AS artifacts, "
            "SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) AS verified, "
            "SUM(CASE WHEN lower(relative_path) LIKE '%.zip' THEN 1 ELSE 0 END) AS archives "
            "FROM crawl_artifacts WHERE session_id = ?",
            (args.session_id,),
        ).fetchone()
        if ledger["records"] != transfer["record_count"]:
            raise SystemExit("jumlah record transfer tidak sama dengan ledger")
        if (
            artifacts["artifacts"] != transfer["artifact_count"]
            or (artifacts["verified"] or 0) != transfer["artifact_count"]
            or (artifacts["archives"] or 0) != 0
        ):
            raise SystemExit("ledger artifact transfer tidak valid")
        canonical_files = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN analyzed = 1 THEN 1 ELSE 0 END) AS analyzed "
            "FROM files WHERE session_id = ? AND mime = ?",
            (args.session_id, CANONICAL_MIME),
        ).fetchone()
        files = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN analyzed = 1 THEN 1 ELSE 0 END) AS analyzed "
            "FROM files WHERE session_id = ?",
            (args.session_id,),
        ).fetchone()
        screenshots = connection.execute(
            "SELECT record_id FROM crawl_artifacts "
            "WHERE session_id = ? AND source_kind = 'visible_ui' AND role = 'screenshot'",
            (args.session_id,),
        ).fetchall()
        screenshot_record_ids = {row["record_id"] for row in screenshots}
        if args.require_screenshot and not screenshot_record_ids:
            raise SystemExit("screenshot visible UI belum teringest")
        records_by_id = {row["record_id"]: row for row in records}
        profile_records = [row for row in records if row["social_scope"] == "own_profile"]
        if args.require_profile_data:
            if not profile_records:
                raise SystemExit("record profil akun belum teringest")
            for record in profile_records:
                try:
                    canonical_profile = json.loads(record["canonical_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SystemExit("record profil akun tidak valid") from exc
                metadata = canonical_profile.get("metadata") or {}
                if (
                    not isinstance(metadata, dict)
                    or not isinstance(metadata.get("profile_username"), str)
                    or not metadata["profile_username"].strip()
                    or "profile_display_name" not in metadata
                    or "profile_bio" not in metadata
                    or not isinstance(metadata.get("profile_links"), list)
                    or not isinstance(metadata.get("profile_metrics"), dict)
                    or set(metadata["profile_metrics"]) != {"posts", "followers", "following"}
                ):
                    raise SystemExit("username/bio/link/metrik profil belum terstruktur")
        if args.require_screenshot:
            required_visual_signals = {"perceptual_hash", "ocr", "face", "objects"}
            for record_id in screenshot_record_ids:
                record = records_by_id.get(record_id)
                if record is None:
                    raise SystemExit("screenshot tidak terikat ke record visible UI")
                try:
                    canonical_record = json.loads(record["canonical_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SystemExit("record canonical visible UI tidak valid") from exc
                if not isinstance(canonical_record, dict):
                    raise SystemExit("record canonical visible UI tidak valid")
                preprocessing = canonical_record.get("preprocessing") or {}
                if (
                    not isinstance(preprocessing, dict)
                    or not required_visual_signals <= preprocessing.keys()
                ):
                    raise SystemExit("sinyal visual Android tidak lengkap")
                if any(
                    not isinstance(preprocessing[key], dict)
                    or preprocessing[key].get("status") not in {"completed", "truncated"}
                    for key in required_visual_signals
                ):
                    raise SystemExit("preprocessing visual Android belum berhasil")
        if (
            (canonical_files["total"] or 0) != transfer["record_count"]
            or (canonical_files["analyzed"] or 0) != transfer["record_count"]
            or (files["total"] or 0) != transfer["artifact_count"]
            or (files["analyzed"] or 0) != transfer["artifact_count"]
        ):
            raise SystemExit("artifact belum seluruhnya masuk analisis SIKSIK")
        result = {
            "session_id": args.session_id,
            "transfer_state": transfer["state"],
            "record_count": transfer["record_count"],
            "artifact_count": transfer["artifact_count"],
            "visible_ui_records": len(records),
            "social_profile_records": len(profile_records),
            "visible_ui_screenshots": len(screenshots),
            "social_scopes": sorted(scopes),
            "canonical_files": canonical_files["total"] or 0,
            "canonical_files_analyzed": canonical_files["analyzed"] or 0,
            "files": files["total"] or 0,
            "files_analyzed": files["analyzed"] or 0,
        }
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
