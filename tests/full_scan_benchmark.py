from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


REQUIRED_SOURCES = (
    "media_store_image",
    "shared_storage_document",
    "sms_content_provider",
    "accessibility_visible_ui",
)
ACCEPTED_SOURCE_STATES = {"complete", "partial"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and summarize one completed SIKSIK full-scan session.",
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--baseline-ms", type=float)
    parser.add_argument("--require-faster", action="store_true")
    parser.add_argument("--min-images", type=int, default=0)
    parser.add_argument("--min-pdfs", type=int, default=0)
    parser.add_argument("--min-sms", type=int, default=0)
    parser.add_argument("--min-instagram-posts", type=int, default=0)
    parser.add_argument("--min-instagram-stories", type=int, default=0)
    parser.add_argument("--min-instagram-comments", type=int, default=0)
    parser.add_argument("--min-x-posts", type=int, default=0)
    parser.add_argument("--min-x-replies", type=int, default=0)
    return parser.parse_args()


def open_database(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def scalar(connection: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> int:
    row = connection.execute(sql, values).fetchone()
    return int(row[0]) if row is not None else 0


def selected_counts(connection: sqlite3.Connection, session_id: str) -> dict[str, int]:
    return {
        "images": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'media_image'",
            (session_id,),
        ),
        "pdfs": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_artifacts WHERE session_id = ? "
            "AND source_kind = 'document' AND role = 'source_binary' "
            "AND mime_type = 'application/pdf'",
            (session_id,),
        ),
        "sms": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'sms'",
            (session_id,),
        ),
        "instagram_posts": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'visible_ui' AND source_app = 'com.instagram.android' "
            "AND social_scope = 'own_posts'",
            (session_id,),
        ),
        "instagram_stories": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'visible_ui' AND source_app = 'com.instagram.android' "
            "AND social_scope = 'own_story_archive'",
            (session_id,),
        ),
        "instagram_comments": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'visible_ui' AND source_app = 'com.instagram.android' "
            "AND social_scope = 'own_comments'",
            (session_id,),
        ),
        "x_posts": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'visible_ui' AND source_app = 'com.twitter.android' "
            "AND social_scope = 'own_tweets'",
            (session_id,),
        ),
        "x_replies": scalar(
            connection,
            "SELECT COUNT(*) FROM crawl_records WHERE session_id = ? "
            "AND source_kind = 'visible_ui' AND source_app = 'com.twitter.android' "
            "AND social_scope = 'own_replies'",
            (session_id,),
        ),
    }


def phase_timings(progress: dict[str, object], timing: dict[str, object]) -> dict[str, float]:
    keys = (
        "android_inventory_ms",
        "android_preprocessing_ms",
        "android_selection_ms",
        "android_transfer_ms",
        "android_acquisition_ms",
    )
    output = {
        key: float(progress[key])
        for key in keys
        if isinstance(progress.get(key), (int, float))
    }
    for key in ("t_detect_ms", "t_acquire_ms", "t_index_ms", "t_analyze_ms", "t_total_ms"):
        if isinstance(timing.get(key), (int, float)):
            output[key] = float(timing[key])
    return output


def main() -> int:
    args = arguments()
    if any(
        value < 0
        for value in (
            args.min_images,
            args.min_pdfs,
            args.min_sms,
            args.min_instagram_posts,
            args.min_instagram_stories,
            args.min_instagram_comments,
            args.min_x_posts,
            args.min_x_replies,
        )
    ):
        raise ValueError("minimum counts must be non-negative")
    if args.baseline_ms is not None and args.baseline_ms <= 0:
        raise ValueError("baseline must be positive")
    if args.require_faster and args.baseline_ms is None:
        raise ValueError("--require-faster requires --baseline-ms")

    with open_database(args.db) as connection:
        session = connection.execute(
            "SELECT mode, status, review_candidates, progress_json, timing_json "
            "FROM sessions WHERE id = ?",
            (args.session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("session was not found")
        progress = json.loads(session["progress_json"])
        timing = json.loads(session["timing_json"])
        counts = selected_counts(connection, args.session_id)

    source_progress = progress.get("crawl_source_progress")
    if not isinstance(source_progress, dict):
        source_progress = {}
    source_states = {
        source: value.get("state") if isinstance(value, dict) else None
        for source, value in source_progress.items()
    }
    timings = phase_timings(progress, timing)
    failures: list[str] = []
    if session["mode"] != "full":
        failures.append("session_mode_not_full")
    if session["status"] != "completed":
        failures.append("session_not_completed")
    if args.require_faster and bool(session["review_candidates"]):
        failures.append("human_review_wait_not_comparable")
    for source in REQUIRED_SOURCES:
        if source_states.get(source) not in ACCEPTED_SOURCE_STATES:
            failures.append(f"source_not_scanned:{source}:{source_states.get(source)}")
    expected = {
        "images": args.min_images,
        "pdfs": args.min_pdfs,
        "sms": args.min_sms,
        "instagram_posts": args.min_instagram_posts,
        "instagram_stories": args.min_instagram_stories,
        "instagram_comments": args.min_instagram_comments,
        "x_posts": args.min_x_posts,
        "x_replies": args.min_x_replies,
    }
    for kind, minimum in expected.items():
        if counts[kind] < minimum:
            failures.append(f"selected_count_below_minimum:{kind}:{counts[kind]}:{minimum}")
    total_ms = timings.get("t_total_ms")
    speedup = None
    if args.baseline_ms is not None and total_ms is not None and total_ms > 0:
        speedup = args.baseline_ms / total_ms
        if args.require_faster and speedup <= 1.0:
            failures.append("baseline_not_beaten")
    elif args.require_faster:
        failures.append("session_total_timing_missing")

    result = {
        "session_id": args.session_id,
        "mode": session["mode"],
        "status": session["status"],
        "review_candidates": bool(session["review_candidates"]),
        "source_states": {source: source_states.get(source) for source in REQUIRED_SOURCES},
        "selected_counts": counts,
        "phase_timings_ms": timings,
        "baseline_ms": args.baseline_ms,
        "speedup": round(speedup, 3) if speedup is not None else None,
        "passed": not failures,
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as error:
        print(f"full_scan_benchmark_error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
