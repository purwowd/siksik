from __future__ import annotations

from pathlib import Path

import pytest

from app.services.video_poster import extract_video_poster, is_video_path


@pytest.mark.unit
def test_is_video_path_uses_mime_or_suffix() -> None:
    assert is_video_path(Path("a.bin"), "video/mp4") is True
    assert is_video_path(Path("clip.MP4"), None) is True
    assert is_video_path(Path("photo.jpg"), "image/jpeg") is False


@pytest.mark.unit
def test_extract_video_poster_skips_missing_file(tmp_path: Path) -> None:
    assert extract_video_poster(tmp_path / "missing.mp4") is None
