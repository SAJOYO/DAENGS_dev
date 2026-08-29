"""`pet` 축이 적재 → 파생 → 검색까지 한 진실로 흐르는지.

순수 파싱 규칙은 `test_pet_axes.py` 가 고정한다. 여기서 지키는 것은 그 위의 세 가지다:

1. 축은 **저장된** `pet` 에서 파생된다 (입력 dict 가 아니라)
2. 크기 필터는 **미상을 빼지 않는다** — 병원 `open_now` 와 같은 규칙
3. `pet` 을 빌려온 행은 축도 같이 빌려오고, 필터 역시 그 effective 축을 본다
"""

from datetime import UTC, date, datetime

from sqlalchemy import text

from app.ingest.facility_store import update_pet_detail, upsert_rows
from app.ingest.kcisa import source_ref
from app.ingest.pet_axes import derive_all as derive_pet_axes
from app.ingest.restrictions import derive_all as derive_restrictions
from app.place.facility_resolver import FacilityParams, resolve_facilities
from tests.conftest import TEST_ORIGIN, db_session

SOURCES = ("test:pet_base", "test:pet_newer")
SNAPSHOT = "2025-03-24"


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


async def _clean(session) -> None:
    for source in SOURCES:
        await session.execute(text("DELETE FROM facility WHERE source = :s"), {"s": source})
    await session.commit()


async def _seed(session, rows: list[dict], *, source: str = SOURCES[0]) -> None:
    await upsert_rows(session, source, rows, SNAPSHOT, datetime.now(UTC))
    await session.commit()
    # 실데이터 전체를 훑지 않게 이 테스트가 심은 원천만 다시 파생한다.
    await derive_pet_axes(session, redo=True, sources=SOURCES)


async def _search(session, **params):
    out = await resolve_facilities(
        FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000, **params),
        session,
    )
    return {r.name: r for r in out.results}


async def _link(session) -> None:
    lender, borrower = (await session.execute(text(
        "SELECT id FROM facility WHERE source = :a UNION ALL "
        "SELECT id FROM facility WHERE source = :b"),
        {"a": SOURCES[0], "b": SOURCES[1]})).scalars().all()
    await session.execute(text("""
        INSERT INTO facility_link (facility_id, source, source_ref, method)
        VALUES (:borrower, 'facility', :lender, 'test')
    """), {"borrower": borrower, "lender": str(lender)})
    await session.commit()


async def test_stored_pet_change_invalidates_and_rederives_both_projections():
    """저장된 `pet` 이 바뀌면 축·제약 모두 옛 판정을 버리고 새 원문에서 다시 만든다."""
    async with db_session() as session:
        await _clean(session)
        try:
            ref = source_ref("소형만카페", TEST_ORIGIN[0], TEST_ORIGIN[1])
            await _seed(session, [facility_row(
                "소형만카페",
                pet=('{"allowed": "Y", "exclusive": "해당없음", '
                     '"size": "5kg 미만 소형", "restrictions": "목줄"}'),
            )])
            await derive_restrictions(session, sources=(SOURCES[0],))
            found = await _search(session)
            axes = found["소형만카페"].pet_axes

            assert (axes.allowed, axes.exclusive) == (True, False)
            assert (axes.size_class, axes.max_kg) == ("small", 5.0)
            # 원문은 지우지 않는다 — 파싱이 틀렸을 때 되돌릴 근거다
            assert found["소형만카페"].pet["size"] == "5kg 미만 소형"
            assert found["소형만카페"].restrictions.state == "restricted"

            # 공통 UPSERT 경로: 실제 봉투 변경은 두 파생 묶음을 함께 무효화한다.
            changed = facility_row(
                "소형만카페",
                pet=('{"allowed": "Y", "exclusive": "반려동물 전용", '
                     '"size": "모두 가능", "restrictions": "제한사항 없음"}'),
            )
            await upsert_rows(session, SOURCES[0], [changed], SNAPSHOT, datetime.now(UTC))
            stale = (await session.execute(text("""
                SELECT pet_allowed, pet_exclusive, pet_dog_ok, pet_size_class, pet_max_kg,
                       restriction_state, restriction_parse_state, restriction_predicates,
                       restriction_semantics_version
                FROM facility WHERE source = :source AND source_ref = :ref
            """), {"source": SOURCES[0], "ref": ref})).one()
            assert all(value is None for value in stale)

            pet_stats = await derive_pet_axes(session, sources=(SOURCES[0],))
            restriction_stats = await derive_restrictions(session, sources=(SOURCES[0],))
            assert (pet_stats.updated, restriction_stats.updated) == (1, 1)
            refreshed = (await _search(session))["소형만카페"]
            assert (refreshed.pet_axes.exclusive, refreshed.pet_axes.size_class) == (True, "any")
            assert refreshed.restrictions.state == "none_confirmed"

            # KTO 상세 경로도 같은 무효화 함수를 쓴다.
            changed_count = await update_pet_detail(
                session,
                SOURCES[0],
                ref,
                '{"allowed": "Y", "size": "10kg 이하", "restrictions": "목줄"}',
            )
            assert changed_count == 1
            invalidated = (await session.execute(text("""
                SELECT pet_allowed, pet_exclusive, pet_dog_ok, pet_size_class, pet_max_kg,
                       restriction_state, restriction_parse_state, restriction_predicates,
                       restriction_semantics_version
                FROM facility WHERE source = :source AND source_ref = :ref
            """), {"source": SOURCES[0], "ref": ref})).one()
            assert all(value is None for value in invalidated)
        finally:
            await _clean(session)


