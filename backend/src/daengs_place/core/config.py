"""Place 검색이 실제로 소비하는 설정만. **provider·LLM·deeplink 설정은 여기 없다.**

원본(DAENGS_geo `app/core/config.py`)은 지도/route/LLM provider 키까지 한 덩어리였다.
이 서비스의 약속은 "PostGIS 만 있으면 뜬다"(D-026)라서, 설정 표면도 그 약속만큼만 둔다 —
여기 없는 필드를 다시 들여오는 변경은 경계 침범이다 (tests/test_boundary.py).
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DAENGS_", extra="ignore")

    # 로컬·CI의 기존 한 줄 설정은 유지하되, compose는 아래 조각을 넘긴다. 비밀번호를
    # URL 문자열에 이어 붙이면 @ / # 같은 예약 문자가 사용자·호스트 경계를 깨뜨린다.
    database_url: str | None = None
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


settings = Settings()
