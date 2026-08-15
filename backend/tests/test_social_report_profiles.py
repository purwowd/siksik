from app.acquisition.agent_client import SocialProfileMetricsV1
from app.services.reports import (
    _is_profile_metric_chrome,
    _profile_metrics,
    _profile_username,
    _social_account_heading,
)


def test_facebook_profile_does_not_invent_username_from_prose() -> None:
    lines = [
        "We made it easier to add the stuff you like.",
        "Demo User",
    ]

    assert _profile_username("com.facebook.katana", {}, {}, lines) is None
    assert (
        _profile_username("com.facebook.katana", {}, {}, ["@demo.user"])
        == "demo.user"
    )


def test_profile_metrics_keep_friends_separate_from_followers() -> None:
    metrics = _profile_metrics({}, ["2 friends · 5 posts"])

    assert metrics == {
        "posts": 5,
        "followers": None,
        "friends": 2,
        "following": None,
    }
    assert _is_profile_metric_chrome("2 friends · 5 posts")
    assert not _is_profile_metric_chrome("I published 5 posts today")
    assert SocialProfileMetricsV1(posts=5, friends=2).model_dump() == {
        "posts": 5,
        "followers": None,
        "friends": 2,
        "following": None,
    }


def test_social_heading_uses_facebook_display_name_without_placeholder_handle() -> None:
    assert _social_account_heading(
        {
            "platform": "Facebook",
            "display_name": "Demo User",
            "username": None,
        }
    ) == "Facebook · Demo User"
