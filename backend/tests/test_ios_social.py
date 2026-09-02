from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.acquisition.ios_social import (
    PACKAGE_FACEBOOK,
    PACKAGE_INSTAGRAM,
    PACKAGE_X,
    _wda_ready,
    build_wda_flow_job,
    ios_social_operator_target,
    records_from_fb_output,
    records_from_ig_output,
    records_from_x_output,
    stack_udid_matches,
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
def test_records_from_ig_output_maps_posts_and_comments(tmp_path: Path) -> None:
    out = tmp_path / "ig"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(
        json.dumps({"username": "denirwan_08", "posts": 1, "followers": 1, "following": 5}),
        encoding="utf-8",
    )
    _write_png(out / "profile.png")
    _write_png(out / "post_01.png")
    (out / "posts.jsonl").write_text(
        json.dumps({"index": 1, "screenshot": "post_01.png", "text": "grid photo caption"}) + "\n",
        encoding="utf-8",
    )
    (out / "comments.jsonl").write_text(
        json.dumps({"index": 1, "screenshot": "", "text": "nice shot"}) + "\n",
        encoding="utf-8",
    )
    records = records_from_ig_output(
        session_id="session_ios_001",
        crawl_id="ios_social_crawl_001",
        out_dir=out,
        artifacts_dir=arts,
    )
    scopes = [item[0].metadata.social_scope for item in records]
    assert "own_posts" in scopes
    assert "own_comments" in scopes
    post = next(item[0] for item in records if item[0].metadata.social_scope == "own_posts")
    assert "caption" in (post.normalized_text or "")


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


@pytest.mark.unit
def test_records_from_fb_output_maps_profile(tmp_path: Path) -> None:
    out = tmp_path / "fb"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(
        json.dumps(
            {
                "display_name": "Deni Irwan",
                "friends": 2,
                "posts": 2,
                "followers": 0,
            }
        ),
        encoding="utf-8",
    )
    _write_png(out / "profile.png")

    records = records_from_fb_output(
        session_id="session_ios_003",
        crawl_id="ios_social_crawl_003",
        out_dir=out,
        artifacts_dir=arts,
    )
    assert len(records) == 1
    profile, shot = records[0]
    assert profile.source_app == PACKAGE_FACEBOOK
    assert profile.metadata.social_scope == "own_profile"
    assert profile.metadata.profile_display_name == "Deni Irwan"
    assert profile.metadata.profile_metrics is not None
    assert profile.metadata.profile_metrics.friends == 2
    assert shot is not None and shot.is_file()
    assert "2 friends" in (profile.normalized_text or "")


@pytest.mark.unit
def test_records_from_x_prefers_parsed_tweet_items(tmp_path: Path) -> None:
    out = tmp_path / "x"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(json.dumps({"username": "tokofunfun"}), encoding="utf-8")
    _write_png(out / "profile.png")
    (out / "tweet.jsonl").write_text(
        json.dumps({"index": 1, "text": "X\nHeader chrome\nWho to follow"}) + "\n",
        encoding="utf-8",
    )
    (out / "tweet_items.jsonl").write_text(
        json.dumps({"index": 1, "text": "Gw lah young future astra tsb. 31 March 2026. 22 Views"})
        + "\n",
        encoding="utf-8",
    )
    records = records_from_x_output(
        session_id="session_ios_004",
        crawl_id="ios_social_crawl_004",
        out_dir=out,
        artifacts_dir=arts,
    )
    tweets = [item[0] for item in records if item[0].metadata.social_scope == "own_tweets"]
    assert len(tweets) == 1
    assert "young future" in (tweets[0].normalized_text or "")


@pytest.mark.unit
def test_records_from_fb_skips_composer_pages(tmp_path: Path) -> None:
    out = tmp_path / "fb"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(json.dumps({"display_name": "Deni Irwan"}), encoding="utf-8")
    _write_png(out / "profile.png")
    (out / "fb_post.jsonl").write_text(
        json.dumps({"index": 1, "text": "New post\nWhat's on your mind?"}) + "\n",
        encoding="utf-8",
    )
    records = records_from_fb_output(
        session_id="session_ios_005",
        crawl_id="ios_social_crawl_005",
        out_dir=out,
        artifacts_dir=arts,
    )
    assert [item[0].metadata.social_scope for item in records] == ["own_profile"]


@pytest.mark.unit
def test_records_from_fb_maps_comment_items(tmp_path: Path) -> None:
    out = tmp_path / "fb"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(json.dumps({"display_name": "Deni Irwan"}), encoding="utf-8")
    _write_png(out / "profile.png")
    (out / "fb_comment.jsonl").write_text(
        json.dumps({"index": 1, "text": "Menu\nSettings\nActivity log"}) + "\n",
        encoding="utf-8",
    )
    (out / "fb_comment_items.jsonl").write_text(
        json.dumps({"index": 1, "text": "nice photo"}) + "\n",
        encoding="utf-8",
    )
    records = records_from_fb_output(
        session_id="session_ios_006",
        crawl_id="ios_social_crawl_006",
        out_dir=out,
        artifacts_dir=arts,
    )
    comments = [item[0] for item in records if item[0].metadata.social_scope == "own_comments"]
    assert len(comments) == 1
    assert comments[0].normalized_text == "nice photo"


@pytest.mark.unit
def test_records_from_fb_skips_activity_log_hub_chrome(tmp_path: Path) -> None:
    out = tmp_path / "fb"
    arts = tmp_path / "artifacts"
    out.mkdir()
    arts.mkdir()
    (out / "profile.json").write_text(json.dumps({"display_name": "Deni Irwan"}), encoding="utf-8")
    _write_png(out / "profile.png")
    (out / "fb_comment_items.jsonl").write_text(
        json.dumps({"index": 1, "text": "PERSONALINFOGROUPING-SECTION-ITEM"})
        + "\n"
        + json.dumps({"index": 2, "text": "Personal information, Information about your profile"})
        + "\n",
        encoding="utf-8",
    )
    records = records_from_fb_output(
        session_id="session_ios_007",
        crawl_id="ios_social_crawl_007",
        out_dir=out,
        artifacts_dir=arts,
    )
    assert [item[0].metadata.social_scope for item in records] == ["own_profile"]


@pytest.mark.unit
def test_ios_social_operator_target_uses_ios_bundles() -> None:
    assert ios_social_operator_target(PACKAGE_INSTAGRAM) == (
        "Instagram (com.burbn.instagram)"
    )
    assert ios_social_operator_target(PACKAGE_X) == "X (com.atebits.Tweetie2)"
    assert ios_social_operator_target(PACKAGE_FACEBOOK) == (
        "Facebook (com.facebook.Facebook)"
    )
    assert "android" not in ios_social_operator_target(PACKAGE_INSTAGRAM)
    assert "android" not in ios_social_operator_target(PACKAGE_X)
    assert "katana" not in ios_social_operator_target(PACKAGE_FACEBOOK)


@pytest.mark.unit
def test_build_wda_flow_job_has_no_cli_flags(tmp_path: Path) -> None:
    job = build_wda_flow_job(
        flow="ig-profile",
        output_dir=tmp_path / "ig",
        wda_url="http://127.0.0.1:8100",
        udid="00008101-0008384601D8001E",
        archive_shots=2,
        x_shots=2,
    )
    dumped = json.dumps(job)
    assert "--" not in dumped
    assert job["flow"] == "ig-profile"
    assert job["archive_shots"] == 2
    assert job["stop_after"] == "all"


@pytest.mark.unit
def test_stack_udid_matches_same_and_swapped_iphone(tmp_path: Path) -> None:
    state = tmp_path / "stack"
    state.mkdir()
    (state / "udid").write_text("PHONE-A\n", encoding="utf-8")
    assert stack_udid_matches("PHONE-A", state_dir=state) is True
    assert stack_udid_matches("PHONE-B", state_dir=state) is False
    assert stack_udid_matches("PHONE-A", state_dir=tmp_path / "missing") is False


@pytest.mark.unit
def test_ensure_ios_wda_stack_reruns_when_iphone_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from app.acquisition import ios_social as mod

    puller = tmp_path / "puller"
    scripts = puller / "ios_automator" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_stack.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (puller / ".venv" / "bin").mkdir(parents=True)
    (puller / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (puller / "ios_automator" / "automator.py").write_text("", encoding="utf-8")

    ran = {"n": 0}

    async def fake_ready(_url: str, timeout_s: float = 3.0) -> bool:
        return True

    async def fake_run(*_args: object, **_kwargs: object) -> object:
        ran["n"] += 1
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(mod, "_wda_ready", fake_ready)
    monkeypatch.setattr(mod, "run_process", fake_run)
    monkeypatch.setattr(mod, "_STACK_STATE_DIR", tmp_path)
    monkeypatch.setattr(
        mod,
        "_puller_paths",
        lambda: (
            puller,
            puller / ".venv" / "bin" / "python",
            puller / "ios_automator" / "automator.py",
        ),
    )
    (tmp_path / "udid").write_text("PHONE-A\n", encoding="utf-8")

    asyncio.run(mod.ensure_ios_wda_stack(udid="PHONE-A"))
    assert ran["n"] == 0
    asyncio.run(mod.ensure_ios_wda_stack(udid="PHONE-B"))
    assert ran["n"] == 1


@pytest.mark.unit
def test_wda_ready_true_when_curl_status_200() -> None:
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b'{"value":{"ready":true}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert asyncio.run(_wda_ready(f"http://127.0.0.1:{port}", timeout_s=2.0)) is True
        assert asyncio.run(_wda_ready("http://127.0.0.1:1", timeout_s=1.0)) is False
    finally:
        server.shutdown()
        server.server_close()


def _ax_text():
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "ios-media-puller"
        / "ios_automator"
        / "lib"
        / "ax_text.py"
    )
    spec = importlib.util.spec_from_file_location("ios_ax_text", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_fb_comments_cluster_overlaps_tab_bar_on_dump() -> None:
    dump = (
        Path(__file__).resolve().parents[2]
        / "temp_crawl"
        / "ios_wda"
        / "38490b13-ec32-4e38-81dd-247cfe008b1a"
        / "fb-profile"
        / "page_source_after_comments_reactions.xml"
    )
    if not dump.is_file():
        pytest.skip("FB dump 38490b13 tidak ada")
    ax = _ax_text()
    xml = dump.read_text(encoding="utf-8")
    tab_top = ax.xml_tab_bar_top(xml)
    hit = ax.xml_find_control(xml, names=("COMMENTSCLUSTER",), include_hidden=True)
    assert hit is not None
    assert ax.hit_overlaps_tab_bar(hit, tab_top)


@pytest.mark.unit
def test_fb_groups_tab_dump_is_not_comments_surface() -> None:
    dump = (
        Path(__file__).resolve().parents[2]
        / "temp_crawl"
        / "ios_wda"
        / "38490b13-ec32-4e38-81dd-247cfe008b1a"
        / "fb-profile"
        / "page_source_comments.xml"
    )
    if not dump.is_file():
        pytest.skip("FB dump 38490b13 tidak ada")
    ax = _ax_text()
    xml = dump.read_text(encoding="utf-8")
    assert ax.looks_like_fb_groups_tab(xml)
    assert not ax.looks_like_fb_comments_surface(xml)
