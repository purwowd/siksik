from __future__ import annotations

from pydantic import Field

from app.models.base import RequestModel, ResponseModel


class MediaTicketRequest(RequestModel):
    path: str = Field(min_length=1, max_length=1024)


class MediaTicketOut(ResponseModel):
    ticket: str = Field(min_length=32, max_length=256)
    expires_at: str


class GalleryAlbumOut(ResponseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern=r"^(access|album)$")
    count: int = Field(ge=0)


class GalleryItemOut(ResponseModel):
    id: str
    session_id: str
    file_id: str
    source: str
    path: str
    album: str
    album_key: str
    label: str
    mime: str | None = None
    preview_path: str | None = Field(default=None, max_length=1024)
    preview_text: str | None = Field(default=None, max_length=2000)
    captured_at: str | None = None
    favorite: bool = False


class PaginatedGallery(ResponseModel):
    items: list[GalleryItemOut] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
