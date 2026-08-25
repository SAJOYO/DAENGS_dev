from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# backend/.env 를 가리킵니다. config.py 기준으로 잡아 두면
# 어느 디렉터리에서 실행하든 같은 파일을 읽습니다.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """환경 변수 / backend/.env 로 덮어쓸 수 있는 설정.

    예) DAENGS_CORS_ORIGINS='["http://localhost:3000","https://daengs.example"]'
    """

    model_config = SettingsConfigDict(
        env_prefix="DAENGS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 개발 서버가 바인딩할 주소.
    # 호스트에서 띄울 때는 루프백이면 충분하지만,
    # 컨테이너 안에서는 0.0.0.0 이어야 밖에서 닿습니다. (compose 가 DAENGS_HOST 로 넘겨줍니다)
    host: str = "127.0.0.1"
    port: int = 8000

    # 브라우저가 프론트 오리진에서 API 를 부를 때 허용할 목록.
    # 서버에 올릴 때는 프론트 도메인을 여기에 추가해야 합니다.
    # 등록하는 것은 '부르는 쪽'(프론트)이지 API 도메인이 아닙니다.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # 쿠키에 Secure 를 붙일지. **HTTPS 를 붙이면 반드시 true 로 바꾸세요.**
    #
    # 지금 배포가 http 라 기본값이 false 입니다. Secure 쿠키는 https 로만 전송돼서,
    # 지금 켜면 로그인이 되고도 쿠키가 안 실려 곧바로 로그아웃됩니다.
    # 반대로 https 로 옮긴 뒤에도 false 로 두면 토큰이 평문으로 오갑니다.
    cookie_secure: bool = False

    # ── DB ────────────────────────────────────────────────────────────
    # URL 한 줄이 아니라 조각으로 받습니다 (D-013). 개발 PC 와 서버가 다른 것은
    # 사실상 호스트 하나뿐인데, URL 로 받으면 그 한 글자 때문에 접속 정보 전체를
    # 두 군데에 각각 적게 됩니다.
    #
    # 조립은 아래 database_url 이 합니다.
    #   개발 PC : 팀 DB 가 있는 서버 IP (최상단 .env 의 POSTGRES_IP)
    #   서버 PC : compose 가 DAENGS_DB_HOST=pgvector 로 넘깁니다.
    #             컨테이너 안에서 localhost 는 컨테이너 자신이라 닿지 않습니다.
    db_host: str
    db_port: int = 5432

    # 최상단 .env 의 POSTGRES_USER / POSTGRES_PASSWORD 와 같은 값입니다.
    # 서버 컨테이너에는 compose 가 거기서 읽어 넘겨 주므로, 접속 정보의 원본은
    # 여전히 최상단 .env 하나입니다 (D-009).
    db_user: str = "postgres"
    db_password: SecretStr  # 기본값을 두지 않습니다. LAN 에 열린 DB 입니다

    db_name: str = "vectordb"

    # 실행되는 SQL 을 로그로 찍습니다. 쿼리를 들여다볼 때만 켜세요.
    db_echo: bool = False

    # 조각으로 바뀌기 전에 쓰던 이름입니다 (D-013).
    #
    # extra="ignore" 라서 .env 에 남아 있어도 조용히 무시되는데, 그러면 개발 PC 가
    # 엉뚱한 호스트로 붙어서 원인을 찾기 어려운 인증 실패를 봅니다. 옛 줄이 보이면
    # 무시하지 말고 뜨지 않는 편이 낫습니다. 언젠가 지워도 되는 필드입니다.
    legacy_database_url: str | None = Field(
        default=None, validation_alias="DAENGS_DATABASE_URL"
    )

    # ── 암호화 키 ─────────────────────────────────────────────────────
    # 셋 다 32바이트 난수를 urlsafe base64 로 인코딩한 문자열입니다.
    #
    #   uv run python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    #
    # 기본값을 두지 않습니다 — 키가 없으면 앱이 뜨지 않는 것이 맞습니다.
    # 개발용 기본값을 하나 박아 두면 그게 그대로 서버에 올라갑니다.
    #
    # SecretStr 이라 로그·예외 메시지에는 '**********' 로 찍힙니다.
    # 실제 값은 .get_secret_value() 로 꺼내며, 꺼내는 곳은 core/crypto.py 뿐입니다.
    #
    # 셋을 나눠 둔 이유는 잃었을 때의 결과가 전부 다르기 때문입니다 (D-012).
    jwe_key: SecretStr  # 토큰 암호화. 아직 안 씁니다 — 로그인 API 카드에서.
    aes_key: SecretStr  # 개인정보 컬럼 암복호화
    blind_index_key: SecretStr  # HMAC pepper. AES 키와 같은 값을 쓰면 안 됩니다.

    @model_validator(mode="after")
    def _reject_legacy_database_url(self) -> "Settings":
        if self.legacy_database_url is not None:
            raise ValueError(
                "DAENGS_DATABASE_URL 은 더 이상 쓰지 않습니다 (D-013). backend/.env 에서 "
                "그 줄을 지우고 DAENGS_DB_HOST / DAENGS_DB_PASSWORD 를 넣으세요. "
                "backend/.env.example 에 예시가 있습니다."
            )
        return self

    @property
    def database_url(self) -> URL:
        """SQLAlchemy 접속 URL.

        문자열로 이어 붙이지 않고 URL.create 로 만듭니다. 비밀번호에 `@` `/` `#` 가
        들어가면 이어 붙인 URL 은 엉뚱하게 파싱되는데, 여기서는 알아서 이스케이프됩니다.

        로그에 찍을 일이 있으면 `.render_as_string(hide_password=True)` 를 쓰세요.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


def _load_settings(**overrides: object) -> Settings:
    """설정을 읽습니다. 실패하면 **값은 빼고** 무엇이 잘못됐는지만 알립니다.

    pydantic 의 ValidationError 메시지에는 입력값이 그대로 들어갑니다. 여기서 읽는
    입력에는 암호화 키와 DB 비밀번호가 있어서, 그대로 올리면 부팅 실패 로그에
    원문이 남습니다. 필드 이름과 사유만 남기고 값은 버립니다.

    `from None` 인 것도 같은 이유입니다 — 원래 예외를 달고 가면 그 메시지가
    트레이스백에 그대로 찍힙니다.
    """
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc']) or '(전체)'}: {error['msg']}"
            for error in exc.errors()
        )
        raise RuntimeError(
            "설정을 읽지 못했습니다. backend/.env 를 확인하세요 "
            f"(backend/.env.example 참고).\n{problems}"
        ) from None


settings = _load_settings()
