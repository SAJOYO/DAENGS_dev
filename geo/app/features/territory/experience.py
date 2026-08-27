"""화면이 쓸 질의 조합 — 저녁 산책 직전 장면 하나를 숫자로 만든다.

갈래는 [experience-scenario](../../../docs/explorations/walk/experience-scenario.md).
장면의 다섯 비트 중 3·4·5 번이 여기서 나온다.

    3. 조건을 바꾸면 지도가 바뀐다          조건별 Layer
    4. 영역을 탭하면 방문률·조건 대비·추세   RegionStats

이 파일은 파이프라인의 **Instruments** 단이다 — 읽기 장치까지 만들고 멈춘다. 그 위의
근거(Evidence)와 고르기(Judgment)는 `evidence.py` 다. 처음엔 여기서 카드까지 골랐는데
(`pick_cards`), 고르기가 그 자체로 장치라서 갈랐다.

## 왜 `app/geo` 가 아닌가

`region_visit_rate` 같은 순수 공간 연산은 geo 것이다. 그런데 **"익숙함이란 무엇인가" 는
공간 진실이 아니라 제품 가설**이다. 지금은

    조건 편향 = 이 조건 방문률이 전체보다 높다
    추세      = 최근 30 일이 그 앞 30 일과 다르다
    미개척    = 전체 방문률이 문턱 아래다

로 두지만 나중에 recency·체류·계절 안정성으로 바뀔 수 있다. geo 가 이 정의를 소유하면
가설을 바꿀 때 공간 연산까지 흔들린다. 화살표는 #67 그대로 — 응용이 도메인을 내려다본다.

## 결정론

`now` 를 **인자로 받는다.** 페르소나 자료는 합성 1 년치라 "최근 30 일" 의 기준점이 없으면
같은 입력이 매번 다른 답을 낸다. 장면의 완료 조건이 "deterministic JSON 하나" 라서
시계를 읽는 곳이 하나도 없어야 한다.

## 비율은 분자·분모와 함께 낸다

조건을 겹치면(저녁 ∩ 최근 30 일) 분모가 금방 한 자리로 떨어진다. `5/7` 과 `17/24` 는
화면에서 같은 71% 지만 믿을 값이 아니다. 그래서 `VisitRate` 를 그대로 들고 다니고
화면까지 넘긴다.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.features.territory.layers import (
    Aggregation,
    LayerSpec,
    Projection,
    Selector,
    VisitRate,
    derive_tags,
    region_visit_rate,
)
from app.features.territory.paint import Cellophane
from app.features.territory.region import Region

EXPERIENCE_VERSION = 1
RECENT_DAYS = 30

# 추세라고 부르기 위한 최소 표본. **근거 있는 문턱이 아니라 화면용 잠정값이다** — 실사용
# 데이터가 0 이라 정할 근거가 없다(#57 이 보관 일수를 안 정한 것과 같은 이유). 그래서 숨기지
# 않고 JSON 에 실어, 화면이 "추세" 대신 "표본 부족" 을 말할 수 있게 한다.
TREND_MIN_WALKS = 5

# 이 아래면 "안 가봤다" 로 본다. 역시 잠정값이고 JSON 에 실어 보낸다.
UNEXPLORED_CUT = 0.05

# 화면의 조건 칩. 이 순서가 화면 순서다.
CHIPS: tuple[tuple[str, str], ...] = (
    ("all", "전체"),
    ("recent", "최근 30일"),
    ("morning", "아침"),
    ("evening", "저녁"),
    ("spring", "봄"),
    ("summer", "여름"),
    ("autumn", "가을"),
    ("winter", "겨울"),
)
_SEASONS = ("spring", "summer", "autumn", "winter")


def recent_window(today: date) -> tuple[date, date]:
    """"최근 30 일" 이 뜻하는 정확한 날짜 범위. **오늘 포함 30 개 날짜.**

    두 가지를 여기 한 곳에 모아 둔다.

    **`until` 을 빼먹으면 미래까지 삼킨다.** `now` 가 자료 끝보다 뒤일 때는 티가 안 나다가,
    자료 한가운데를 "지금" 으로 잡는 순간 최근 30 일이 **261 회**가 됐다. 창은 창이어야 한다.

    **그리고 `today - 30` 은 30 일이 아니라 31 일이다.** 양끝을 다 세니까 `7/27 … 8/26` 이
    31 개다. 앞 창은 30 개였으므로 둘을 견주는 추세가 조용히 31 대 30 이었다. 화면이
    "최근 30 일" 이라고 **말하는** 순간 그건 계약이라 맞아야 한다.
    """
    return today - timedelta(days=RECENT_DAYS - 1), today


def previous_window(today: date) -> tuple[date, date]:
    """그 앞 30 일. `recent_window` 바로 앞에 붙고 겹치지 않는다."""
    recent_since, _ = recent_window(today)
    return recent_since - timedelta(days=RECENT_DAYS), recent_since - timedelta(days=1)


def chip_selector(chip: str, today: date) -> Selector:
    """칩 이름 하나 → `Selector`. 화면과 질의가 같은 사전을 쓰게 하려고 여기 둔다."""
    if chip == "all":
        return Selector.of()
    if chip == "recent":
        since, until = recent_window(today)
        return Selector.of(since=since, until=until)
    if chip in ("morning", "evening"):
        return Selector.of(time_band=chip)
    if chip in _SEASONS:
        return Selector.of(season=chip)
    raise ValueError(f"모르는 칩: {chip}")


@dataclass(frozen=True)
class NamedRegion:
    """후보 영역 하나. 이름은 **사람이 붙인다** — 자동 분할은 이번 범위 밖이다."""

    region: Region
    name: str


@dataclass(frozen=True)
class Trend:
    """최근 30 일 방문률 − 그 앞 30 일 방문률.

    두 창의 분자·분모를 다 들고 다닌다. 추세는 **차이보다 표본이 먼저** 읽혀야 하는 값이라서다 —
    `1/2 → 2/2` 도 +0.5 고 `40/80 → 80/80` 도 +0.5 지만 둘은 같은 얘기가 아니다.
    """

    recent: VisitRate
    previous: VisitRate

    @property
    def delta(self) -> float | None:
        """두 창 중 한쪽이라도 산책이 없으면 **추세가 없다.** 0 이 아니다.

        `VisitRate.rate` 가 `0/0` 에서 `None` 을 주는 것과 같은 이유다 — "안 갔다" 와
        "잴 수 없다" 를 같은 값으로 접으면 화면이 "변화 없음" 이라고 단정하게 된다.
        """
        if self.recent.rate is None or self.previous.rate is None:
            return None
        return self.recent.rate - self.previous.rate

    @property
    def trustworthy(self) -> bool:
        return (self.recent.selected >= TREND_MIN_WALKS
                and self.previous.selected >= TREND_MIN_WALKS)


@dataclass(frozen=True)
class RegionStats:
    """영역 하나를 탭했을 때 나오는 것 전부 (장면 4 번)."""

    region_id: str
    region_version: int
    name: str
    by_chip: dict[str, VisitRate]
    trend: Trend


@dataclass(frozen=True)
class Experience:
    """장면 하나. 이것이 그대로 JSON 이 된다."""

    now: datetime
    context: dict[str, str]
    spec_label: str
    regions: list[RegionStats]
    walks_total: int
    thresholds: dict[str, float]
    version: int = EXPERIENCE_VERSION


def _spec(selector: Selector, projection: Projection, min_peak: float) -> LayerSpec:
    return LayerSpec(selector=selector,
                     aggregation=Aggregation(metric="walks", min_peak=min_peak),
                     projection=projection)


def region_stats(sheets: list[Cellophane], named: NamedRegion, now: datetime,
                 projection: Projection, *, min_peak: float = 0.0) -> RegionStats:
    """영역 하나의 조건별 방문률과 추세 (장면 4 번)."""
    today = now.date()
    by_chip = {
        chip: region_visit_rate(
            sheets, _spec(chip_selector(chip, today), projection, min_peak), named.region)
        for chip, _label in CHIPS
    }
    previous_since, previous_until = previous_window(today)
    previous = Selector.of(since=previous_since, until=previous_until)
    return RegionStats(
        region_id=named.region.id,
        region_version=named.region.version,
        name=named.name,
        by_chip=by_chip,
        trend=Trend(
            recent=by_chip["recent"],
            previous=region_visit_rate(
                sheets, _spec(previous, projection, min_peak), named.region),
        ),
    )


def build(sheets: list[Cellophane], regions: list[NamedRegion], now: datetime,
          projection: Projection, *, context_chip: str = "evening",
          min_peak: float = 0.0) -> Experience:
    """장면 하나를 통째로. 화면은 이 결과만 읽는다."""
    stats = [region_stats(sheets, named, now, projection, min_peak=min_peak)
             for named in regions]
    tags = derive_tags(now)
    return Experience(
        now=now,
        context={"chip": context_chip,
                 "season": tags["season"], "time_band": tags["time_band"]},
        spec_label=_spec(chip_selector(context_chip, now.date()), projection, min_peak).label,
        regions=stats,
        walks_total=len(sheets),
        thresholds={"trend_min_walks": TREND_MIN_WALKS, "unexplored_cut": UNEXPLORED_CUT,
                    "recent_days": RECENT_DAYS, "min_peak": min_peak},
    )
