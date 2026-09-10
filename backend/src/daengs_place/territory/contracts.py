"""앱이 읽는 중립 점령지 계약. 적재 원천과 시설 종류는 의도적으로 숨긴다."""

from pydantic import BaseModel, ConfigDict


class TerritorySite(BaseModel):
    """지도에서 찾아갈 수 있는 하나의 중립 점령지."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str
    lat: float
    lng: float
    distance_m: float


class TerritorySitePage(BaseModel):
    """현재 위치 주변의 점령지. 상한으로 잘렸는지를 반드시 함께 말한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    truncated: bool
    sites: tuple[TerritorySite, ...]


class TerritorySiteLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    site_id: str
    lat: float
    lng: float


class TerritorySiteLocations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sites: tuple[TerritorySiteLocation, ...]
