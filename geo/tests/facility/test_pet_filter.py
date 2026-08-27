"""`pet` 축이 적재 → 파생 → 검색까지 한 진실로 흐르는지.

순수 파싱 규칙은 `test_pet_axes.py` 가 고정한다. 여기서 지키는 것은 그 위의 세 가지다:

1. 축은 **저장된** `pet` 에서 파생된다 (입력 dict 가 아니라)
2. 크기 필터는 **미상을 빼지 않는다** — 병원 `open_now` 와 같은 규칙
3. `pet` 을 빌려온 행은 축도 같이 빌려오고, 필터 역시 그 effective 축을 본다
"""

from datetime import UTC, date, datetime

from sqlalchemy import text

from app.api.facility import FacilityParams, facility_search
from app.ingest.facility_store import upsert_rows
from app.ingest.kcisa import source_ref
from app.ingest.pet_axes import derive_all
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
    await derive_all(session, redo=True, sources=SOURCES)


async def _search(session, **params):
    out = await facility_search(
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


async def test_axes_are_derived_from_the_stored_envelope():
    """적재 파서가 아니라 저장된 `pet` 이 축의 원천이다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [facility_row(
                "소형만카페",
                pet='{"allowed": "Y", "exclusive": "해당없음", "size": "5kg 미만 소형"}',
            )])
            found = await _search(session)
            axes = found["소형만카페"].pet_axes

            assert (axes.allowed, axes.exclusive) == (True, False)
            assert (axes.size_class, axes.max_kg) == ("small", 5.0)
            # 원문은 지우지 않는다 — 파싱이 틀렸을 때 되돌릴 근거다
            assert found["소형만카페"].pet["size"] == "5kg 미만 소형"
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


async def test_borrowed_envelope_brings_its_axes_along_and_size_filter_uses_them():
    """빌린 `pet` 과 축을 표시할 뿐 아니라 **그 축으로 필터**해야 한다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [facility_row(
                "빌려주는쪽", pet='{"allowed": "Y", "size": "5kg 이하"}',
            )])
            await _seed(session, [facility_row("빌리는쪽")], source=SOURCES[1])
            await _link(session)

            found = await _search(session)
            assert "빌려주는쪽" not in found, "링크의 ref 쪽은 노출 행에서 빠진다"
            axes = found["빌리는쪽"].pet_axes

            assert found["빌리는쪽"].pet["size"] == "5kg 이하"
            assert (axes.size_class, axes.max_kg) == ("small", 5.0), "원문만 빌리고 축은 미상으로 남았다"
            assert "pet" in found["빌리는쪽"].field_sources, "빌린 값에 출처가 안 붙었다"
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


async def test_dog_id_fills_size_from_profile():
    """프로필이 크기를 채운다 — 시설 검색이 개를 모르면 대형견에게 못 가는 곳을 내민다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row("소형만", east_m=10, pet='{"allowed": "Y", "size": "5kg 이하"}'),
                facility_row("모두가능", east_m=20, pet='{"allowed": "Y", "size": "모두 가능"}'),
            ])
            # 장군이 = 셰퍼드 34kg large (app/profile/source.py 페르소나)
            out = await facility_search(
                FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000,
                               dog_id="janggun"),
                session,
            )
            names = {r.name for r in out.results}

            assert "소형만" not in names, "프로필 크기가 필터에 안 닿았다"
            assert "모두가능" in names
            # 무엇으로 걸렀는지가 응답에 남아야 한다 — 빈 목록이 데이터 부족으로 읽히면 안 된다
            assert out.params.dog_size == "large"
        finally:
            await _clean(session)


async def test_explicit_dog_size_wins_over_profile():
    """남의 개를 데려가는 경우가 있다. 명시가 프로필을 이긴다."""
    async with db_session() as session:
        await _clean(session)
        try:
            await _seed(session, [
                facility_row("소형만", east_m=10, pet='{"allowed": "Y", "size": "5kg 이하"}'),
            ])
            out = await facility_search(
                FacilityParams(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=2000,
                               dog_id="janggun", dog_size="small"),
                session,
            )
            assert out.params.dog_size == "small"
            assert "소형만" in {r.name for r in out.results}
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

            cut = await facility_search(FacilityParams(**base, limit=2), session)
            assert cut.truncated is True
            assert len(cut.results) == 2, "상한을 넘겨 돌려주면 안 된다"

            # limit 미지정 = 전부. 지도가 반경 안을 다 그릴 수 있어야 한다.
            whole = await facility_search(FacilityParams(**base), session)
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

            from app.api import facility as facility_api
            original = facility_api.MAX_RESULTS
            facility_api.MAX_RESULTS = 3          # 상한이 실제로 집행되는지만 본다
            try:
                out = await facility_search(FacilityParams(**base), session)
            finally:
                facility_api.MAX_RESULTS = original

            assert len(out.results) == 3, "limit 미지정이 상한을 우회했다"
            assert out.truncated is True, "잘랐으면 말해야 한다"
        finally:
            await _clean(session)
