from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DAENGS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://daengs:daengs@localhost:5432/daengs"

    # 지도 제공사 — docs/provider-assembly.md. 정적 지도와 지오코딩을 따로 고를 수 있다.
    map_provider: Literal["kakao", "naver", "fake", "none"] = "none"
    static_map_provider: Literal["kakao", "naver", "fake", "none"] | None = None  # None → map_provider 따름
    geocode_provider: Literal["kakao", "naver", "fake", "none"] | None = None

    # 경로 — 모드별 제공사. 도보 장애물은 TMAP만 줌 (docs/research/2026-08-19-route-apis.md)
    walk_route_provider: Literal["tmap", "fake", "none"] = "fake"
    car_route_provider: Literal["kakao", "naver", "fake", "none"] = "fake"
    transit_route_provider: Literal["kakao", "fake", "none"] = "fake"
    route_top_n: int = 5              # 실측 호출은 상위 N개만, 나머지 휴리스틱

    # LLM — utterance 있을 때만 호출. fake = 규칙 기반
    llm_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # 실제 외부 호출은 기본 거부. dev는 같은 Usage Gate 안에서 작은 프로세스별 한도만 허용한다.
    usage_policy: Literal["deny-all", "dev"] = "deny-all"

    kakao_rest_key: str = ""
    tmap_app_key: str = ""
    naver_ncp_key_id: str = ""
    naver_ncp_key: str = ""

    # 행정안전부 동물병원/동물약국 인허가 데이터 (data.go.kr)
    data_go_kr_service_key: str = ""       # 일반(Decoding) 인증키 권장
    mois_page_size: int = Field(100, ge=1, le=100)
    mois_sync_overlap_days: int = Field(3, ge=0, le=30)

    # 한국관광공사 반려동물 동반여행 (KorPetTourService2) — 기반층 두 번째 원천
    kto_service_key: str = ""              # 일반(Decoding) 인증키
    kto_page_size: int = Field(100, ge=1, le=1000)

    # 검색 기본값
    default_radius_m: int = Field(2000, ge=100, le=20000)
    max_radius_m: int = 10000
    default_limit: int = 20

    # /dev 검증 콘솔. 앱 UI 가 아니라 내부 검증용 표면이라 출고 기본값은 닫혀 있다.
    # 개발에서는 .env 의 DAENGS_DEV_CONSOLE=true 로 연다 (.env.example 에 들어 있다).
    dev_console: bool = False

    # 딥링크
    app_scheme: str = "daengs"
    web_map_base: str = "https://daengs.example/map"


settings = Settings()
