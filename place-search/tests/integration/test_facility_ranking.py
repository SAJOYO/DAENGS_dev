"""FacilityResolver의 선호 순위 계약.

`tests/discovery/` 에 있었다 — 검증 대상이 `place.facility_resolver`와 `ingest`라 discovery와
무관하다.

부스트 규칙 자체(밴드 안에서만 역전)는 `tests/discovery/test_ranking.py` 가 순수 함수로 고정한다.
여기서 보는 것은 그 규칙이 **시설 검색 한 요청을 통과하는 길**이다 — 순위 키가 SQL 에 있고
표시값은 파이썬이 만들기 때문에, 둘이 어긋나면 순서가 아니라 후보 선택이 틀어진다.

단언은 심은 행만으로 한다. TEST_ORIGIN 에서 실제 공공데이터(울릉도)는 920m 부터라
반경 850m 안은 이 파일이 심은 것뿐이다.
"""

from datetime import UTC, date, datetime

from sqlalchemy import text

from app.ingest.facility_store import upsert_rows
from app.ingest.kcisa import source_ref
from app.ingest.pet_axes import derive_all
from app.place.facility_resolver import FacilityParams, resolve_facilities
from tests.conftest import TEST_ORIGIN, db_session

SOURCES = ("test:rank_base", "test:rank_newer")
SNAPSHOT = "2025-03-24"
RADIUS_M = 850            # 실데이터 최근접 920m 아래. 반경 안은 심은 행만 남는다


def row(name: str, *, east_m: int, **fields) -> dict:
    """시설 한 행. 원점에서 동쪽으로 `east_m` — 밴드는 거리로 정해지니 거리를 테스트가 정한다."""
    lng = TEST_ORIGIN[1] + east_m / 91_000.0
    return {
        "source_ref": source_ref(name, TEST_ORIGIN[0], lng),
        "name": name, "kind": "cafe", "category3": "cafe",
        "sido": None, "sigungu": None, "address": "테스트", "phone": None,
        "homepage": None, "hours_text": None, "closed_days": None,
        "parking": None, "indoor": None, "outdoor": None,
        "lat": TEST_ORIGIN[0], "lng": lng, "last_written": date(2025, 3, 24),
        **fields,
    }


async def _clean(session) -> None:
    for source in SOURCES:
        await session.execute(text("DELETE FROM facility WHERE source = :s"), {"s": source})
    await session.commit()


async def _seed(session, rows: list[dict], *, source: str = SOURCES[0]) -> None:
    await upsert_rows(session, source, rows, SNAPSHOT, datetime.now(UTC))
    await session.commit()
    await derive_all(session, redo=True, sources=SOURCES)


async def _ranked(session, **params) -> list[str]:
    out = await resolve_facilities(
        FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=RADIUS_M, **params),
        session,
    )
    return [r.name for r in out.results]


async def test_boost_reorders_inside_a_band_but_never_across_one():
    """
    Contract: 선호는 필터가 아니라 순위다. 500m 밴드 안에서만 순서를 바꾸고, 밴드를 넘는
              역전은 없다 — 더 먼 밴드의 완벽한 시설이 가까운 밴드를 이기지 못한다.
    Decision: #20
    """
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                row("가까움_주차없음", east_m=100),
                row("같은밴드_주차", east_m=450, parking=True),
                row("다음밴드_주차", east_m=700, parking=True),
            ])
            assert await _ranked(session) == [
                "가까움_주차없음", "같은밴드_주차", "다음밴드_주차",
            ], "선호가 꺼져 있으면 순서는 거리뿐이어야 한다"

            assert await _ranked(session, parking=True) == [
                "같은밴드_주차", "가까움_주차없음", "다음밴드_주차",
            ], "밴드 안에서는 부스트가, 밴드를 넘으면 거리가 이겨야 한다"
        finally:
            await _clean(session)


async def test_preferred_row_survives_a_crowded_band():
    """
    Contract: 선호 시설이 `limit` 보다 뒤 거리에 있어도 같은 밴드면 후보에 들어와야 한다.
              후보를 거리로만 잘라놓고 부스트를 매기면, 빽빽한 밴드에서 선호가 조용히
              무력해진다 — 사용자에겐 `parking=true` 가 아무것도 안 바꾼 것으로 보인다.
    Decision: #20
    """
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                *(row(f"근접_{i}", east_m=10 * i) for i in range(1, 5)),
                row("먼쪽_주차", east_m=450, parking=True),
            ])
            ranked = await _ranked(session, parking=True, limit=2)
            assert ranked[0] == "먼쪽_주차", "빽빽한 밴드에서 선호 시설이 후보에서 잘렸다"
            assert ranked[1] == "근접_1"
        finally:
            await _clean(session)


