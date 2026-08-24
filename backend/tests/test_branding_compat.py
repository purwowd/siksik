from app.core.branding import (
    CANONICAL_CRAWL_RECORD_MIME,
    LEGACY_CRAWL_RECORD_MIME,
    MAIN_AGENT_CRAWL_RECORD_MIME,
    is_crawl_record_mime,
)
from app.selection.contracts import SelectionRunV1


def test_crawl_record_mime_accepts_satria_and_siksik():
    assert is_crawl_record_mime(CANONICAL_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime(LEGACY_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime(MAIN_AGENT_CRAWL_RECORD_MIME)
    assert not is_crawl_record_mime("application/json")


def _selection_payload(session_key: str) -> dict:
    return {
        "schema_version": 1,
        "crawl_id": "crawl-abcdefgh",
        session_key: "session-abcdefgh",
        "state": "confirmed",
        "policy_version": "siksik-selection-v6",
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


def test_selection_session_id_accepts_satria_and_serializes_legacy():
    run = SelectionRunV1.model_validate(_selection_payload("satria_session_id"))
    assert run.siksik_session_id == "session-abcdefgh"
    dumped = run.model_dump(by_alias=True)
    assert dumped["siksik_session_id"] == "session-abcdefgh"
    assert "satria_session_id" not in dumped
