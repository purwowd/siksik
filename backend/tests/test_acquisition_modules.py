"""Smoke tests for split acquisition modules."""

from __future__ import annotations


def test_media_types_exports():
    from app.acquisition.media_types import IMG_EXT, _is_junk_media_path, guess_mime
    from pathlib import Path

    assert ".jpg" in IMG_EXT
    assert _is_junk_media_path(".nomedia")
    assert guess_mime(Path("x.jpg")) == "image/jpeg"


def test_toolchain_exports():
    from app.acquisition import toolchain

    assert callable(toolchain.toolchain_status)
    assert callable(toolchain.detect_devices)


def test_indexing_exports():
    from app.acquisition import indexing

    assert callable(indexing.index_staging)
    assert callable(indexing.hash_file)


def test_orchestration_reexports():
    from app.services import acquisition as legacy
    from app.acquisition import orchestration

    assert legacy.index_staging is orchestration.index_staging
    assert legacy.toolchain_status is orchestration.toolchain_status
