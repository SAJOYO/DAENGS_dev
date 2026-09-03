from sqlalchemy.engine import make_url

from daengs_place.core.config import Settings


def test_database_components_preserve_reserved_password_characters():
    settings = Settings(
        database_url=None,
        db_host="place-db",
        db_port=5432,
        db_user="place",
        db_password="p@ss/word#1",
        db_name="place",
    )

    url = make_url(settings.sqlalchemy_url)

    assert url.username == "place"
    assert url.password == "p@ss/word#1"
    assert url.host == "place-db"
    assert url.port == 5432
    assert url.database == "place"


def test_database_url_override_remains_available_for_local_and_ci_runs():
    settings = Settings(database_url="postgresql+asyncpg://ci:ci@localhost:5544/test_place")

    assert settings.sqlalchemy_url.host == "localhost"
    assert settings.sqlalchemy_url.port == 5544
    assert settings.sqlalchemy_url.database == "test_place"


def test_place_gemini_settings_reuse_existing_unprefixed_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "place-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setenv("GEMINI_TIMEOUT_MS", "12500")

    settings = Settings(_env_file=None)

    assert settings.gemini_api_key.get_secret_value() == "place-test-key"
    assert settings.gemini_model == "gemini-test-model"
    assert settings.gemini_timeout_ms == 12_500


def test_place_gemini_settings_are_optional(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TIMEOUT_MS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.gemini_api_key.get_secret_value() == ""
    assert settings.gemini_model == "gemini-3.1-flash-lite"
    assert settings.gemini_timeout_ms == 30_000
