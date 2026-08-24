from app.core.branding import (
    CANONICAL_CRAWL_RECORD_MIME,
    LEGACY_CRAWL_RECORD_MIME,
    MAIN_AGENT_CRAWL_RECORD_MIME,
    is_crawl_record_mime,
)


def test_crawl_record_mime_accepts_satria_and_siksik():
    assert is_crawl_record_mime(CANONICAL_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime(LEGACY_CRAWL_RECORD_MIME)
    assert is_crawl_record_mime(MAIN_AGENT_CRAWL_RECORD_MIME)
    assert not is_crawl_record_mime("application/json")
