"""의료·시설 resolver가 공유할 PlaceResult 계약."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.geo.contract import SearchMust, SearchPlan
from app.geo.schemas import PlaceOut
from app.geo.search import find_places
from app.ingest.facility_store import upsert_rows
from app.place.adapters import facility_place_result, medical_place_result
from app.place.contracts import (
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
)
from app.place.facility_resolver import (
    FacilityOut,
    FacilityParams,
    FacilitySourceOut,
    PetAxesOut,
    RestrictionsOut,
    resolve_facilities,
)
from tests.conftest import TEST_ORIGIN, db_session


def medical_result(**overrides) -> PlaceOut:
    values = {
        "id": 991,
        "kind": "hospital",
        "name": "튼튼동물병원",
        "lat": 37.5,
        "lng": 127.0,
        "distance_m": 120,
        "address": "서울시 테스트로 1",
        "phone": "02-000-0000",
        "is_night": False,
        "is_24h": False,
        "open_now": None,
        "hours_today": None,
        "tags": [],
        "source": "public:mois:animal_hospital",
        "source_ref": "MOIS:123",
        "source_updated_at": datetime(2026, 8, 20, tzinfo=UTC),
        "active": True,
        "license_status_code": "01",
        "license_status_name": "영업/정상",
    }
    values.update(overrides)
    return PlaceOut(**values)


def facility_result(**overrides) -> FacilityOut:
    values = {
        "id": 992,
        "source_ref": "KTO:38:123",
        "name": "일반 쇼핑",
        "kind": "shopping",
        "icon_group": "supply",
        "category3": "SH040300",
        "classification_category": "38",
        "lat": 37.5,
        "lng": 127.0,
        "distance_m": 240,
        "address": "서울시 테스트로 2",
        "phone": None,
        "homepage": None,
        "hours_text": None,
        "closed_days": None,
        "parking": None,
        "indoor": None,
        "outdoor": None,
        "pet": {},
        "pet_axes": PetAxesOut(),
        "restrictions": RestrictionsOut(),
        "source": FacilitySourceOut(
            name="kto", ref="KTO:38:123", as_of="2026-08-20",
        ),
    }
    values.update(overrides)
    return FacilityOut(**values)


def test_medical_adapter_uses_external_key_and_keeps_unknowns():
    legacy = medical_result()
    result = medical_place_result(legacy)
    payload = result.model_dump()

    assert payload["key"] == {"source": "public:mois:animal_hospital", "ref": "MOIS:123"}
    assert payload["match"] == {
        "source": {"source": "public:mois:animal_hospital", "ref": "MOIS:123"},
        "kind": "hospital",
    }
    assert payload["classifications"] == [{
        "source": {"source": "public:mois:animal_hospital", "ref": "MOIS:123"},
        "source_category": "animal_hospitals",
        "kind": "hospital",
        "mapping_version": "mois-source/1",
        "as_of": "2026-08-20T00:00:00+00:00",
    }]
    assert payload["facts"]["medical"]["open_now"] is None
    assert "is_night" not in payload["facts"]["medical"]
    assert "is_24h" not in payload["facts"]["medical"]
    assert "tags" not in payload["facts"]["medical"]
    assert "id" not in payload, "DB PK가 Place identity로 노출됐다"
    assert result.aliases == []

    # adapter용 메타데이터가 기존 의료 JSON을 바꾸면 이 PR의 절단면을 넘는다.
    legacy_payload = legacy.model_dump()
    assert "source" not in legacy_payload
    assert "source_ref" not in legacy_payload
    assert "license_status_code" not in legacy_payload


def test_facility_adapter_preserves_mapping_input_and_borrowed_provenance():
    borrowed = FacilitySourceOut(name="kcisa", ref="KCISA:456", as_of="2025-03-24")
    legacy = facility_result(
        hours_text="매일 10:00~20:00",
        pet={"restrictions": "제한사항 없음"},
        restrictions=RestrictionsOut(state="none_confirmed", parse_state="mapped"),
        field_sources={
            "hours_text": borrowed,
            "pet": borrowed,
            "restrictions": borrowed,
        },
    )
    result = facility_place_result(legacy)

    assert result.classifications[0].source_category == "38"
    assert result.classifications[0].mapping_version == "kto-contenttypeid/2"
    assert result.field_sources["facts.hours_text"].source == PlaceRef(
        source="kcisa", ref="KCISA:456",
    )
    assert result.field_sources["facts.pet_access"].source == PlaceRef(
        source="kcisa", ref="KCISA:456",
    )
    assert result.field_sources["facts.restrictions"].source == PlaceRef(
        source="kcisa", ref="KCISA:456",
    )
    # name+150m 링크는 검증된 물리 identity가 아니므로 alias로 승격하지 않는다.
    assert result.aliases == []

    legacy_payload = legacy.model_dump()
    assert "classification_category" not in legacy_payload
    assert "indoor" not in legacy_payload and "outdoor" not in legacy_payload
    assert "ref" not in legacy_payload["source"]


def test_medical_borrowed_hours_keep_the_facility_record_key():
    legacy = medical_result(
        hours_text="매일 09:00~18:00",
        hours_source={"name": "kcisa", "as_of": "2025-03-24"},
        hours_source_ref="KCISA:HOURS:1",
    )

    result = medical_place_result(legacy)

    assert result.field_sources["facts.hours_text"].source == PlaceRef(
        source="kcisa", ref="KCISA:HOURS:1",
    )
    assert result.aliases == [], "필드를 빌렸다는 이유만으로 identity alias가 됐다"


def test_adapter_metadata_is_absent_from_legacy_serialization_schemas():
    medical_internal = {
        "source", "source_ref", "source_updated_at", "active",
        "license_status_code", "license_status_name", "hours_source_ref",
    }
    facility_internal = {
        "classification_category", "indoor", "outdoor", "place_field_sources",
    }

    assert medical_internal.isdisjoint(
        PlaceOut.model_json_schema(mode="serialization")["properties"]
    )
    assert facility_internal.isdisjoint(
        FacilityOut.model_json_schema(mode="serialization")["properties"]
    )
    assert "ref" not in FacilitySourceOut.model_json_schema(mode="serialization")["properties"]


def test_match_can_use_an_alias_classification_without_changing_primary_key():
    kcisa = PlaceRef(source="kcisa", ref="K1")
    kto = PlaceRef(source="kto", ref="T1")
    result = PlaceResult(
        key=kcisa,
        aliases=[kto],  # 호출자가 검증했다고 명시적으로 준 경우만
        name="같은 물리 장소",
        lat=37.5,
        lng=127.0,
        distance_m=10,
        match=PlaceMatch(source=kto, kind="shopping"),
        classifications=[
            PlaceClassification(
                source=kcisa, source_category="반려동물용품", kind="pet_shop",
                mapping_version="kcisa-category3/2",
            ),
            PlaceClassification(
                source=kto, source_category="38", kind="shopping",
                mapping_version="kto-contenttypeid/2",
            ),
        ],
        facts=PlaceFacts(),
    )

    assert {item.kind for item in result.classifications} == {"pet_shop", "shopping"}
    assert result.key == kcisa
    assert result.match == PlaceMatch(source=kto, kind="shopping")
    assert result.icon_group == "supply"


def test_match_must_reference_an_exact_classification():
    key = PlaceRef(source="kcisa", ref="K1")
    with pytest.raises(ValidationError, match="match must reference"):
        PlaceResult(
            key=key,
            name="불일치",
            lat=37.5,
            lng=127.0,
            distance_m=10,
            match=PlaceMatch(source=key, kind="shopping"),
            classifications=[PlaceClassification(
                source=key, source_category="반려동물용품", kind="pet_shop",
                mapping_version="kcisa-category3/2",
            )],
            facts=PlaceFacts(),
        )


def test_key_and_each_source_record_require_unique_classification_provenance():
    key = PlaceRef(source="kcisa", ref="K1")
    other = PlaceRef(source="kto", ref="T1")
    common = {
        "name": "계약 오류",
        "lat": 37.5,
        "lng": 127.0,
        "distance_m": 10,
        "facts": PlaceFacts(),
    }

    with pytest.raises(ValidationError, match="key must have classification"):
        PlaceResult(
            key=key,
            match=PlaceMatch(source=other, kind="shopping"),
            classifications=[PlaceClassification(
                source=other, source_category="38", kind="shopping",
                mapping_version="kto-contenttypeid/2",
            )],
            **common,
        )

    with pytest.raises(ValidationError, match="exactly one classification"):
        PlaceResult(
            key=key,
            match=PlaceMatch(source=key, kind="pet_shop"),
            classifications=[
                PlaceClassification(
                    source=key, source_category="반려동물용품", kind="pet_shop",
                    mapping_version="kcisa-category3/2",
                ),
                PlaceClassification(
                    source=key, source_category="반려동물용품", kind="shopping",
                    mapping_version="kcisa-category3/2",
                ),
            ],
            **common,
        )


def test_adapter_rejects_a_kind_not_produced_by_its_mapping_version():
    with pytest.raises(ValueError, match="stale facility kind"):
        facility_place_result(facility_result(kind="pet_shop"))

    with pytest.raises(ValueError, match="stale medical kind"):
        medical_place_result(medical_result(kind="pharmacy"))


async def test_kto_mapping_category_is_recovered_from_raw_contenttypeid():
    """legacy category3=SH040300이 아니라 실제 kind 매핑 입력 38을 노출한다."""
    ref = "test:place-contract:kto-38"
    row = {
        "source_ref": ref,
        "name": "계약테스트쇼핑",
        "kind": "shopping",
        "category3": "SH040300",
        "sido": None,
        "sigungu": None,
        "address": "테스트",
        "phone": None,
        "homepage": None,
        "hours_text": None,
        "closed_days": None,
        "parking": None,
        "indoor": None,
        "outdoor": None,
        "pet": "{}",
        "lat": TEST_ORIGIN[0],
        "lng": TEST_ORIGIN[1],
        "last_written": None,
        "raw": json.dumps({"contenttypeid": "38", "lclsSystm3": "SH040300"}),
    }

    async with db_session() as session:
        await session.execute(text(
            "DELETE FROM facility WHERE source = 'kto' AND source_ref = :ref"
        ), {"ref": ref})
        await session.commit()
        try:
            now = datetime.now(UTC)
            await upsert_rows(session, "kto", [row], "2026-08-26", now)
            result = await resolve_facilities(
                FacilityParams(
                    lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=1000, kind="shopping",
                ),
                session,
            )
            legacy = next(item for item in result.results if item.source_ref == ref)
            place = facility_place_result(legacy)

            assert legacy.category3 == "SH040300", "기존 API 표시 category를 바꿨다"
            assert place.classifications[0].source_category == "38"
        finally:
            await session.rollback()
            await session.execute(text(
                "DELETE FROM facility WHERE source = 'kto' AND source_ref = :ref"
            ), {"ref": ref})
            await session.commit()


async def test_medical_search_carries_hidden_source_identity_into_adapter():
    """현재 의료 응답 JSON은 그대로지만 resolver 내부에는 외부 키가 남아 있어야 한다."""
    ref = "test:place-contract:mois-hospital"
    source = "public:mois:animal_hospital"

    async with db_session() as session:
        await session.execute(text(
            "DELETE FROM place WHERE source = :source AND source_id = :ref"
        ), {"source": source, "ref": ref})
        await session.commit()
        try:
            await session.execute(text("""
                INSERT INTO place (
                    kind, name, address, phone, location, is_night, is_24h, hours, tags,
                    source, source_id, source_updated_at, license_status_code,
                    license_status_name, active
                ) VALUES (
                    'hospital', '계약테스트동물병원', '테스트', '02-0',
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    false, false, NULL, ARRAY[]::text[], :source, :ref, :updated,
                    '01', '영업/정상', true
                )
            """), {
                "lat": TEST_ORIGIN[0],
                "lng": TEST_ORIGIN[1],
                "source": source,
                "ref": ref,
                "updated": datetime(2026, 8, 20, tzinfo=UTC),
            })
            places = await find_places(session, SearchPlan(must=SearchMust(
                lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1], radius_m=1000,
                judge_at=datetime.now(UTC), kind="hospital",
            )))
            legacy = next(item for item in places if item.source_ref == ref)
            place = medical_place_result(legacy)

            assert place.key == PlaceRef(source=source, ref=ref)
            assert place.classifications[0].source_category == "animal_hospitals"
            assert "source_ref" not in legacy.model_dump(), "기존 API JSON이 바뀌었다"
        finally:
            await session.rollback()
            await session.execute(text(
                "DELETE FROM place WHERE source = :source AND source_id = :ref"
            ), {"source": source, "ref": ref})
            await session.commit()
