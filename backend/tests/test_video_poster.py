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
def test_is_video_path_treats_animated_gif_as_video(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from app.services.video_poster import is_video_path as probe

    gif = tmp_path / "clip.gif"
    frames = [
        Image.new("RGB", (4, 4), (1, 2, 3)),
        Image.new("RGB", (4, 4), (4, 5, 6)),
    ]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=40, loop=0)
    still = tmp_path / "photo.webp"
    Image.new("RGB", (4, 4), (9, 9, 9)).save(still, format="WEBP")

    assert probe(gif) is True
    assert probe(still) is False


@pytest.mark.unit
def test_extract_video_poster_skips_missing_file(tmp_path: Path) -> None:
    assert extract_video_poster(tmp_path / "missing.mp4") is None
