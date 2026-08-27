"""계획 타입의 계약. 값 검사가 아니라 **경계**를 지킨다.

계획을 나눈 목적은 엔진이 남의 축을 못 보게 하는 것이다. 그 목적은 필드 목록으로만
지켜지므로, 여기서 필드 목록을 고정한다 — 나중에 누가 편의로 JourneyPlan 에
`require_tags` 를 하나 얹으면 그 순간 경계가 사라지고, 그건 리뷰에서 잘 안 보인다.
"""

import dataclasses
from datetime import UTC, datetime

import pytest

from app.discovery.plans import ViewPlan
from app.discovery.trace import ResolutionTrace
from app.geo.contract import SearchMust, SearchPlan, SearchPrefer
from app.journey.contract import JourneyPlan

NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
MUST = SearchMust(lat=37.4979, lng=127.0276, radius_m=2000, judge_at=NOW)


def _fields(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_search_plan_cannot_see_how_we_travel():
    """검색이 이동수단을 보면 '차로 가니까 멀어도 된다' 같은 판정이 검색에 스며든다."""
    leaked = _fields(SearchMust) & {"mode_priority", "walk", "companion", "max_total_min"}
    assert not leaked, leaked


def test_journey_plan_cannot_see_what_we_are_looking_for():
    """경로가 필터를 보면 결과를 빼기 시작한다 — 그건 target 의 권한이다."""
    leaked = _fields(JourneyPlan) & {"require_tags", "exclude_ids", "prefer", "open_now", "limit"}
    assert not leaked, leaked


def test_view_plan_changes_neither_set_nor_route():
    leaked = _fields(ViewPlan) & {"radius_m", "require_tags", "mode_priority", "walk"}
    assert not leaked, leaked


def test_plans_are_frozen():
    """엔진이 받은 계획을 실행 중에 고치면 추적이 끊긴다. 계획은 읽기 전용이다."""
    plan = SearchPlan(must=MUST)
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.must.radius_m = 5000       # type: ignore[misc]


def test_prefer_is_separate_from_must():
    """같은 상자에 있으면 '거르는 것'과 '올리는 것'이 다시 섞인다."""
    plan = SearchPlan(must=MUST, prefer=SearchPrefer(tags=("emergency", "24h")))
    assert "emergency" not in _fields(SearchMust)
    assert plan.prefer.confidence == "name_regex", "신뢰도를 안 달면 나중에 능력처럼 쓰인다"


# ------------------------------------------------------------------ trace
def test_trace_separates_overrides_from_ordinary_decisions():
    """상황이 사용자 설정을 **누른 것**만 따로 뽑힌다 — 이건 화면에 반드시 나가야 한다."""
    t = ResolutionTrace()
    t.note("journey", "수단 우선순위 car>walk", because="urgency=urgent")
    t.note("journey", "도보 20분 제한 무시", because="urgency=urgent", overrode="walk.max_walk_min")
    assert [e.overrode for e in t.overrides()] == ["walk.max_walk_min"]
    assert len(t.entries) == 2


def test_trace_groups_by_axis():
    t = ResolutionTrace()
    t.note("context", "급한 상황")
    t.note("target", "반경 2km")
    assert t.by_axis() == {"context": ["급한 상황"], "target": ["반경 2km"]}
