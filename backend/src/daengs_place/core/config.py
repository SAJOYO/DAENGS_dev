"""Place 런타임이 실제로 소비하는 설정만.

원본(DAENGS_geo `app/core/config.py`)은 지도/route/LLM provider 키까지 한 덩어리였다.
이 서비스는 PostGIS만으로 기존 검색을 계속 제공하고, optional Gemini 설정은 내부 discovery
요청에서만 읽는다. 지도/route/deeplink 설정을 다시 들여오는 변경은 경계 침범이다.
"""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="DAENGS_", extra="ignore", populate_by_name=True,
    )

    # 로컬·CI의 기존 한 줄 설정은 유지하되, compose는 아래 조각을 넘긴다. 비밀번호를
    # URL 문자열에 이어 붙이면 @ / # 같은 예약 문자가 사용자·호스트 경계를 깨뜨린다.
    #
    # 이름이 둘인 이유 (#346): `DAENGS_DATABASE_URL` 은 `daengs_backend.config` 가 보면
    # 기동을 거부하는 이름이다 (D-013). 같은 pytest 프로세스에 두 설정이 함께 뜨는 CI 는
    # place 전용 이름 `DAENGS_PLACE_DATABASE_URL` 로 준다. 옛 이름은 상류(UPSTREAM.md)와
    # 로컬 한 줄 설정을 위해 남긴다 — 둘 다 있으면 place 전용 이름이 이긴다.
    database_url: str | None = Field(
        None,
        validation_alias=AliasChoices("DAENGS_PLACE_DATABASE_URL", "DAENGS_DATABASE_URL"),
    )
    db_host: str = "localhost"
    db_port: int = Field(5432, ge=1, le=65535)
    db_user: str = "daengs"
    db_password: str = "daengs"
    db_name: str = "daengs"

    @property
    def sqlalchemy_url(self) -> URL:
        if self.database_url:
            return make_url(self.database_url)
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    # 행정안전부 동물병원/동물약국 인허가 데이터 (data.go.kr) — 배치 전용
    data_go_kr_service_key: str = ""       # 일반(Decoding) 인증키 권장
    mois_page_size: int = Field(100, ge=1, le=100)
    mois_sync_overlap_days: int = Field(3, ge=0, le=30)

    # 한국관광공사 반려동물 동반여행 (KorPetTourService2) — 기반층 두 번째 원천, 배치 전용
    kto_service_key: str = ""              # 일반(Decoding) 인증키
    kto_page_size: int = Field(100, ge=1, le=1000)

    # Place 내부 discovery 전용. 키가 비어도 앱 import·health·기존 검색은 정상이어야 한다.
    # 배포 중인 공용 이름을 각 프로세스 Settings가 독립적으로 읽으며, timeout 단위는 ms다.
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("GEMINI_API_KEY"),
    )
    gemini_model: str = Field(
        default="gemini-3.1-flash-lite",
        min_length=1,
        validation_alias=AliasChoices("GEMINI_MODEL"),
    )
    gemini_timeout_ms: int = Field(
        default=30_000,
        gt=0,
        le=120_000,
        validation_alias=AliasChoices("GEMINI_TIMEOUT_MS"),
    )


settings = Settings()
