from app.api.v1._route_group import route_group

router = route_group(
    "bootstrap_android_agent",
    "android_agent_status",
)
