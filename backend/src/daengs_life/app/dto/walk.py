"""`GET /walk` 의 공개 계약 (RT-001 ⑥ · RAG-027 의 V 층).

**도메인 타입을 그대로 내보내지 않는 이유가 이 파일의 존재 이유다.** `Verdict` 를 직렬화해서
보내면 ②-d(값의 표현)를 손대는 날 API 가 같이 깨진다 — `Interval` 을 다른 모양으로 바꾸거나
`Q` 를 하나 더 넣는 것은 도메인의 자유여야 하고, 그 자유가 클라이언트로 새면 안 된다.

⑥ 이 못 박은 것 넷을 여기서 지킨다:

  `grade` 는 **문자열**이고 판정 불가는 `"unknown"`   `null` 은 "필드 없음"과 헷갈린다
  `label` 은 **서버가 만든다**                        "○○동 (측정소: △△) 기준" 정직 표기는
                                                      보장돼야 하는 것이라 클라이언트에 안 맡긴다
  `now` 에만 상세 `basis`                             타임라인 25개에 근거를 다 실으면 몇 배가 된다
  `sources` 를 노출한다                               저하된 이유를 사용자가 볼 수 있어야 한다 (②-e)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 판정 불가를 `null` 이 아니라 이 문자열로 낸다 (⑥).
GradeName = Literal["UNSAFE", "CAUTION", "GOOD", "unknown"]


class BasisValue(BaseModel):
    """근거가 된 측정값 하나. `Measurement` 의 **공개 가능한 부분만**."""

    quantity: str
    value: float | str
    unit_note: str | None = None     # 구간·코드는 숫자가 아니라 라벨로 나간다 ('강수없음')
    grade: int | None = None         # API 가 준 공식 등급. 없으면 결함이 아니다 (②-d-2)
    source: str
    spatial_ref: str
    valid_at: datetime
    issued_at: datetime


class BasisState(BaseModel):
    """근거가 된 상태 하나 — 특보·대기질 예보 (②-a). 값이 아니라 구간 + 범주다."""

    kind: str
    category: str
    area: str
    valid_from: datetime
    valid_to: datetime | None = None
    issued_at: datetime


class AxisOut(BaseModel):
    grade: GradeName
    note: str = ""
    basis: list[BasisValue | BasisState] = Field(default_factory=list)
    derived: dict[str, float] = Field(default_factory=dict)   # 체감온도 등 우리가 계산한 값


class VerdictOut(BaseModel):
    """`now` — 유일하게 상세 근거를 싣는 자리."""

    at: datetime
    grade: GradeName
    dominant: list[str] = Field(default_factory=list)
    axes: dict[str, AxisOut] = Field(default_factory=dict)
    unknown_axes: list[str] = Field(default_factory=list)
    capped: bool = False
    uv: AxisOut | None = None        # 부가 표시. **등급에 영향 없음** (③-b ③)


class TimelinePoint(BaseModel):
    """T+0 ~ T+24h, 1시간 간격. **가볍게** — 근거는 안 싣는다 (⑥)."""

    at: datetime
    grade: GradeName
    dominant: list[str] = Field(default_factory=list)


class WindowOut(BaseModel):
    """산책 권장 구간. **이 응답의 실제 가치다** — ③-a 를 시각의 함수로 만든 이유가 여기서 회수된다.

    `to` 는 **마지막으로 좋은 시각**이다 (`rules.windows`). 그다음 판정까지 좋다고 말하면
    없는 것을 지어낸다.
    """

    from_: datetime = Field(alias="from")      # `from` 은 파이썬 예약어라 별칭으로 낸다
    to: datetime
    grade: GradeName

    model_config = {"populate_by_name": True}


class LocationOut(BaseModel):
    dong: str
    grid: tuple[int, int]
    air_station: str | None = None
    air_station_km: float | None = None
    aws_station: str | None = None
    warning_zone: str | None = None
    label: str                                  # 서버가 만든 정직 표기


class SourceOut(BaseModel):
    """②-e `ProviderResult` 그대로. 저하 이유가 사용자에게 보이는 자리."""

    provider: str
    ok: bool
    stale: bool = False
    reason: str | None = None


class WalkOut(BaseModel):
    """`GET /walk` 응답 전체. 엔드포인트가 **하나**이고 타임라인째로 준다 (⑥).

    `now` 만 필요한 클라이언트는 `timeline` 을 무시하면 된다. 시각마다 부르게 만들면
    **추가 API 호출은 0인데 왕복만 24번** 는다.
    """

    location: LocationOut
    generated_at: datetime
    now: VerdictOut
    timeline: list[TimelinePoint] = Field(default_factory=list)
    windows: list[WindowOut] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = ["AxisOut", "BasisState", "BasisValue", "GradeName", "LocationOut", "SourceOut",
           "TimelinePoint", "VerdictOut", "WalkOut", "WindowOut"]
