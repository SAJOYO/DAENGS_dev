"""config.py — DB 접속 URL 조립 · 카카오 앱 키 목록."""

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


def test_place_discovery_uses_compose_dns_and_an_independent_timeout_by_default() -> None:
    configured = _settings()
    assert configured.place_search_base_url == "http://place-search:8000"
    assert configured.place_discovery_timeout_ms == 15_000


def test_place_discovery_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _settings(place_discovery_timeout_ms=0)


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


class TestKakaoAppKeys:
    """`aud` 허용 목록. **비어 있으면 검증이 통째로 사라집니다.**"""

    def test_여러_키를_받는다(self) -> None:
        """앱은 네이티브 키로, cli/kakao_token.py 는 REST 키로 로그인합니다."""
        s = _settings(kakao_app_keys=["native-key", "rest-key"])
        assert s.kakao_app_keys == ["native-key", "rest-key"]

    def test_빈_목록은_거부한다(self) -> None:
        """**이 테스트가 이 카드의 본체입니다.**

        joserfc 의 `check_value` 는 `values` 가 비면 `return` 으로 빠져나가
        aud 검사를 하지 않습니다. 즉 빈 목록은 "앱 키가 없다"가 아니라
        **"아무 카카오 앱의 토큰이나 통과"** 입니다. 필수 필드로 두는 것만으로는
        `[]` 를 못 막으니 validator 가 있어야 합니다 (D-017).
        """
        with pytest.raises(ValidationError, match="DAENGS_KAKAO_APP_KEYS"):
            _settings(kakao_app_keys=[])

    def test_빈_문자열이_섞이면_거부한다(self) -> None:
        """`[""]` 는 목록이 비지 않았지만 어떤 aud 와도 안 맞아, 전원 로그인 불가입니다."""
        with pytest.raises(ValidationError, match="DAENGS_KAKAO_APP_KEYS"):
            _settings(kakao_app_keys=["native-key", "  "])

    def test_옛_이름만_남아_있으면_바뀌었다고_알려_준다(self) -> None:
        """조용히 무시되면 앱 로그인이 전부 401 인데 원인이 안 보입니다 (D-013 과 같은 이유)."""
        with pytest.raises(ValidationError, match="DAENGS_KAKAO_APP_KEYS"):
            _settings(kakao_app_keys=[], kakao_rest_api_key="rest-key")

    def test_CLI_용_REST_키는_목록과_따로_있어도_된다(self) -> None:
        """`cli/kakao_token.py` 는 client_id 로 REST 키가 필요합니다. 목록이 서 있으면 정상입니다."""
        s = _settings(kakao_app_keys=["native-key"], kakao_rest_api_key="rest-key")
        assert s.kakao_rest_api_key == "rest-key"
