from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.geo.icons import IconGroup, icon_group

Kind = Literal["hospital", "pharmacy"]


class SearchParams(BaseModel):
    """챗봇 parse()와 메뉴 UI가 공통으로 채우는 검색 파라미터. docs/03."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(2000, ge=100, le=20000)
    kind: Kind | None = None            # None = 둘 다
    open_now: bool = False
    night: bool = False                 # 야간 표방 **우선** (필터 아님 — 결정 #20)
    emergency: bool = False             # 응급 표방 우선. resolver 쪽 emergency_service 와 같은 뜻
    limit: int = Field(20, ge=1, le=100)
    at: datetime | None = None          # 판정 기준 시각. None = 서버 now


class PlaceOut(BaseModel):
    id: int
    kind: Kind
    name: str
    lat: float
    lng: float
    distance_m: int
    address: str | None
    phone: str | None
    is_night: bool
    is_24h: bool
    open_now: bool | None               # None = 영업시간 미상
    hours_today: list[tuple[str, str]] | None
    tags: list[str] = Field(default_factory=list)
    area_m2: float | None = None        # 인허가 면적 — 표시만
    staff_count: int | None = None      # 인허가 종사자수 — 표시만
    hours_text: str | None = None       # 기반층에서 빌린 운영시간 원문 — 표시만, 판정 아님
    closed_days: str | None = None
    hours_source: dict | None = None    # {"name": 원천, "as_of": 기준일} — 빌린 값엔 딱지
    prefer_hit: list[str] = Field(default_factory=list)  # 선호 조건과 태그 교집합 — 부스트 근거
    boost: int = 0                      # 선호 적중 부스트. 거리 밴드 안에서만 순서를 바꾼다
    # 새 Place adapter가 쓰는 내부 provenance. exclude라 기존 의료 API JSON은 바뀌지 않는다.
    source: str | None = Field(None, exclude=True, repr=False)
    source_ref: str | None = Field(None, exclude=True, repr=False)
    source_updated_at: datetime | None = Field(None, exclude=True, repr=False)
    active: bool = Field(True, exclude=True, repr=False)
    license_status_code: str | None = Field(None, exclude=True, repr=False)
    license_status_name: str | None = Field(None, exclude=True, repr=False)
    hours_source_ref: str | None = Field(None, exclude=True, repr=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def icon_group(self) -> IconGroup:
        """지도 마커 그룹. /facility 의 같은 이름 필드와 한 어휘를 쓴다.

        kind 에서 파생되므로 저장·입력값이 아니다 — 계산 필드로 두어 두 값이 어긋날 수 없게 한다.
        """
        return icon_group(self.kind)


class MapOut(BaseModel):
    preview_url: str | None
    deeplink: str
    web_url: str


class SearchOut(BaseModel):
    params: SearchParams
    results: list[PlaceOut]
    map: MapOut
