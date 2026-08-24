from app.api.v1._route_group import route_group

router = route_group(
    "clear_hash_cache_endpoint",
    "recompute_recommendations_endpoint",
)
