from app.api.v1._route_group import route_group

router = route_group(
    "session_report",
    "authorize_session",
)
