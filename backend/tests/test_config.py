"""config.py — DB 접속 URL 조립."""

import pytest
from pydantic import ValidationError

from daengs_backend.config import Settings, _load_settings


def _settings(**kwargs: object) -> Settings:
    """테스트용 Settings. 필수 값만 채우고 나머지는 인자로 덮어씁니다."""
    return Settings(**{"db_host": "pgvector", "db_password": "pw", **kwargs})  # type: ignore[arg-type]


def test_url_is_assembled_from_parts() -> None:
    url = _settings(db_port=5433, db_user="daengs", db_name="vectordb").database_url

    assert url.drivername == "postgresql+asyncpg"
    assert (url.host, url.port) == ("pgvector", 5433)
    assert (url.username, url.database) == ("daengs", "vectordb")


def test_special_characters_in_password_are_escaped() -> None:
    """이 카드의 이유. 문자열로 이어 붙였다면 여기서 URL 이 깨집니다."""
    url = _settings(db_user="daengs", db_password="p@ss/w#rd").database_url

    assert url.password == "p@ss/w#rd"
    assert "p%40ss%2Fw%23rd" in url.render_as_string(hide_password=False)


def test_rendered_url_hides_password_by_default() -> None:
    """로그에 URL 을 찍어도 비밀번호가 새지 않아야 합니다."""
    assert "s3cret" not in _settings(db_password="s3cret").database_url.render_as_string()


def test_legacy_database_url_is_rejected() -> None:
    """조용히 무시되면 엉뚱한 호스트로 붙어 원인 찾기가 어렵습니다 (D-013)."""
    with pytest.raises(ValidationError, match="DAENGS_DATABASE_URL"):
        _settings(DAENGS_DATABASE_URL="postgresql+asyncpg://a:b@c:5432/d")


def test_load_error_names_the_field_but_not_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설정 로딩 실패 로그에 키·비밀번호 원문이 남으면 안 됩니다."""
    monkeypatch.delenv("DAENGS_DB_PASSWORD")
    monkeypatch.setenv("DAENGS_AES_KEY", "SUPER-SECRET-VALUE")

    # _env_file=None : 개발자 PC 의 backend/.env 에 값이 있어도 결과가 같도록.
    with pytest.raises(RuntimeError) as err:
        _load_settings(_env_file=None)

    assert "db_password" in str(err.value)
    assert "SUPER-SECRET-VALUE" not in str(err.value)
