from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.acquisition.ios_social import (
    PACKAGE_INSTAGRAM,
    PACKAGE_X,
    records_from_ig_output,
    records_from_x_output,
)


def _write_png(path: Path) -> None:
    # Minimal valid 1x1 PNG
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )


@pytest.mark.unit
def test_records_from_ig_output_maps_profile_and_archive(tmp_path: Path) -> None:
    out = tmp_path / "ig"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(
        json.dumps(
            {
                "username": "denirwan_08",
                "display_name": "Denirwan",
                "bio": "kapiten",
                "posts": 1,
                "followers": 1,
                "following": 5,
            }
        ),
        encoding="utf-8",
    )
    _write_png(out / "profile.png")
    _write_png(out / "archive_01.png")
    _write_png(out / "archive_02.png")

    records = records_from_ig_output(
        session_id="session_ios_001",
        crawl_id="ios_social_crawl_001",
        out_dir=out,
        artifacts_dir=arts,
    )
    assert len(records) == 3
    profile, _ = records[0]
    assert profile.source_app == PACKAGE_INSTAGRAM
    assert profile.metadata.social_scope == "own_profile"
    assert profile.metadata.profile_username == "denirwan_08"
    assert profile.metadata.profile_metrics is not None
    assert profile.metadata.profile_metrics.posts == 1
    assert profile.provenance.enumeration_method == "ios_webdriveragent"
    assert profile.provenance.source_adapter == "ios_wda_visible_ui"
    assert {item[0].metadata.social_scope for item in records[1:]} == {"own_story_archive"}


@pytest.mark.unit
def test_records_from_x_output_maps_profile_and_tweets(tmp_path: Path) -> None:
    out = tmp_path / "x"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(
        json.dumps(
            {
                "username": "tokofunfun",
                "display_name": "akun promosi",
                "bio": "order",
                "followers": 0,
                "following": 5,
            }
        ),
        encoding="utf-8",
    )
    _write_png(out / "profile.png")
    _write_png(out / "post_01.png")

    records = records_from_x_output(
        session_id="session_ios_002",
        crawl_id="ios_social_crawl_002",
        out_dir=out,
        artifacts_dir=arts,
    )
    assert len(records) == 2
    profile, _ = records[0]
    assert profile.source_app == PACKAGE_X
    assert profile.metadata.social_scope == "own_profile"
    assert profile.metadata.profile_username == "tokofunfun"
    tweet, shot = records[1]
    assert tweet.metadata.social_scope == "own_tweets"
    assert shot is not None and shot.is_file()
    assert tweet.attachment_ids == tweet.metadata.screenshot_ids
