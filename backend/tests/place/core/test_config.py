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
