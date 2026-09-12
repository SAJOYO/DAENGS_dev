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


def test_facility_provider_uses_its_own_model_and_keeps_shared_discovery_model(monkeypatch):
    from daengs_place.api import conversation_internal

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "discovery-model")
    monkeypatch.setenv("FACILITY_CONVERSATION_MODEL", "facility-model")
    settings = Settings(_env_file=None)
    monkeypatch.setattr(conversation_internal, "settings", settings)
    assert conversation_internal.provider().model == "facility-model"
    assert settings.gemini_model == "discovery-model"


def test_place_database_url_has_its_own_environment_name(monkeypatch):
    """CI 는 `DAENGS_PLACE_DATABASE_URL` 로 준다 (#346).

    옛 이름 `DAENGS_DATABASE_URL` 은 `daengs_backend.config` 가 보면 기동을 거부한다 (D-013).
    place CI 가 그 이름을 쓰면 루트 conftest 의 autouse fixture 가 backend 를 import 하는
    순간 place 테스트가 죽는다 — 이름을 place 전용으로 갈라 두 설정이 같은 변수를 안 본다.
    """
    monkeypatch.delenv("DAENGS_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DAENGS_PLACE_DATABASE_URL", "postgresql+asyncpg://ci:ci@localhost:5544/test_place"
    )

    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_url.port == 5544
    assert settings.sqlalchemy_url.database == "test_place"


def test_place_database_url_still_accepts_the_upstream_name(monkeypatch):
    """로컬 한 줄 설정과 상류(UPSTREAM.md)는 `DAENGS_DATABASE_URL` 을 쓴다 — 그대로 받는다."""
    monkeypatch.delenv("DAENGS_PLACE_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DAENGS_DATABASE_URL", "postgresql+asyncpg://ci:ci@localhost:5545/legacy_place"
    )

    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_url.port == 5545
    assert settings.sqlalchemy_url.database == "legacy_place"
