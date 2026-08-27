from app.api.v1._route_group import route_group

router = route_group(
    "get_ios_setup",
    "start_ios_setup",
    "submit_ios_setup_code",
    "ack_ios_setup_trust",
    "cancel_ios_setup",
)