async def test_identical_rank_is_stable_before_the_limit_cutoff():
    """같은 rank key의 후보도 LIMIT 전에 (source, ref)로 안정화한다."""
    async with db_session() as session:
        await _clean(session)
        try:
            # 물리 삽입은 의도한 외부 키 순서의 반대다. 안정 키가 없으면 먼저 들어간 newer
            # 두 행이 LIMIT에 남을 수 있다.
            await _seed(session, [
                row(
                    "newer-c", east_m=250, source_ref="tie-c", parking=True,
                ),
                row(
                    "newer-b", east_m=250, source_ref="tie-b", parking=True,
                ),
            ], source=SOURCES[1])
            await _seed(session, [
                row(
                    "base-z", east_m=250, source_ref="tie-z", parking=True,
                ),
            ], source=SOURCES[0])

            out = await resolve_facilities(
                FacilityParams(
                    lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=RADIUS_M,
                    kind="cafe", parking=True, limit=2,
                ),
                session,
            )

            assert [
                (item.source.name, item.source_ref) for item in out.results
            ] == [
                (SOURCES[0], "tie-z"),
                (SOURCES[1], "tie-b"),
            ]
            assert out.truncated is True
        finally:
            await _clean(session)


async def test_null_source_ref_legacy_tie_falls_back_to_id():
    """외부 ref가 없는 legacy 동률은 마지막 내부 id로 LIMIT 후보를 안정화한다."""
    async with db_session() as session:
        await _clean(session)
        try:
            lng = TEST_ORIGIN[1] + 250 / 91_000.0
            await session.execute(text("""
                INSERT INTO facility (
                    source, source_ref, name, kind, category3, location,
                    snapshot, parking, pet
                ) VALUES
                    (:source, NULL, 'legacy-first', 'cafe', 'cafe',
                     ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                     :snapshot, true, '{}'::jsonb),
                    (:source, NULL, 'legacy-second', 'cafe', 'cafe',
                     ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                     :snapshot, true, '{}'::jsonb)
            """), {
                "source": SOURCES[0],
                "lat": TEST_ORIGIN[0],
                "lng": lng,
                "snapshot": SNAPSHOT,
            })
            await session.commit()

            expected_id = (await session.execute(text("""
                SELECT id
                FROM facility
                WHERE source = :source AND source_ref IS NULL
                ORDER BY id
                LIMIT 1
            """), {"source": SOURCES[0]})).scalar_one()

            out = await resolve_facilities(
                FacilityParams(
                    lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=RADIUS_M,
                    kind="cafe", parking=True, limit=1,
                ),
                session,
            )

            assert out.results[0].id == expected_id
            assert out.results[0].source_ref is None
            assert out.results[0].name == "legacy-first"
            assert out.truncated is True
        finally:
            await _clean(session)


async def test_borrowed_parking_also_earns_the_boost():
    """
    Contract: `parking` 은 빌려올 수 있는 필드다. 병합 전 자기 컬럼만 보고 순위를 매기면
              빌린 주차장을 화면에는 보여주면서 순위에서는 없는 것으로 친다.
    Decision: #20
    """
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [row("빌려주는쪽", east_m=450, parking=True)])
            await _seed(session, [row("빌리는쪽", east_m=450)], source=SOURCES[1])
            lender, borrower = (await session.execute(text(
                "SELECT id FROM facility WHERE source = :a UNION ALL "
                "SELECT id FROM facility WHERE source = :b"),
                {"a": SOURCES[0], "b": SOURCES[1]})).scalars().all()
            await session.execute(text("""
                INSERT INTO facility_link (facility_id, source, source_ref, method)
                VALUES (:borrower, 'facility', :lender, 'test')
            """), {"borrower": borrower, "lender": str(lender)})
            await session.commit()
            await _seed(session, [row("가까움_주차없음", east_m=100)])

            ranked = await _ranked(session, parking=True)
            assert "빌려주는쪽" not in ranked, "링크의 ref 쪽은 노출 행에서 빠진다"
            assert ranked[0] == "빌리는쪽", "빌려온 주차장이 부스트에 안 세어졌다"
        finally:
            await _clean(session)
