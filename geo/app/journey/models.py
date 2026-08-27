"""journey 응답 모델 — 공용. 병원행·약국행·산책 코스가 같은 형태를 쓴다.

companion: dog  = 개 동반. 개 계수·spots 노트·advice·대중교통 제한 전부 적용
           none = 사람만. 제공사 시간 그대로, spots는 도착 앵커만, advice 없음 — 그냥 지도앱 수준
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.journey.contract import Companion
from app.providers.base import Mode, RouteStatus


class SpotOut(BaseModel):
    kind: str
    lat: float
    lng: float
    offset_m: int
    text: str
    landmark: str = ""
    road: str = ""
    big_road: bool = False
    length_m: int = 0
    note: str | None = None      # daengs 고유 — 이 개한테 한마디 (companion=dog일 때만)
    warn: bool = False


class Handoff(BaseModel):
    """실제 따라가기는 제공사 앱으로. 딥링크 3종."""
    naver: str
    kakao: str
    tmap: str


class RoadMix(BaseModel):
    """길의 성격 — 큰길(대로/로) 구간 비율. 소음·차·밝기의 대리 지표."""
    big_road_m: int = 0
    total_m: int = 0
    big_ratio: float = 0.0       # 0~1
    big_crossings: int = 0


class Leg(BaseModel):
    """**status 를 먼저 읽어라.** unavailable 이면 min·m 이 null 이다 — 0 이 아니라 없음이다."""

    status: RouteStatus = "estimate"
    status_reason: str | None = None   # estimate 로 강등된 이유, unavailable 인 이유
    min: int | None = None          # companion=dog면 개 계수 적용, none이면 제공사 원값
    provider_min: int | None = None
    m: int | None = None
    source: str = "none"            # estimate | tmap | naver | kakaomobility
    facilities: dict[str, int | float] | None = None
    road_mix: RoadMix | None = None
    taxi_fare: int | None = None
    fare: int | None = None
    advice: str | None = None       # ok | caution | avoid (dog만)
    why: list[str] = Field(default_factory=list)
    spots: list[SpotOut] = Field(default_factory=list)
    polyline: str | None = None     # encoded polyline (precision 5) — 실측일 때
    polyline_points: int = 0
    handoff: Handoff | None = None


class Transport(BaseModel):
    as_of: datetime
    companion: Companion
    straight_m: int
    mode_priority: list[Mode] = Field(default_factory=list)
    walk: Leg | None = None
    car: Leg | None = None
    transit: Leg | None = None