async def test_size_filter_keeps_unknown_but_drops_too_small():
    """**미상은 빼지 않는다.** 크기를 모르는 곳은 못 가는 곳이 아니다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row("소형만", east_m=10, pet='{"allowed": "Y", "size": "5kg 이하"}'),
                facility_row("모두가능", east_m=20, pet='{"allowed": "Y", "size": "모두 가능"}'),
                facility_row("미상", east_m=30, pet='{"allowed": "Y", "size": "해당없음"}'),
            ])
            names = set(await _search(session, dog_size="large"))

            assert "모두가능" in names
            assert "미상" in names, "크기 미상을 '못 감'으로 취급했다"
            assert "소형만" not in names
        finally:
            await _clean(session)


async def test_species_without_dog_is_excluded_by_default():
    """종을 열거하면서 개를 뺀 것은 결측이 아니라 명시적 진술이다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row("고양이전용", east_m=10, pet='{"allowed": "Y", "size": "고양이"}'),
                facility_row("개도가능", east_m=20, pet='{"allowed": "Y", "size": "개, 고양이"}'),
            ])
            assert "고양이전용" not in await _search(session)
            assert "고양이전용" in await _search(session, only_dog_ok=False)
            assert "개도가능" in await _search(session)
        finally:
            await _clean(session)


async def test_borrowed_envelope_brings_its_projections_and_provenance_along():
    """빌린 `pet`의 축·제약·출처는 한 묶음이고, legacy 크기 필터도 effective 축을 본다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [facility_row(
                "빌려주는쪽",
                pet='{"allowed": "Y", "size": "5kg 이하", "restrictions": "대형견 입장 불가"}',
            )])
            await _seed(session, [facility_row("빌리는쪽")], source=SOURCES[1])
            await derive_restrictions(session, sources=SOURCES)
            await _link(session)

            found = await _search(session)
            assert "빌려주는쪽" not in found, "링크의 ref 쪽은 노출 행에서 빠진다"
            axes = found["빌리는쪽"].pet_axes

            assert found["빌리는쪽"].pet["size"] == "5kg 이하"
            assert (axes.size_class, axes.max_kg) == ("small", 5.0), "원문만 빌리고 축은 미상으로 남았다"
            assert found["빌리는쪽"].restrictions.state == "restricted"
            assert {"pet", "restrictions"} <= found["빌리는쪽"].field_sources.keys(), (
                "빌린 봉투와 제약에 각각 출처가 안 붙었다"
            )
            assert "빌리는쪽" not in await _search(session, dog_size="large"), (
                "필터는 자기 NULL 축을 보고 통과했는데 표시는 빌린 small 축을 내보냈다"
            )
        finally:
            await _clean(session)


async def test_borrowed_dog_exclusion_is_used_by_default_filter():
    """빌린 봉투가 고양이 전용이면 default `only_dog_ok` 도 effective 축을 봐야 한다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [facility_row(
                "고양이빌려주는쪽", pet='{"allowed": "Y", "size": "고양이"}',
            )])
            await _seed(session, [facility_row("고양이빌리는쪽")], source=SOURCES[1])
            await _link(session)

            assert "고양이빌리는쪽" not in await _search(session)
            shown = await _search(session, only_dog_ok=False)
            assert shown["고양이빌리는쪽"].pet_axes.dog_ok is False
            assert shown["고양이빌리는쪽"].pet["size"] == "고양이"
        finally:
            await _clean(session)


