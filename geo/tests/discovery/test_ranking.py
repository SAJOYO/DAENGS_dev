"""선호 규칙과 부스트 순위 — **두 진입 경로가 같은 뜻을 갖는다** (이슈 #24).

`/places`·`/pharmacy`(SearchParams)와 `/hospital`(EditableState→resolver)이
같은 조건에서 같은 선호 태그를 만들고, 부스트가 두 쪽 모두 실제 순위에 반영되며,
거리 밴드는 넘지 못한다는 것을 고정한다.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.discovery.facts import RuntimeFacts
from app.discovery.resolver import resolve_request
from app.discovery.state import EditableState
from app.geo.ranking import (
    DISTANCE_BAND_M,
    band_boost_sorted,
    band_of,
    prefer_boost,
    preference_tags,
)
from app.geo.schemas import SearchParams
from app.geo.search import search_places
from tests.conftest import TEST_ORIGIN, TEST_SOURCE, place_row, seeded_places

AT = datetime(2026, 8, 25, 22, 0, tzinfo=UTC)


# ------------------------------------------------------------------ 의미 규칙 하나
def test_night_and_emergency_expand_to_the_same_tags():
    assert preference_tags(night=True) == ("24h", "emergency", "night")
    assert preference_tags(emergency=True) == ("24h", "emergency")
    assert preference_tags() == ()


def test_both_entry_paths_derive_identical_preference_tags():
    """SearchParams 세계와 EditableState 세계가 같은 조건 → 같은 태그."""
    simple = preference_tags(night=True, emergency=True)

    state = EditableState.model_validate({
        "lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1],
        "target": {"night_service": True, "emergency_service": True},
    })
    resolved = resolve_request(
        state, RuntimeFacts(now=AT, profile=None, owner=None), kind="hospital",
        companion="dog", measured=False, transport_available=False,
    )
    assert tuple(resolved.search.prefer.tags) == simple


# ------------------------------------------------------------------ 순위 규칙 하나
def test_boost_reorders_inside_a_band_but_never_across():
    items = [
        {"d": 100, "b": 0},      # 같은 밴드(0~499): 부스트가 순서를 바꾼다
        {"d": 300, "b": 4},
        {"d": 700, "b": 6},      # 다음 밴드: 부스트가 커도 앞 밴드를 못 넘는다
    ]
    ranked = band_boost_sorted(items, distance_of=lambda i: i["d"], boost_of=lambda i: i["b"])
    assert [i["d"] for i in ranked] == [300, 100, 700]


@pytest.mark.parametrize(("distance", "expected"), [(0, 0), (499, 0), (500, 1), (1200, 2)])
def test_band_of_distance(distance, expected):
    assert band_of(distance, DISTANCE_BAND_M) == expected


def test_prefer_boost_counts_two_per_hit():
    assert prefer_boost([]) == 0
    assert prefer_boost(["24h", "night"]) == 4


# ------------------------------------------------------------------ 실제 검색 경로
async def test_night_actually_changes_generic_search_order():
    """#24 의 증상: night=true 가 prefer_hit 만 붙이고 순서를 안 바꿨다."""
    rows = [
        place_row("plain", "가까운동물병원", east_m=100),
        place_row("night", "24시야간동물병원", east_m=300, tags=("24h", "night")),
    ]
    async with seeded_places(rows) as session:
        ids = {r.name: r.id for r in await session.execute(text(
            "SELECT id, name FROM place WHERE source = :s"), {"s": TEST_SOURCE})}
        base = SearchParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=1000,
                            kind="hospital", at=AT)

        plain = await search_places(session, base)
        assert [r.id for r in plain.results][:2] == [ids["가까운동물병원"], ids["24시야간동물병원"]]

        preferred = await search_places(session, base.model_copy(update={"night": True}))
        assert [r.id for r in preferred.results][:2] == [
            ids["24시야간동물병원"], ids["가까운동물병원"]
        ], "night=true 인데 순서가 그대로다 (#24)"
        top = preferred.results[0]
        assert top.prefer_hit == ["24h", "night"] and top.boost == 4


async def test_preference_cannot_pull_a_far_place_over_a_near_one():
    """밴드를 넘는 역전 금지 — 결정 #20. 부스트는 '살짝 위'까지다."""
    rows = [
        place_row("near", "가까운동물병원", east_m=100),
        place_row("far", "24시야간동물병원", east_m=900, tags=("24h", "night", "emergency")),
    ]
    async with seeded_places(rows) as session:
        out = await search_places(session, SearchParams(
            lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000, kind="hospital",
            night=True, at=AT,
        ))
        assert [r.name for r in out.results][:2] == ["가까운동물병원", "24시야간동물병원"]
