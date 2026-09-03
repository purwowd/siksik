from app.services.social_preview import build_social_preview, social_preview_summary


def test_x_profile_omits_unlabelled_numbers_and_profile_chrome() -> None:
    preview = build_social_preview(
        source_app="com.twitter.android",
        social_scope="own_profile",
        normalized_text=(
            "Banner profil\n"
            "Saya Lapar\n"
            "@LaparSaya92719\n"
            "Lahir Desember 15, 1998\n"
            "Bergabung Desember 2025\n"
            "1\n"
            "1\n"
            "Sebarkan"
        ),
        canonical={
            "metadata": {
                "profile_username": "LaparSaya92719",
                "profile_metrics": {
                    "following": None,
                    "followers": None,
                },
            }
        },
    )

    assert preview == {
        "platform": "x",
        "kind": "profile",
        "display_name": None,
        "username": "@LaparSaya92719",
        "body": None,
        "birth_date": "Lahir 15 Desember 1998",
        "published_label": None,
        "following": None,
        "followers": None,
    }
    summary = social_preview_summary(preview)
    assert summary == "@LaparSaya92719 · Lahir 15 Desember 1998"
    assert "Banner" not in summary
    assert "Bergabung" not in summary
    assert "Mengikuti" not in summary
    assert "Pengikut" not in summary


def test_x_post_keeps_content_but_drops_controls_and_detached_like_number() -> None:
    preview = build_social_preview(
        source_app="com.twitter.android",
        social_scope="own_tweets",
        normalized_text=(
            "Saya Lapar\n"
            "Jelaskan postingan ini dengan Grok\n"
            "Opsi postingan\n"
            "Makar\n"
            "@LaparSaya92719\n"
            "· 5 hari\n"
            "Balas\n"
            "Posting ulang\n"
            "Tayangan\n"
            "Markah\n"
            "Sebarkan\n"
            "Suka\n"
            "9"
        ),
    )

    assert preview is not None
    assert preview["kind"] == "post"
    assert preview["display_name"] == "Saya Lapar"
    assert preview["username"] == "@LaparSaya92719"
    assert preview["published_label"] == "5 hari"
    assert preview["body"] == "Makar"
    assert social_preview_summary(preview) == "Makar"
    assert "9" not in preview.values()


def test_x_reply_uses_reply_kind_and_only_accepts_explicit_profile_metrics() -> None:
    reply = build_social_preview(
        source_app="com.twitter.android",
        social_scope="own_replies",
        normalized_text="Saya Lapar\nMakar\n@LaparSaya92719\n· 5 hari\nBalas\n9",
    )
    profile = build_social_preview(
        source_app="com.twitter.android",
        social_scope="own_profile",
        normalized_text=(
            "@akun\nLahir 2 January 2000\n0\nMengikuti\n0\nPengikut"
        ),
    )

    assert reply is not None and reply["kind"] == "reply"
    assert reply["body"] == "Makar"
    assert profile is not None
    assert profile["following"] == "0"
    assert profile["followers"] == "0"
    assert profile["birth_date"] == "Lahir 2 Januari 2000"


def test_non_x_social_record_is_unchanged_by_presenter() -> None:
    assert build_social_preview(
        source_app="com.facebook.katana",
        social_scope="own_posts",
        normalized_text="Postingan Facebook",
    ) is None
