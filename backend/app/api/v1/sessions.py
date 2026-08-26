from app.api.v1._route_group import route_group

router = route_group(
    "start_session",
    "start_session_from_zip",
    "list_sessions",
    "get_session",
    "update_session_participant",
    "session_stream",
    "cancel_session",
    "session_audit",
    "refresh_session_mapping_endpoint",
)
