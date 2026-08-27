"""PostGIS를 실제로 태우는 검색 테스트. DB 없으면 skip. 장치는 conftest.

**왜 필요한가**: `tags && / @>` 는 순수 파이썬 테스트로 절대 안 잡힌다. 실제로
`ARRAY(String)`(→ VARCHAR[])과 DB의 TEXT[]가 안 맞아서 emergency·require_tags 필터가
통째로 죽어 있었는데, 단위 테스트 68개가 전부 통과했다.

아래 `ROWS`는 **공공데이터 적재 모양**을 흉내낸다 — hours=NULL, is_night/is_24h=false,
tags는 이름에서만. 인허가 원천이 영업시간을 안 주기 때문이다.
"""

from datetime import UTC, datetime

import pytest

from app.discovery.facts import RuntimeFacts
from app.discovery.resolver import resolve_request
from app.discovery.state import EditableState
from app.geo.search import find_places
from tests.conftest import TEST_ORIGIN, daily_hours, place_row, seeded_places

# 이 파일이 소유하는 시나리오. 아래 assert 들이 정확히 이 6행에 결합돼 있으므로
# 공용으로 올리지 않는다 — 남이 행을 하나 추가하면 정렬·집합 단언이 조용히 깨진다.
ROWS = [
    place_row("t1", "가까운동물병원", east_m=200),                     # 미상, 제일 가까움
    place_row("t2", "테헤란24시동물병원", east_m=400, tags=["24h"]),
    place_row("t3", "논현야간동물병원", east_m=600, tags=["night"]),
    place_row("t4", "대치응급동물의료센터", east_m=800, tags=["emergency", "center"]),
    place_row("t5", "주간만하는동물병원", east_m=1000,                  # 밤엔 확정 영업종료
              hours=daily_hours(("09:00", "18:00"))),
    place_row("t6", "밤에도하는동물병원", east_m=1200,                  # 확정 영업중, 제일 멂
              hours=daily_hours(("00:00", "23:59"))),
]

AT_NIGHT = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)   # 한국 21:00


async def _search(db, **kw):
    state = EditableState(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1])
    state.target.radius_m = 5000
    state.target.limit = 20
    state.target.open_now = kw.pop("open_now", False)
    state.target.night_service = kw.pop("night", False)
    state.target.emergency_service = kw.pop("emergency", False)
    state.target.require_tags = kw.pop("require_tags", [])
    assert not kw, kw
    plan = resolve_request(
        state, RuntimeFacts(now=AT_NIGHT), kind="hospital", companion="dog", measured=False,
    )
    return await find_places(db, plan.search)


# --------------------------------------------- 타입 회귀 (이 필터들이 죽어 있었다)
@pytest.mark.parametrize("kw", [{"emergency": True}, {"require_tags": ["24h"]}, {"night": True}])
async def test_tag_filters_do_not_blow_up_on_postgres(kw):
    """tags 연산자가 TEXT[] 로 나가는지. ARRAY(String)이면 여기서 ProgrammingError."""
    async with seeded_places(ROWS) as db:
        await _search(db, **kw)


# --------------------------------------------- 미상은 제외하지 않는다
async def test_open_now_keeps_unknown_hours():
    """공공데이터엔 영업시간이 없다. 미상을 빼면 결과가 통째로 사라진다."""
    async with seeded_places(ROWS) as db:
        names = [p.name for p in await _search(db, open_now=True)]
    assert "가까운동물병원" in names, "영업시간 미상이 제외됐다 — 실데이터에선 전멸한다"
    assert "주간만하는동물병원" not in names, "확정 영업종료는 빠져야 한다"


async def test_open_now_puts_confirmed_before_unknown():
    """확정 영업중이 먼저, 미상은 뒤로. 각 묶음 안에서는 거리순."""
    async with seeded_places(ROWS) as db:
        results = await _search(db, open_now=True)

    assert [p.open_now for p in results] == sorted(
        (p.open_now for p in results), key=lambda f: f is not True)

    confirmed = [p for p in results if p.open_now is True]
    unknown = [p for p in results if p.open_now is None]
    assert confirmed and unknown, "혼합 데이터가 아니면 이 테스트가 무의미하다"
    # 더 가까운 미상(t1, 200m)이 있어도 확정(t6, 1200m)이 앞선다
    assert confirmed[0].distance_m > unknown[0].distance_m
    for group in (confirmed, unknown):
        assert [p.distance_m for p in group] == sorted(p.distance_m for p in group)


# --------------------------------------------- 이름 태그는 순위지 필터가 아니다
async def test_night_prefers_name_tags_without_removing_the_rest():
    """인허가 원천은 is_night/is_24h 를 안 준다 — 이름 태그가 유일한 재료다.

    그 재료가 너무 얇아서 필터로 쓰면 검색이 죽는다: 실측 2026-08-20 활성 병원
    5,457곳 중 night 태그 **1곳** · emergency **2곳**. 그래서 거르지 않고 위로 올린다.
    """
    async with seeded_places(ROWS) as db:
        results = await _search(db, night=True)
    names = [p.name for p in results]
    assert "가까운동물병원" in names, "태그 없는 곳이 사라졌다 — 실데이터에선 전멸한다"
    tagged = {p.name for p in results if p.prefer_hit}
    assert tagged == {"테헤란24시동물병원", "논현야간동물병원", "대치응급동물의료센터"}, tagged


async def test_emergency_does_not_wipe_the_result_set():
    """"급해요" 한마디에 반경 안 병원이 사라지면 안 된다. 응급일수록 더더욱."""
    async with seeded_places(ROWS) as db:
        results = await _search(db, emergency=True)
    names = [p.name for p in results]
    assert "가까운동물병원" in names
    assert {p.name for p in results if p.prefer_hit} == {"테헤란24시동물병원",
                                                        "대치응급동물의료센터"}
