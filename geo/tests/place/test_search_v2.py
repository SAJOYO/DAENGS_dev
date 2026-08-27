"""canonical `/v2/places/search`의 후보군·resolver·정렬 계약."""

import json
import math
from datetime import UTC, datetime

from sqlalchemy import text

from app.ingest.facility_store import upsert_rows
from app.place.search import PlaceKind, PlaceSearchRequest, search_place_groups
from app.place.source_catalog import KCISA_KINDS, KTO_KINDS, MOIS_SOURCES
from tests.conftest import TEST_ORIGIN, db_session

_MEDICAL_SOURCE = "public:mois:animal_hospital"
_MEDICAL_REF = "test:v2:medical:hospital"
_DEV_MEDICAL_REF = "test:v2:dev:hospital"
_NULL_REF_FACILITY_NAME = "V2계약식별자없는카페"
_FACILITY_REFS = ("test:v2:kcisa:cafe-near", "test:v2:kcisa:cafe-far", "test:v2:kto:shop")
_PREFERENCE_REFS = (
    "test:v2:kcisa:preference-near",
    "test:v2:kcisa:preference-middle",
    "test:v2:kcisa:preference-parking",
)


def _lng_at(east_m: int) -> float:
    return TEST_ORIGIN[1] + east_m / (
        111_320 * math.cos(math.radians(TEST_ORIGIN[0]))
    )


def _facility_row(
    ref: str,
    name: str,
    kind: str,
    category3: str,
    east_m: int,
    *,
    raw: dict | None = None,
    parking: bool | None = None,
) -> dict:
    return {
        "source_ref": ref,
        "name": name,
        "kind": kind,
        "category3": category3,
        "sido": None,
        "sigungu": None,
        "address": "테스트",
        "phone": None,
        "homepage": None,
        "hours_text": None,
        "closed_days": None,
        "parking": parking,
        "indoor": None,
        "outdoor": None,
        "pet": "{}",
        "lat": TEST_ORIGIN[0],
        "lng": _lng_at(east_m),
        "last_written": None,
        "raw": json.dumps(raw or {}),
    }


async def _delete_owned_rows(session) -> None:
    await session.execute(text(
        "DELETE FROM place WHERE source_id = ANY(:refs)"
    ), {"refs": [_MEDICAL_REF, _DEV_MEDICAL_REF]})
    await session.execute(text(
        "DELETE FROM facility WHERE source_ref = ANY(:refs)"
    ), {"refs": [*_FACILITY_REFS, *_PREFERENCE_REFS]})
    await session.execute(text(
        "DELETE FROM facility WHERE source = 'kcisa' AND source_ref IS NULL AND name = :name"
    ), {"name": _NULL_REF_FACILITY_NAME})


def test_place_kind_exactly_matches_all_resolver_vocabularies():
    resolver_kinds = {
        *KCISA_KINDS.values(),
        *KTO_KINDS.values(),
        *(source.kind for source in MOIS_SOURCES.values()),
        "etc",
    }
    assert {kind.value for kind in PlaceKind} == resolver_kinds


def test_default_group_limit_stays_inside_the_total_budget():
    request = PlaceSearchRequest(
        lat=37.5,
        lng=127.0,
        kinds=["hospital", "cafe", "shopping"],
    )
    assert request.effective_limit_per_kind == 1666
    assert request.effective_limit_per_kind * len(request.kinds) <= 5000


async def test_parking_preference_reaches_candidate_selection_before_limit():
    """Place가 선호를 resolver에 안 넘기면 450m 주차 행은 거리 LIMIT 뒤에서 복구할 수 없다."""
    async with db_session() as session:
        await _delete_owned_rows(session)
        await session.commit()
        try:
            now = datetime.now(UTC)
            await upsert_rows(session, "kcisa", [
                _facility_row(
                    _PREFERENCE_REFS[0], "100m_주차불가", "cafe", "카페", 100,
                    parking=False,
                ),
                _facility_row(
                    _PREFERENCE_REFS[1], "200m_주차불가", "cafe", "카페", 200,
                    parking=False,
                ),
                _facility_row(
                    _PREFERENCE_REFS[2], "450m_주차가능", "cafe", "카페", 450,
                    parking=True,
                ),
            ], "2026-08-26", now)
            await session.commit()

            response = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=850,
                kinds=["cafe"],
                limit_per_kind=1,
                preferences={"parking": True},
            ))

            group = response.groups[0]
            assert [hit.place.name for hit in group.results] == ["450m_주차가능"]
            assert group.truncated is True
            assert group.sort.coverage["parking"].model_dump() == {
                "known_true": 1, "known_false": 0, "unknown": 0,
            }
        finally:
            await session.rollback()
            await _delete_owned_rows(session)
            await session.commit()


