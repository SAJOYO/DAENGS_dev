from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 / .env 로 덮어쓸 수 있는 설정.

    예) DAENGS_CORS_ORIGINS='["http://localhost:3000","https://daengs.example"]'
    """

    model_config = SettingsConfigDict(
        env_prefix="DAENGS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 로컬 개발용 프론트 주소.
    # 배포 환경에서는 nginx 가 /api/ 를 같은 오리진으로 프록시하므로
    # CORS 자체가 필요 없습니다. (필요해지면 환경 변수로 추가)
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


settings = Settings()
