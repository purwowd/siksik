"""SATRIA branding compatibility — dual env, MIME, session-id aliases."""

from __future__ import annotations

import os

import pytest

from app.core.branding import (
    CANONICAL_CRAWL_RECORD_MIME,
    LEGACY_CRAWL_RECORD_MIME,
    crawl_record_filename_mime,
    is_crawl_record_mime,
    promote_satria_env,
)
from app.selection.contracts import SelectionRunV1


pytestmark = pytest.mark.unit


def test_is_crawl_record_mime_accepts_legacy_and_satria():
    assert is_crawl_record_mime(CANONICAL_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime(LEGACY_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime("application/vnd.SIKSIK.crawl-record+json")
    assert not is_crawl_record_mime("application/json")
    assert not is_crawl_record_mime(None)


def test_crawl_record_filename_mime():
    assert crawl_record_filename_mime("a.satria-record.json") == CANONICAL_CRAWL_RECORD_MIME
    assert crawl_record_filename_mime("a.siksik-record.json") == LEGACY_CRAWL_RECORD_MIME
    assert crawl_record_filename_mime("photo.jpg") is None


def test_promote_satria_env_overrides_sadt(monkeypatch):
    monkeypatch.delenv("SATRIA_OCR_ENABLED", raising=False)
    monkeypatch.delenv("SADT_OCR_ENABLED", raising=False)
    monkeypatch.setenv("SADT_OCR_ENABLED", "0")
    monkeypatch.setenv("SATRIA_OCR_ENABLED", "1")
    promote_satria_env()
    assert os.environ["SADT_OCR_ENABLED"] == "1"


def test_promote_satria_env_fills_missing_sadt(monkeypatch):
    monkeypatch.delenv("SATRIA_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("SADT_WORKER_CONCURRENCY", raising=False)
    monkeypatch.setenv("SATRIA_WORKER_CONCURRENCY", "6")
    promote_satria_env()
    assert os.environ["SADT_WORKER_CONCURRENCY"] == "6"


def _minimal_run_payload(session_key: str, session_value: str) -> dict:
    return {
        "schema_version": 1,
        "crawl_id": "crawl-abcdefgh",
        session_key: session_value,
        "state": "confirmed",
        "policy_version": "v1",
        "policy_fingerprint": "a" * 64,
        "revision": 1,
        "review_candidates": False,
        "totals": {
            "total": 0,
            "evaluated": 0,
            "candidates": 0,
            "auto_selected": 0,
            "selected": 0,
            "below_threshold": 0,
            "selected_bytes": 0,
        },
        "started_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:01Z",
    }


def test_selection_run_accepts_legacy_session_id_key():
    run = SelectionRunV1.model_validate(
        _minimal_run_payload("siksik_session_id", "session-abcdefgh")
    )
    assert run.siksik_session_id == "session-abcdefgh"


def test_selection_run_accepts_satria_session_id_alias():
    run = SelectionRunV1.model_validate(
        _minimal_run_payload("satria_session_id", "session-satria01")
    )
    assert run.siksik_session_id == "session-satria01"
    dumped = run.model_dump(by_alias=True)
    assert dumped["siksik_session_id"] == "session-satria01"
    assert "satria_session_id" not in dumped
