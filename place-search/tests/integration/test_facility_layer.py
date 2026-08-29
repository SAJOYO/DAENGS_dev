"""기반층(facility) 계약 — 리뷰가 지목한 네 가지를 고정한다.

1. 스냅샷을 다시 적재해도 `facility.id`가 유지된다 (외부가 잡을 수 있어야 한다)
2. 같은 시설이 두 원천에 있을 때 정보가 사라지지 않는다 (행 승자독식 금지)
3. 상세(pet)는 다음 적재에 증발하지 않는다
4. 운영시간 보강이 의료 검색의 개수·정렬·open_now 판정을 바꾸지 않는다
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from app.geo.facility_hours import attach_facility_hours
from app.geo.ranking import DISTANCE_BAND_M
from app.geo.schemas import PlaceOut
from app.ingest.facility_store import prune_unseen, upsert_rows
from app.ingest.kcisa import source_ref
from app.ingest.linking import _LINK_CROSS
from app.place.facility_resolver import _SEARCH, MEDICAL, _merge
from tests.conftest import TEST_ORIGIN, TEST_SOURCE, db_session, place_row, seeded_places

# 동해 한복판 — place 쪽 테스트와 같은 격리 전략(좌표)을 쓴다.
FAC_SOURCES = ("test:base", "test:newer")


def facility_row(name: str, *, east_m: int = 0, kind: str = "cafe", **fields) -> dict:
    lng = TEST_ORIGIN[1] + east_m / 91_000.0
    row = {
        "source_ref": source_ref(name, TEST_ORIGIN[0], lng),
        "name": name, "kind": kind, "category3": kind,
        "sido": None, "sigungu": None, "address": "테스트", "phone": None,
        "homepage": None, "hours_text": None, "closed_days": None,
        "parking": None, "indoor": None, "outdoor": None,
        "lat": TEST_ORIGIN[0], "lng": lng, "last_written": date(2025, 3, 24),
    }
    row.update(fields)
    return row


async def _clean(session):
    for source in FAC_SOURCES:
        await session.execute(
            text("DELETE FROM facility WHERE source = :s"), {"s": source}
        )
    await session.commit()


async def test_id_survives_resnapshot():
    """같은 시설은 다시 적재해도 같은 id — 즐겨찾기·추천 이력이 잡을 수 있어야 한다."""
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            await upsert_rows(session, "test:base", [facility_row("멍멍카페")],
                              "2025-03-24", first)
            await session.commit()
            before = (await session.execute(text(
                "SELECT id FROM facility WHERE source = 'test:base'"))).scalar_one()

            second = datetime.now(UTC)
            await upsert_rows(session, "test:base", [facility_row("멍멍카페")],
                              "2026-01-01", second)
            pruned = await prune_unseen(session, "test:base", second)
            await session.commit()
            after = (await session.execute(text(
                "SELECT id FROM facility WHERE source = 'test:base'"))).scalar_one()

            assert after == before, "스냅샷 교체가 id를 갈아치웠다"
            assert pruned == 0, "이번 실행에서 본 행을 prune이 지웠다"
        finally:
            await _clean(session)


async def test_prune_removes_only_rows_missing_from_this_run():
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            await upsert_rows(session, "test:base",
                              [facility_row("남는곳"), facility_row("사라질곳", east_m=50)],
                              "2025-03-24", first)
            await session.commit()

            second = datetime.now(UTC)
            await upsert_rows(session, "test:base", [facility_row("남는곳")],
                              "2026-01-01", second)
            assert await prune_unseen(session, "test:base", second) == 1
            await session.commit()

            names = [r.name for r in await session.execute(text(
                "SELECT name FROM facility WHERE source = 'test:base'"))]
            assert names == ["남는곳"]
        finally:
            await _clean(session)


async def test_empty_detail_does_not_erase_stored_detail():
    """쿼터 때문에 나눠 받는 상세가 다음 적재에 증발하면 누적 자산이 못 된다."""
    async with db_session() as session:
        await _clean(session)
        try:
            now = datetime.now(UTC)
            await upsert_rows(session, "test:base",
                              [facility_row("상세보유", pet='{"acmpyTypeCd": "전구역 동반가능"}')],
                              "2025-03-24", now)
            await session.commit()

            # 상세 없이 목록만 다시 적재 — pet 이 '{}' 로 들어온다
            await upsert_rows(
                session,
                "test:base",
                [facility_row("상세보유")],
                "2026-01-01",
                datetime.now(UTC),
                preserve_empty_pet=True,
            )
            await session.commit()

            pet = (await session.execute(text(
                "SELECT pet FROM facility WHERE source = 'test:base'"))).scalar_one()
            assert pet == {"acmpyTypeCd": "전구역 동반가능"}, "빈 상세가 기존 상세를 덮었다"
        finally:
            await _clean(session)


async def test_cross_source_merge_keeps_richer_fields():
    """두 원천에 같은 시설이 있어도 운영시간이 사라지면 안 된다 (행 승자독식 금지)."""
    async with db_session() as session:
        await _clean(session)
        try:
            now = datetime.now(UTC)
            # 과거 원천: 운영시간 보유. 최신 원천: 목록만 (실제 KCISA/KTO 관계)
            await upsert_rows(session, "test:base",
                              [facility_row("멍멍카페", hours_text="매일 10:00~22:00",
                                            closed_days="연중무휴", parking=True)],
                              "2025-03-24", now)
            await upsert_rows(session, "test:newer", [facility_row("멍멍카페")],
                              "2026-08-24", now)
            await session.commit()

            ids = {r.source: r.id for r in await session.execute(text(
                "SELECT source, id FROM facility WHERE source = ANY(:s)"),
                {"s": list(FAC_SOURCES)})}
            await session.execute(text("""
                INSERT INTO facility_link (facility_id, source, source_ref, method)
                VALUES (:new, 'facility', :old, 'test')
            """), {"new": ids["test:newer"], "old": str(ids["test:base"])})
            await session.commit()

            rows = await session.execute(_SEARCH, {
                "lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1], "radius_m": 1000,
                "kind": "cafe", "limit": 10, "medical": list(MEDICAL),
                "require_canonical_identity": False,
                "canonical_sources": [],
                # 이 테스트가 보는 것은 병합이라 pet 축 필터는 열어 둔다 (엔드포인트 기본값과 별개).
                "only_dog_ok": False, "dog_size": None, "size_accepts": [],
                # 순위는 이 테스트의 관심이 아니다. 선호를 끄면 rank key 가 거리순으로 접힌다.
                "band_m": DISTANCE_BAND_M, "want_parking": False, "want_exclusive": False,
            })
            rows = [r for r in rows if r.source in FAC_SOURCES]

            assert len(rows) == 1, "링크된 두 행이 모두 노출되거나 모두 숨겨졌다"
            assert rows[0].source == "test:newer", "최신 원천이 노출돼야 한다"

            values, borrowed = _merge(rows[0])
            assert values["hours_text"] == "매일 10:00~22:00", "빌려온 운영시간이 사라졌다"
            assert values["parking"] is True
            assert borrowed["hours_text"].name == "test:base", "빌린 필드에 출처가 없다"
        finally:
            await session.execute(text("DELETE FROM facility_link WHERE method = 'test'"))
            await _clean(session)


async def test_cross_kind_rows_are_neither_linked_nor_collapsed():
    """같은 장소 후보여도 shopping 때문에 pet_shop 후보군이 사라지면 안 된다."""
    async with db_session() as session:
        await _clean(session)
        try:
            now = datetime.now(UTC)
            await upsert_rows(
                session,
                "test:base",
                [
                    facility_row("같은가게", kind="pet_shop", hours_text="매일 10:00~22:00"),
                    facility_row("같은카페", east_m=50),
                ],
                "2025-03-24",
                now,
            )
            await upsert_rows(
                session,
                "test:newer",
                [facility_row("같은가게", kind="shopping"),
                 facility_row("같은카페", east_m=50)],
                "2026-08-24",
                now,
            )

            ids = {(r.source, r.name): r.id for r in await session.execute(text(
                "SELECT source, name, id FROM facility WHERE source = ANY(:sources)"
            ), {"sources": list(FAC_SOURCES)})}

            # 새 linker는 이름과 좌표가 같아도 후보군이 다르면 hard identity를 만들지 않는다.
            await session.execute(_LINK_CROSS)
            cross_kind_linked = (await session.execute(text("""
                SELECT count(*) FROM facility_link
                WHERE source = 'facility'
                  AND facility_id = :newer AND source_ref = :base
            """), {
                "newer": ids[("test:newer", "같은가게")],
                "base": str(ids[("test:base", "같은가게")]),
            })).scalar_one()
            same_kind_linked = (await session.execute(text("""
                SELECT count(*) FROM facility_link
                WHERE source = 'facility'
                  AND facility_id = :newer AND source_ref = :base
            """), {
                "newer": ids[("test:newer", "같은카페")],
                "base": str(ids[("test:base", "같은카페")]),
            })).scalar_one()
            assert cross_kind_linked == 0
            assert same_kind_linked == 1, "같은 kind까지 링크하지 않았다"

            # migration 전 링크가 남거나 수동 링크가 생겨도 resolver는 방어해야 한다.
            await session.execute(text("""
                INSERT INTO facility_link (facility_id, source, source_ref, method)
                VALUES (:newer, 'facility', :base, 'test-cross-kind')
            """), {
                "newer": ids[("test:newer", "같은가게")],
                "base": str(ids[("test:base", "같은가게")]),
            })

            async def search(kind: str):
                rows = await session.execute(_SEARCH, {
                    "lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1], "radius_m": 1000,
                    "kind": kind, "limit": 10, "medical": list(MEDICAL),
                    "require_canonical_identity": False,
                    "canonical_sources": [],
                    "only_dog_ok": False, "dog_size": None, "size_accepts": [],
                    "band_m": DISTANCE_BAND_M,
                    "want_parking": False, "want_exclusive": False,
                })
                return [row for row in rows if row.source in FAC_SOURCES]

            pet_shops = await search("pet_shop")
            shopping = await search("shopping")

            assert [row.source for row in pet_shops] == ["test:base"]
            assert [row.source for row in shopping] == ["test:newer"]
            assert shopping[0].b_source is None, "다른 kind의 운영정보를 빌렸다"
        finally:
            # _LINK_CROSS는 전역 파생 링크를 다루므로 이 테스트 트랜잭션을 통째로 되돌린다.
            await session.rollback()
            await _clean(session)


async def test_hours_enrichment_does_not_change_medical_results():
    """보강은 표시만 — 결과 개수·정렬·open_now 판정에 관여하지 않는다."""
    async with seeded_places([
        place_row("h1", "가까운동물병원", east_m=100),
        place_row("h2", "먼동물병원", east_m=300),
    ]) as session:
        places = [
            PlaceOut(id=r.id, kind="hospital", name=r.name, lat=TEST_ORIGIN[0],
                     lng=TEST_ORIGIN[1], distance_m=d, address=None, phone=None,
                     is_night=False, is_24h=False, open_now=None, hours_today=None)
            for r, d in zip(
                await session.execute(text(
                    "SELECT id, name FROM place WHERE source = :s ORDER BY name"),
                    {"s": TEST_SOURCE}),
                (100, 300), strict=False)
        ]
        before = [(p.id, p.distance_m, p.open_now) for p in places]

        await attach_facility_hours(session, places)

        assert [(p.id, p.distance_m, p.open_now) for p in places] == before
        assert all(p.open_now is None for p in places), "보강이 open_now를 판정했다"
        assert all(p.hours_source is None for p in places), "링크 없는데 출처가 붙었다"


async def test_attach_hours_on_empty_list_is_noop():
    async with db_session() as session:
        await attach_facility_hours(session, [])


@pytest.mark.parametrize("name,lat,lng", [("멍멍카페", 37.4979, 127.0276)])
def test_source_ref_is_deterministic(name, lat, lng):
    assert source_ref(name, lat, lng) == source_ref(name, lat, lng)
    assert source_ref(name, lat, lng) != source_ref(name, lat + 0.01, lng)
    assert source_ref("멍멍 카페", lat, lng) == source_ref("멍멍카페", lat, lng)
