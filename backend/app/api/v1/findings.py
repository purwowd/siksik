from app.api.v1._route_group import route_group

router = route_group(
    "session_findings",
    "all_findings",
    "review_finding",
    "bulk_review_findings",
    "session_risk_timeline",
)
