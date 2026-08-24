from app.models.schemas import BulkReviewRequest, ReviewStatus


def test_bulk_review_request_shape():
    body = BulkReviewRequest(review_status=ReviewStatus.CONFIRMED)
    assert body.review_status is ReviewStatus.CONFIRMED
