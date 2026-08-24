from app.api.v1._route_group import route_group

router = route_group(
    "auth_login",
    "auth_logout",
    "auth_me",
    "auth_users",
    "auth_roles",
)
