from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.acquisition.agent_client import InventoryRecordV1
from app.acquisition.direct_transfer import CANONICAL_RECORD_MIME
from app.services.analysis import analyze_content, read_preview

SESSION_ID = "session-direct-transfer-001"
CRAWL_ID = "crawl-direct-transfer-001"
TIMESTAMP = "2026-07-17T10:00:00Z"


def visible_record(normalized_text: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_id": "record-visible-ui-001",
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_kind": "visible_ui",
        "source_app": "com.instagram.android",
        "source_locator": "visible_ui:opaque",
        "observed_at": TIMESTAMP,
        "source_created_at": TIMESTAMP,
        "source_modified_at": None,
        "normalized_text": normalized_text,
        "metadata": {
            "package_name": "com.instagram.android",
            "social_scope": "own_posts",
            "window_id": -1,
            "activity_context": "FixtureActivity",
            "event_type": 2048,
            "screen_sequence": 1,
            "nodes": [],
            "screenshot_ids": [],
            "warning_codes": ["metadata_narkoba_must_not_be_analyzed"],
        },
        "attachment_ids": [],
        "content_sha256": "a" * 64,
        "preprocessing": None,
        "selection": {
            "policy_version": "siksik-selection-v1",
            "policy_fingerprint": "c" * 64,
            "revision": 1,
            "selection_fingerprint": "b" * 64,
            "score": 0.9,
            "threshold": 0.5,
            "auto_selected": True,
            "selected": True,
            "matched_keywords": [],
            "matched_rules": [],
            "model_signals": [],
            "reasons": [],
            "human_override": "none",
            "operator_id": None,
            "decided_at": TIMESTAMP,
        },
        "provenance": {
            "source_adapter": "accessibility_visible_ui",
            "enumeration_method": "android_uiautomator",
            "agent_version": "0.7.0",
            "original_staged": False,
        },
    }


@pytest.mark.unit
def test_visible_ui_requires_an_owned_account_scope() -> None:
    payload = visible_record("fixture")
    record = InventoryRecordV1.model_validate(payload)
    assert record.metadata.social_scope == "own_posts"

    payload["metadata"]["social_scope"] = "home_feed"
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)

    payload = visible_record("fixture")
    payload["metadata"]["social_scope"] = "own_tweets"
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)

    payload = visible_record("fixture")
    payload["metadata"]["screenshot_ids"] = ["shot_fixture"]
    with pytest.raises(ValueError):
        InventoryRecordV1.model_validate(payload)


@pytest.mark.unit
async def test_canonical_analyzer_reads_only_normalized_text(tmp_path: Path) -> None:
    path = tmp_path / "record-visible-ui-001.siksik-record.json"
    path.write_text(json.dumps(visible_record("konten aman")), encoding="utf-8")

    preview = await read_preview(path, CANONICAL_RECORD_MIME)
    findings = analyze_content(
        path,
        CANONICAL_RECORD_MIME,
        "visible_ui",
        preview,
        ["narkoba"],
    )

    assert preview == "konten aman"
    assert findings == []


@pytest.mark.unit
async def test_canonical_social_text_uses_existing_siksik_lexicon(tmp_path: Path) -> None:
    path = tmp_path / "record-visible-ui-001.siksik-record.json"
    path.write_text(json.dumps(visible_record("postingan narkoba")), encoding="utf-8")

    preview = await read_preview(path, CANONICAL_RECORD_MIME)
    findings = analyze_content(
        path,
        CANONICAL_RECORD_MIME,
        "visible_ui",
        preview,
        ["narkoba"],
    )

    assert findings
    assert all(item["evidence"] in preview for item in findings)


@pytest.mark.unit
def test_android_transfer_source_does_not_create_archive_output() -> None:
    roots = [
        Path(__file__).parents[2] / "android-agent" / "app" / "src" / "main",
        Path(__file__).parents[1] / "app" / "acquisition",
    ]
    forbidden = ("ZipOutputStream", "zipfile", "make_archive")
    transfer_sources = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and "transfer" in path.name.casefold()
    ]
    assert transfer_sources
    for path in transfer_sources:
        value = path.read_text(encoding="utf-8")
        assert not any(marker in value for marker in forbidden)
