from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # SQLAlchemy 접속 URL. 드라이버까지 포함해야 합니다 (`+asyncpg`).
    #
    # 이 기본값은 사실상 자리표시자입니다. 어느 쪽에서 띄우든 덮어써집니다.
    #   개발 PC : DB 가 서버 PC 에 하나뿐이므로 backend/.env 에 그 IP 를 적습니다.
    #             localhost 로는 안 붙습니다.
    #   서버 PC : 컨테이너 안에서는 localhost 가 컨테이너 자신이라, compose 가
    #             같은 이름의 환경 변수로 덮어써 pgvector 서비스를 보게 합니다.
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/vectordb"
    )

    # 실행되는 SQL 을 로그로 찍습니다. 쿼리를 들여다볼 때만 켜세요.
    db_echo: bool = False

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


settings = Settings()
