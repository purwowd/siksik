#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.acquisition import social_ocr
from app.acquisition.agent_client import VisibleUiMetadataV1
from app.core.config import settings
from app.core.db import Database
from app.services import reports


def main() -> None:
    sample = (
        "Inspo needed Q ltfzp.blog o lutfizp posts Edit profile "
        "471 followers 432 following Share profile"
    )
    if social_ocr._username_from_ocr(sample) != "lutfizp":
        raise SystemExit("Instagram OCR username extraction is not profile-scoped")
    if social_ocr._valid_username("ltfzp.blog") is not None:
        raise SystemExit("Instagram bio link was accepted as a username")
    if social_ocr.PROFILE_LINK.fullmatch("ltfzp.blog") is None:
        raise SystemExit("Instagram .blog bio link was not recognized")
    if social_ocr._metric_from_text(sample, "followers") != 471:
        raise SystemExit("Instagram follower OCR extraction failed")
    if social_ocr._metric_from_text(sample, "following") != 432:
        raise SystemExit("Instagram following OCR extraction failed")
    regions = [
        {"text": "0", "left": 340, "top": 270, "right": 365, "bottom": 310},
        {"text": "posts", "left": 335, "top": 330, "right": 440, "bottom": 370},
        {"text": "471", "left": 540, "top": 270, "right": 610, "bottom": 310},
        {"text": "followers", "left": 535, "top": 330, "right": 705, "bottom": 370},
        {"text": "432", "left": 805, "top": 270, "right": 885, "bottom": 310},
        {"text": "following", "left": 800, "top": 330, "right": 970, "bottom": 370},
    ]
    if social_ocr._metric_from_regions(regions, "posts") != 0:
        raise SystemExit("Instagram post count spatial extraction failed")
    if social_ocr._metric_from_regions(regions, "followers") != 471:
        raise SystemExit("Instagram follower spatial extraction failed")
    if social_ocr._metric_from_regions(regions, "following") != 432:
        raise SystemExit("Instagram following spatial extraction failed")

    node_metadata = VisibleUiMetadataV1.model_validate(
        {
            "package_name": "com.instagram.android",
            "social_scope": "own_profile",
            "window_id": -1,
            "event_type": 2048,
            "screen_sequence": 1,
            "nodes": [
                _node(0, "lutfizp", "action_bar_title", 40, 100),
                _node(1, "0", "profile_header_familiar_post_count_value", 270, 310),
                _node(2, "471", "profile_header_familiar_followers_value", 270, 310),
                _node(3, "432", "profile_header_familiar_following_value", 270, 310),
                _node(4, "ltfzp.blog", "profile_header_link", 490, 540),
            ],
            "screenshot_ids": ["shot_12345678"],
            "profile_links": ["ltfzp.blog"],
            "profile_metrics": {"posts": None, "followers": None, "following": None},
            "warning_codes": [],
        }
    )
    enriched = social_ocr._profile_metadata(node_metadata, sample, regions)
    if enriched["profile_username"] != "lutfizp":
        raise SystemExit("resource-backed Instagram username extraction failed")
    if enriched["profile_metrics"] != {"posts": 0, "followers": 471, "following": 432}:
        raise SystemExit("resource-backed Instagram profile metrics extraction failed")
    if "ltfzp.blog" not in enriched["profile_links"]:
        raise SystemExit("resource-backed Instagram bio link extraction failed")

    metadata = {
        "profile_metrics": {"posts": None, "followers": None, "following": None}
    }
    lines = ["lutfizp", "posts", "471", "followers", "432", "following"]
    metrics = reports._profile_metrics(metadata, lines)
    if metrics["followers"] != 471 or metrics["following"] != 432:
        raise SystemExit("report metadata fallback does not use OCR counts")

    record = {
        "preprocessing": {
            "ocr": {
                "regions": [
                    {"text": "lutfizp", "top": 44, "left": 441},
                    {"text": "Inspo", "top": 197, "left": 133},
                ]
            }
        }
    }
    if reports._profile_username_from_ocr_regions(record) != "lutfizp":
        raise SystemExit("spatial OCR username fallback selected Instagram Notes")

    with tempfile.TemporaryDirectory() as raw_temp:
        temp = Path(raw_temp)
        source = temp / "source.png"
        source.write_bytes(b"png-fixture")
        previous_root = settings.android_social_debug_dir
        previous_enabled = settings.android_social_debug_snapshots
        try:
            settings.android_social_debug_dir = temp / "temp_crawl"
            settings.android_social_debug_snapshots = True
            mirrored = social_ocr._mirror_debug_snapshot(
                source,
                session_id="session_12345678",
                crawl_id="crawl_12345678",
                social_scope="own_profile",
                record_id="record_12345678",
                artifact_id="artifact_12345678",
                index=1,
            )
            if mirrored is None or mirrored.read_bytes() != source.read_bytes():
                raise SystemExit("Instagram debug snapshot mirror failed")
        finally:
            settings.android_social_debug_dir = previous_root
            settings.android_social_debug_snapshots = previous_enabled
        asyncio.run(check_database_migration(temp / "contract.db"))

    preprocessor = (
        ROOT
        / "android-agent/app/src/main/java/com/siksik/agent/preprocessing/RecordPreprocessor.kt"
    ).read_text(encoding="utf-8")
    if "runOcr = false" not in preprocessor or "ocr_host_deferred" not in preprocessor:
        raise SystemExit("Android visible-UI OCR was not deferred to the backend")

    print("social host OCR contract: ok")


def _node(sequence: int, text: str, resource: str, top: int, bottom: int) -> dict:
    return {
        "sequence": sequence,
        "depth": 0,
        "text": text,
        "content_description": None,
        "class_name": "android.widget.TextView",
        "view_id": f"com.instagram.android:id/{resource}",
        "bounds": {"left": 0, "top": top, "right": 200, "bottom": bottom},
        "clickable": False,
        "scrollable": False,
    }


async def check_database_migration(path: Path) -> None:
    database = Database(path)
    await database.connect()
    try:
        row = await database.conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'social_snapshot_enrichments'"
        )
        if await row.fetchone() is None:
            raise SystemExit("social snapshot enrichment migration is missing")
    finally:
        await database.close()


if __name__ == "__main__":
    main()
