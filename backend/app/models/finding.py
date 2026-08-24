from __future__ import annotations

from pydantic import Field

from app.models.base import RequestModel, ResponseModel
from app.models.enums import ReviewStatus


class ReviewRequest(RequestModel):
    review_status: ReviewStatus


class BulkReviewRequest(RequestModel):
    review_status: ReviewStatus


class FindingOut(ResponseModel):
    id: str
    session_id: str
    file_id: str
    source: str
    path: str
    category: str
    label: str
    confidence: float = Field(ge=0, le=1)
    layer_origin: str
    evidence: str
    review_status: ReviewStatus
    created_at: str
    media_year: int | None = None
    media_captured_at: str | None = None
    preview_path: str | None = Field(default=None, max_length=1024)
    preview_text: str | None = Field(default=None, max_length=320)
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class PaginatedFindings(ResponseModel):
    items: list[FindingOut] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
