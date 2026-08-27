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

    # ---- 별도 Training RAG service -----------------------------------------
    # 메인 backend에 모델·벡터 검색기를 넣지 않는다. 반드시 별도 FastAPI service
    # URL을 환경변수로 받으며, 컨테이너와 로컬 개발의 주소 차이는 env로만 해결한다.
    training_rag_base_url: str
    training_rag_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    training_rag_read_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    # 인증 화면이 아직 연결되지 않은 로컬 데모에서만 명시적으로 true로 둔다.
    # 기본값은 기존 앱 access token을 요구한다.
    training_rag_allow_anonymous_demo: bool = False

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
    jwe_key: SecretStr  # 토큰 암호화 (access token)
    aes_key: SecretStr  # 개인정보 컬럼 암복호화
    blind_index_key: SecretStr  # HMAC pepper. AES 키와 같은 값을 쓰면 안 됩니다.

    # ── 카카오 ────────────────────────────────────────────────────────
    # 앱이 보낸 id_token 의 `aud` 와 대조할 **앱 키 목록**입니다 —
    # "이 토큰이 우리 앱에게 발급된 것인가" (D-017).
    #
    # **하나가 아니라 목록인 이유**: 카카오의 `aud` 는 인가 요청에 쓴 앱 키
    # 그대로입니다. 같은 애플리케이션이라도 들어온 경로마다 값이 다릅니다.
    #
    #     Android / iOS SDK   네이티브 앱 키
    #     JavaScript SDK      JavaScript 키
    #     REST API            REST API 키   (cli/kakao_token.py 가 쓰는 흐름)
    #
    # 그래서 REST 키 하나만 대조하면 **앱 로그인이 전부 401 로 막힙니다.**
    #
    # **한 애플리케이션에서 나온 키만 넣으세요.** 카카오 회원번호(`sub`)는 앱 단위라,
    # 다른 애플리케이션의 키를 섞으면 같은 사람이 서로 다른 회원으로 갈라집니다.
    # 목록을 늘린다고 검사가 느슨해지는 것은 아닙니다 — 우리 앱'들'이 되는 것뿐입니다.
    # 어드민 키는 넣지 마세요. aud 로 오지 않고, 그 키 하나로 전 회원을 조작합니다.
    #
    # **비밀이 아닙니다.** 앱에도 들어가 있고 카카오에 요청할 때 그대로 나갑니다.
    # 그래도 기본값을 두지 않습니다. 기본값을 두면 검증이 조용히 엉뚱한 aud 를
    # 통과시키게 되는데, 그건 남의 앱 토큰으로 우리 서비스에 계정이 생긴다는 뜻입니다.
    #
    # 값은 JSON 배열입니다: DAENGS_KAKAO_APP_KEYS=["<네이티브>","<REST>"]
    #
    # 필수 필드로 두지 않고 빈 목록을 기본값으로 둔 뒤 아래 validator 에서 막습니다.
    # 필수로 두면 pydantic 이 "Field required" 로 먼저 끊어서, 옛 이름을 쓰고 있는
    # 사람에게 **무엇으로 바뀌었는지** 알려 줄 기회가 없어집니다.
    kakao_app_keys: list[str] = Field(default_factory=list)

    # `cli/kakao_token.py` 전용입니다. **로그인 검증에는 쓰이지 않습니다.**
    #
    # 그 CLI 는 REST 흐름이라 `client_id` 자리에 **REST API 키**를 넣어야 하는데,
    # 위 목록에서는 어느 것이 REST 키인지 알 수 없습니다 (넣은 순서에 기대면
    # 언젠가 조용히 어긋납니다). 그래서 한 줄 더 둡니다.
    #
    # 없어도 됩니다 — 그 CLI 를 쓸 때만 필요하고, 서버는 이 값 없이 잘 돕니다.
    # 하나만 받던 시절의 이름과 같아서, 이 값만 있고 위 목록이 비어 있으면
    # 아래 validator 가 "이름이 바뀌었다"고 알려 줍니다.
    kakao_rest_api_key: str | None = None

    @model_validator(mode="after")
    def _reject_legacy_database_url(self) -> "Settings":
        if self.legacy_database_url is not None:
            raise ValueError(
                "DAENGS_DATABASE_URL 은 더 이상 쓰지 않습니다 (D-013). backend/.env 에서 "
                "그 줄을 지우고 DAENGS_DB_HOST / DAENGS_DB_PASSWORD 를 넣으세요. "
                "backend/.env.example 에 예시가 있습니다."
            )
        if not self.training_rag_base_url.startswith(("http://", "https://")):
            raise ValueError("DAENGS_TRAINING_RAG_BASE_URL must start with http:// or https://")
        return self

    @model_validator(mode="after")
    def _check_kakao_app_keys(self) -> "Settings":
        """앱 키 목록이 **비어 있지 않은지** 확인합니다. 이 검사가 핵심입니다.

        joserfc 의 `JWTClaimsRegistry` 는 `values` 가 빈 목록이면 aud 검사를
        **통째로 건너뜁니다** (`check_value` 의 `if not option_values: return`).
        즉 `DAENGS_KAKAO_APP_KEYS=[]` 는 "앱 키가 없다"가 아니라
        **"아무 카카오 앱의 토큰이나 통과"** 가 됩니다 — 남의 앱에서 받은 토큰으로
        우리 서비스에 계정이 생깁니다. 필수 필드로 두는 것만으로는 못 막습니다.

        빈 문자열도 같이 걸러 냅니다. `[""]` 는 목록이 비지 않았지만 어떤 aud 와도
        맞지 않아, 전원 로그인 불가를 조용히 만듭니다.
        """
        if self.kakao_rest_api_key and not self.kakao_app_keys:
            # 옛 이름만 남아 있는 .env 입니다. 위 필드는 이제 CLI 전용이라
            # 이것만으로는 로그인 검증이 서지 않습니다.
            raise ValueError(
                "DAENGS_KAKAO_REST_API_KEY 는 DAENGS_KAKAO_APP_KEYS 로 바뀌었습니다. "
                "카카오 id_token 의 aud 는 로그인에 쓴 앱 키라, 네이티브 SDK 로 들어온 "
                "토큰은 REST API 키와 맞지 않습니다. backend/.env 에서 그 줄을 "
                'DAENGS_KAKAO_APP_KEYS=["<네이티브 앱 키>","<REST API 키>"] 로 바꾸세요. '
                "backend/.env.example 에 예시가 있습니다."
            )

        if not self.kakao_app_keys or any(not k.strip() for k in self.kakao_app_keys):
            raise ValueError(
                "DAENGS_KAKAO_APP_KEYS 가 비어 있습니다. 비워 두면 aud 검증이 통째로 "
                "건너뛰어져 남의 카카오 앱 토큰으로도 우리 서비스에 계정이 생깁니다 "
                '(D-017). 예) DAENGS_KAKAO_APP_KEYS=["<네이티브 앱 키>","<REST API 키>"]'
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
