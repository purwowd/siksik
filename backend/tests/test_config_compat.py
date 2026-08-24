from app.core.config import Settings


def test_cors_origins_accept_comma_separated_value():
    settings = Settings(cors_origins="http://localhost:5173, http://192.168.1.4:5173")
    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://192.168.1.4:5173",
    ]