async def test_dog_size_value_drives_the_size_filter():
    """크기는 **값**으로 받는다 — dog_id → 프로필 projection 은 resolver 의 일이 아니다.

    대형견 값이면 소형 전용을 거르고, 소형견 값이면 그대로 통과한다. 무엇으로 걸렀는지는
    응답 params 에 그대로 남는다 — 빈 목록이 데이터 부족으로 읽히면 안 된다.
    """
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row("소형만", east_m=10, pet='{"allowed": "Y", "size": "5kg 이하"}'),
                facility_row("모두가능", east_m=20, pet='{"allowed": "Y", "size": "모두 가능"}'),
            ])
            large = await resolve_facilities(
                FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000,
                               dog_size="large"),
                session,
            )
            names = {r.name for r in large.results}
            assert "소형만" not in names, "크기 값이 필터에 안 닿았다"
            assert "모두가능" in names
            assert large.params.dog_size == "large"

            small = await resolve_facilities(
                FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000,
                               dog_size="small"),
                session,
            )
            assert small.params.dog_size == "small"
            assert "소형만" in {r.name for r in small.results}
        finally:
            await _clean(session)


async def test_truncation_is_reported_not_silent():
    """상한에 걸리면 말한다. 조용히 자르면 "이 반경엔 이만큼뿐"으로 읽힌다 (`/anchor/search` 와 같은 이유)."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row(f"카페{i}", east_m=10 * i, kind="test_kind",
                             pet='{"allowed": "Y", "size": "모두 가능"}')
                for i in range(4)
            ])
            # 실데이터가 같은 반경에 있어 개수를 세려면 이 테스트 행만 봐야 한다.
            base = {"lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1],
                    "radius_m": 2000, "kind": "test_kind"}

            cut = await resolve_facilities(FacilityParams(**base, limit=2), session)
            assert cut.truncated is True
            assert len(cut.results) == 2, "상한을 넘겨 돌려주면 안 된다"

            # limit 미지정 = 전부. 지도가 반경 안을 다 그릴 수 있어야 한다.
            whole = await resolve_facilities(FacilityParams(**base), session)
            assert whole.truncated is False
            assert len(whole.results) == 4
        finally:
            await _clean(session)


async def test_unspecified_limit_is_still_bounded_by_the_server():
    """`limit` 미지정이 '무한'은 아니다. 경계는 서버가 세운다 — 호출자가 카테고리로
    나눠 부를 것이라는 기대는 경계가 아니다 (`/anchor/search` 의 `MAX_LIMIT` 과 같은 이유)."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row(f"많음{i}", east_m=10 * i, kind="test_bulk",
                             pet='{"allowed": "Y", "size": "모두 가능"}')
                for i in range(5)
            ])
            base = {"lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1],
                    "radius_m": 2000, "kind": "test_bulk"}

            out = await resolve_facilities(
                FacilityParams(**base), session, max_results=3,
            )

            assert len(out.results) == 3, "limit 미지정이 상한을 우회했다"
            assert out.truncated is True, "잘랐으면 말해야 한다"
        finally:
            await _clean(session)
