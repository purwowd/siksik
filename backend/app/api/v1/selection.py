from app.api.v1._route_group import route_group

router = route_group(
    "session_crawl_selection",
    "session_candidates",
    "update_session_candidate",
    "confirm_session_candidates",
)