async def test_v2_groups_kinds_and_sorts_only_inside_each_candidate_set():
    """의료/시설을 한 전역 순위로 섞지 않고 요청 kind 순서의 독립 그룹으로 돌려준다."""
    async with db_session() as session:
        await _delete_owned_rows(session)
        await session.commit()
        try:
            now = datetime.now(UTC)
            await session.execute(text("""
                INSERT INTO place (
                    kind, name, address, phone, location, is_night, is_24h, hours, tags,
                    source, source_id, source_updated_at, license_status_code,
                    license_status_name, active
                ) VALUES (
                    'hospital', 'V2계약동물병원', '테스트', '02-0',
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    false, false, NULL, ARRAY[]::text[], :source, :ref, :updated,
                    '01', '영업/정상', true
                ), (
                    'hospital', 'V2제외개발병원', '테스트', '02-1',
                    ST_SetSRID(ST_MakePoint(:dev_lng, :lat), 4326)::geography,
                    false, false, NULL, ARRAY[]::text[], 'dev', :dev_ref, :updated,
                    '01', '영업/정상', true
                )
            """), {
                "lat": TEST_ORIGIN[0],
                "lng": _lng_at(300),
                "dev_lng": _lng_at(50),
                "source": _MEDICAL_SOURCE,
                "ref": _MEDICAL_REF,
                "dev_ref": _DEV_MEDICAL_REF,
                "updated": now,
            })
            await upsert_rows(session, "kcisa", [
                _facility_row(_FACILITY_REFS[1], "먼카페", "cafe", "카페", 400),
                _facility_row(_FACILITY_REFS[0], "가까운카페", "cafe", "카페", 100),
            ], "2026-08-26", now)
            await upsert_rows(session, "kto", [
                _facility_row(
                    _FACILITY_REFS[2], "명시한쇼핑", "shopping", "SH040300", 200,
                    raw={"contenttypeid": "38", "lclsSystm3": "SH040300"},
                ),
            ], "2026-08-26", now)
            await session.execute(text("""
                INSERT INTO facility (
                    source, source_ref, name, kind, category3, location, snapshot, pet
                ) VALUES (
                    'kcisa', NULL, :name, 'cafe', '카페',
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    'legacy-null-ref', '{}'::jsonb
                )
            """), {
                "name": _NULL_REF_FACILITY_NAME,
                "lat": TEST_ORIGIN[0],
                "lng": _lng_at(25),
            })
            await session.commit()

            response = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["shopping", "hospital", "cafe"],
                limit_per_kind=2,
            ))

            assert [group.kind for group in response.groups] == [
                "shopping", "hospital", "cafe",
            ]
            assert [[hit.place.name for hit in group.results] for group in response.groups] == [
                ["명시한쇼핑"], ["V2계약동물병원"], ["가까운카페", "먼카페"],
            ]
            assert response.conditions is None
            assert "conditions" not in response.model_dump()
            assert all(group.sort.type == "distance" for group in response.groups)
            assert all(group.limit == 2 for group in response.groups)
            assert response.groups[0].results[0].place.key.source == "kto"
            assert response.groups[1].results[0].place.key.source == _MEDICAL_SOURCE
            assert "V2제외개발병원" not in {
                hit.place.name for hit in response.groups[1].results
            }
            assert _NULL_REF_FACILITY_NAME not in {
                hit.place.name for hit in response.groups[2].results
            }
            assert response.groups[2].results[0].place.facts.parking is None

            await session.execute(text("""
                UPDATE facility
                SET parking = CASE source_ref
                    WHEN :near_ref THEN false
                    WHEN :far_ref THEN true
                END
                WHERE source = 'kcisa' AND source_ref = ANY(:refs)
            """), {
                "near_ref": _FACILITY_REFS[0],
                "far_ref": _FACILITY_REFS[1],
                "refs": list(_FACILITY_REFS[:2]),
            })
            await session.commit()

            preferred = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["cafe", "shopping", "hospital"],
                limit_per_kind=2,
                preferences={"parking": True},
            ))
            assert [hit.place.name for hit in preferred.groups[0].results] == [
                "먼카페", "가까운카페",
            ], "같은 500m 밴드에서 주차 사실이 순위에 반영되지 않았다"
            assert preferred.groups[0].sort.model_dump() == {
                "type": "distance_preferred",
                "basis": ("distance_band", "parking", "distance_m"),
                "applied": ("parking",),
                "band_m": 500,
                "coverage": {
                    "parking": {"known_true": 1, "known_false": 1, "unknown": 0},
                },
            }
            assert preferred.groups[1].sort.model_dump() == {
                "type": "distance_preferred",
                "basis": ("distance_band", "parking", "distance_m"),
                "applied": ("parking",),
                "band_m": 500,
                "coverage": {
                    "parking": {"known_true": 0, "known_false": 0, "unknown": 1},
                },
            }, "정보가 없는 shopping을 주차 불가로 세면 안 된다"
            assert preferred.groups[2].sort.model_dump() == {
                "type": "distance", "basis": ("distance_m",),
            }, "주차 사실이 없는 의료 그룹에 선호 적용을 주장하면 안 된다"

            cut = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["cafe"],
                limit_per_kind=1,
            ))
            assert [hit.place.name for hit in cut.groups[0].results] == ["가까운카페"]
            assert cut.groups[0].truncated is True

            await session.execute(text("""
                UPDATE facility
                SET pet_allowed = true,
                    pet_size_class = CASE source_ref
                        WHEN :near_ref THEN 'small'
                        WHEN :far_ref THEN 'any'
                    END,
                    pet_max_kg = CASE source_ref
                        WHEN :near_ref THEN 5
                        ELSE NULL
                    END
                WHERE source = 'kcisa' AND source_ref = ANY(:refs)
            """), {
                "near_ref": _FACILITY_REFS[0],
                "far_ref": _FACILITY_REFS[1],
                "refs": list(_FACILITY_REFS[:2]),
            })
            await session.commit()

            for_dog = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["cafe", "shopping", "hospital"],
                limit_per_kind=2,
                conditions={"dog_id": "janggun"},
            ))

            assert for_dog.conditions is not None
            assert for_dog.conditions.model_dump() == {
                "dog_id": "janggun", "dog_size": "large", "dog_weight_kg": 34.0,
            }
            assert [hit.place.name for hit in for_dog.groups[0].results] == [
                "가까운카페", "먼카페",
            ], "평가가 후보를 삭제하거나 거리순을 바꿨다"
            assert [
                (hit.evaluations.dog_access.state, hit.evaluations.dog_access.reason)
                for hit in for_dog.groups[0].results
            ] == [
                ("incompatible", "weight_exceeded"),
                ("compatible", "size_allowed"),
            ]
            assert for_dog.groups[1].results[0].evaluations.dog_access.model_dump() == {
                "state": "unknown", "reason": "missing_restriction",
            }
            medical_hit = for_dog.groups[2].results[0]
            assert medical_hit.evaluations.dog_access is None
            assert medical_hit.evaluations.model_dump() == {}

            explicit_size = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["cafe"],
                limit_per_kind=2,
                conditions={"dog_id": "janggun", "dog_size": "small"},
            ))
            assert explicit_size.conditions is not None
            assert explicit_size.conditions.dog_size == "small"
            assert explicit_size.conditions.dog_weight_kg is None, (
                "명시한 크기에 장군이의 34kg을 섞었다"
            )
            assert [
                hit.evaluations.dog_access.state
                for hit in explicit_size.groups[0].results
            ] == ["unknown", "compatible"]

            await session.execute(text("""
                UPDATE facility SET pet_dog_ok = false
                WHERE source = 'kto' AND source_ref = :ref
            """), {"ref": _FACILITY_REFS[2]})
            await session.commit()
            unknown_profile = await search_place_groups(session, PlaceSearchRequest(
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=1000,
                kinds=["shopping", "cafe"],
                limit_per_kind=2,
                conditions={"dog_id": "missing-profile"},
            ))
            assert unknown_profile.conditions is not None
            assert unknown_profile.conditions.model_dump() == {
                "dog_id": "missing-profile", "dog_size": None, "dog_weight_kg": None,
            }
            assert unknown_profile.groups[0].results[0].evaluations.dog_access.model_dump() == {
                "state": "incompatible", "reason": "dog_disallowed",
            }
            assert [
                hit.evaluations.dog_access.reason
                for hit in unknown_profile.groups[1].results
            ] == ["missing_dog_weight", "missing_dog_size"]
        finally:
            await session.rollback()
            await _delete_owned_rows(session)
            await session.commit()
