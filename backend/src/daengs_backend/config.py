from pathlib import Path

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
    # 기본값은 '로컬에서 uv run dev 로 띄울 때' 기준입니다.
    # 컨테이너 안에서는 localhost 가 컨테이너 자신이라 닿지 않으므로,
    # compose 가 DAENGS_DATABASE_URL 을 넘겨 이 값을 덮어씁니다.
    # (호스트가 pgvector 로 바뀝니다 - docker-compose.yml 참고)
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/vectordb"
    )

    # 실행되는 SQL 을 로그로 찍습니다. 쿼리를 들여다볼 때만 켜세요.
    db_echo: bool = False


settings = Settings()
