"""조건 판정이 후보를 지우거나 순서를 바꾸지 않는가 — 실DB 관통.

Decision: #70, #68

단위 테스트(`tests/place/test_restriction_projection.py`)는 투영 함수 하나를 본다.
여기서 지키는 것은 **검색 전체의 불변식**이다.

  후보 보존   `incompatible` 이어도 결과에서 사라지지 않는다. 사용자가 hard filter 를
              명시하기 전에는 세 상태를 다 보존한다 (결정 #68)
  순서 보존   판정은 거리순을 바꾸지 않는다
  개별화      같은 시설이 개에 따라 다른 칩·다른 판정을 받는다
"""

import json

from sqlalchemy import text

from app.place.restriction_map import derive
from app.place.search import PlaceSearchRequest, search_place_groups
from tests.conftest import TEST_ORIGIN, db_session

SOURCE = "kcisa"
REFS = ("test:proj:big-no", "test:proj:muzzle", "test:proj:plain")

# 파생 자체는 `test_restriction_derive.py` 가 본다. 여기서는 배치를 돌리지 않고
# `derive()` 결과를 직접 넣는다 — `derive_all` 은 KCISA 전량(23,914행)을 훑어서
# 이 테스트에 2분을 더한다.
_INSERT = text("""
INSERT INTO facility
    (name, kind, category3, address, source, source_ref, pet, location, snapshot, last_written,
     restriction_state, restriction_parse_state, restriction_predicates,
     restriction_semantics_version)
VALUES (:name, 'cafe', '카페', '테스트', :source, :ref, CAST(:pet AS jsonb),
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, 'test', NULL,
        :restriction_state, :restriction_parse_state,
        CAST(:restriction_predicates AS jsonb), :restriction_semantics_version)
""")

_DELETE = text("DELETE FROM facility WHERE source_ref = ANY(:refs)")


def _lng(east_m: int) -> float:
    return TEST_ORIGIN[1] + east_m / 91_000.0


async def _seed(session) -> None:
    await session.execute(_DELETE, {"refs": list(REFS)})
    rows = [
        (REFS[0], "대형견불가카페", "목줄 필수, 대형견 입장 불가, 1층 및 야외 테라스 가능", 100),
        (REFS[1], "입마개카페", "대형견 입마개, 목줄", 200),
        (REFS[2], "조건없는카페", "제한사항 없음", 300),
    ]
    for ref, name, restrictions, east_m in rows:
        columns = derive(restrictions).to_columns()
        columns["restriction_predicates"] = json.dumps(
            columns["restriction_predicates"], ensure_ascii=False
        )
        await session.execute(_INSERT, {
            "name": name, "source": SOURCE, "ref": ref,
            "pet": json.dumps({"restrictions": restrictions}, ensure_ascii=False),
            "lat": TEST_ORIGIN[0], "lng": _lng(east_m),
            **columns,
        })
    await session.commit()


async def _search(session, conditions):
    request = PlaceSearchRequest(
        lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000,
        kinds=["cafe"], limit_per_kind=50, conditions=conditions,
    )
    response = await search_place_groups(session, request)
    return {
        hit.place.name: hit for hit in response.groups[0].results
        if hit.place.name in {"대형견불가카페", "입마개카페", "조건없는카페"}
    }


async def test_incompatible_places_stay_in_the_results():
    """**후보를 지우지 않는다.** 결정 #68 의 3상태 보존이 제약 축에도 적용된다."""
    async with db_session() as session:
        await _seed(session)
        large = await _search(session, {"dog_size": "large", "dog_weight_kg": 34.0})

        assert set(large) == {"대형견불가카페", "입마개카페", "조건없는카페"}
        verdict = large["대형견불가카페"].evaluations.restrictions
        assert verdict.state == "incompatible"
        assert verdict.reason == "size_denied"

        await session.execute(_DELETE, {"refs": list(REFS)})
        await session.commit()


async def test_the_same_place_reads_differently_for_two_dogs():
    """같은 시설이 개에 따라 다른 칩과 다른 판정을 받는다."""
    async with db_session() as session:
        await _seed(session)
        small = await _search(session, {"dog_size": "small", "dog_weight_kg": 2.0})
        large = await _search(session, {"dog_size": "large", "dog_weight_kg": 34.0})

        # 크기 배제 칩은 빠지지만 원문을 일부만 읽었으므로 가능하다고 확정하지 않는다.
        assert small["대형견불가카페"].evaluations.restrictions.state == "unknown"
        assert large["대형견불가카페"].evaluations.restrictions.state == "incompatible"

        # 입마개 요구: 대형견에게 보이지만 준비 여부를 몰라 가능으로 확정하지 않는다.
        small_chips = {c.code for c in small["입마개카페"].evaluations.restrictions.chips}
        large_chips = {c.code for c in large["입마개카페"].evaluations.restrictions.chips}
        assert "require:muzzle" not in small_chips
        assert "require:muzzle" in large_chips
        assert large["입마개카페"].evaluations.restrictions.state == "unknown"

        await session.execute(_DELETE, {"refs": list(REFS)})
        await session.commit()


async def test_place_facts_keep_every_chip_regardless_of_the_dog():
    """장소 사실은 개와 무관하다. 걸러진 것은 `evaluations` 에만 있다 (결정 #68)."""
    async with db_session() as session:
        await _seed(session)
        small = await _search(session, {"dog_size": "small", "dog_weight_kg": 2.0})

        hit = small["입마개카페"]
        fact_codes = {c.code for c in hit.place.facts.restrictions.chips}
        shown_codes = {c.code for c in hit.evaluations.restrictions.chips}

        assert "require:muzzle" in fact_codes, "장소 사실에서 칩이 사라졌다"
        assert "require:muzzle" not in shown_codes

        await session.execute(_DELETE, {"refs": list(REFS)})
        await session.commit()


async def test_verdict_does_not_reorder_results():
    """판정은 순위 정책이 아니다 — 거리순이 그대로여야 한다."""
    async with db_session() as session:
        await _seed(session)
        plain = await _search(session, None)
        large = await _search(session, {"dog_size": "large", "dog_weight_kg": 34.0})

        assert list(plain) == list(large)

        await session.execute(_DELETE, {"refs": list(REFS)})
        await session.commit()


async def test_no_conditions_means_no_restriction_verdict():
    """개를 안 밝혔으면 판정하지 않는다. 칩은 장소 사실로 그대로 있다."""
    async with db_session() as session:
        await _seed(session)
        plain = await _search(session, None)

        hit = plain["입마개카페"]
        assert hit.evaluations.restrictions is None
        assert {c.code for c in hit.place.facts.restrictions.chips} == {
            "require:muzzle", "require:leash",
        }

        await session.execute(_DELETE, {"refs": list(REFS)})
        await session.commit()
