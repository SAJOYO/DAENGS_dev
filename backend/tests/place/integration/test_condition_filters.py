"""Exact SQL evaluation on transaction-scoped synthetic canonical rows."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.service import search_filtered_places
from daengs_place.place.source_catalog import MOIS_SOURCES

from ..conftest import TEST_ORIGIN, db_session

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "place/filters/examples.json").read_text(encoding="utf-8")
)
PREFIX = "filter-phase3-"


async def seed(db, rows):
    for row in rows:
        category = "카페" if row.get("kind", "cafe") == "cafe" else "식당"
        parking = row.get("parking")
        raw = row.get(
            "raw", {"주차 가능여부": "Y" if parking else "N"} if parking is not None else {}
        )
        pet = row.get("pet", {"exclusive": "반려동물 전용" if row.get("exclusive") else "해당없음"})
        await db.execute(
            text("""
            INSERT INTO facility (source, source_ref, name, kind, category3, location,
                                  snapshot, raw, pet, parking, pet_exclusive)
            VALUES (:source, :ref, :ref, :kind, :category,
                ST_Project(ST_SetSRID(ST_MakePoint(:lng, :lat),4326)::geography, CAST(:meters AS double precision), pi()/2),
                '2026-09-09', CAST(:raw AS jsonb), CAST(:pet AS jsonb), :parking, :exclusive)
        """),
            {
                "source": row.get("source", "kcisa"),
                "ref": PREFIX + row["id"],
                "kind": row.get("kind", "cafe"),
                "category": row.get("category", category),
                "lat": TEST_ORIGIN[0],
                "lng": TEST_ORIGIN[1],
                "meters": row.get("distance_m", 100),
                "raw": json.dumps(raw, ensure_ascii=False),
                "pet": json.dumps(pet, ensure_ascii=False),
                "parking": parking,
                "exclusive": row.get("exclusive"),
            },
        )


def payload(all=(), any=(), **extra):
    return {
        "candidate_kinds": ["cafe", "restaurant"],
        "spatial": {"lat": TEST_ORIGIN[0], "lng": TEST_ORIGIN[1], "radius_m": 3000},
        "name_query": PREFIX,
        "hard": {"all": list(all), "any": list(any)},
        "unknown_policy": "separate",
        "result_policy": {"limit_per_kind": 20, "uncertain_limit_per_kind": 20},
    } | extra


def parking(value, id="p"):
    return {"id": id, "capability": "operations.parking", "op": "eq", "value": value}


def refs(hits):
    return [hit.place.key.ref.removeprefix(PREFIX) for hit in hits]


@pytest.mark.parametrize("case", FIXTURE["predicate_cases"], ids=lambda c: c["id"])
async def test_sql_matches_approved_cases(case):
    async with db_session() as db:
        await seed(db, FIXTURE["candidates"])
        value = case["state"] | {
            "spatial": payload()["spatial"],
            "name_query": PREFIX,
            "unknown_policy": "separate",
            "result_policy": {"limit_per_kind": 20, "uncertain_limit_per_kind": 20},
        }
        response = await search_filtered_places(db, FilterState.model_validate(value))
        assert sorted(refs([h for g in response.groups for h in g.matched])) == sorted(
            case["expected"]["matched"]
        )
        assert sorted(refs([h for g in response.groups for h in g.uncertain])) == sorted(
            case["expected"]["uncertain"]
        )
        if "ordered_matched_by_kind" in case["expected"]:
            for group in response.groups:
                assert (
                    refs(group.matched) == case["expected"]["ordered_matched_by_kind"][group.kind]
                )
        assert not any(g.matched_truncated or g.uncertain_truncated for g in response.groups)


async def test_filter_precedes_limit_and_unknown_has_independent_budget():
    async with db_session() as db:
        await seed(
            db,
            [
                *({"id": f"no-{i}", "parking": False, "distance_m": i + 1} for i in range(30)),
                {"id": "yes1", "parking": True, "distance_m": 450},
                {"id": "yes2", "parking": True, "distance_m": 600},
                {"id": "unknown1", "parking": None, "distance_m": 20},
                {"id": "unknown2", "parking": None, "distance_m": 21},
            ],
        )
        state = FilterState.model_validate(
            payload(
                [parking(True)],
                result_policy={"limit_per_kind": 1, "uncertain_limit_per_kind": 1},
            )
        )
        group = (await search_filtered_places(db, state)).groups[0]
        assert refs(group.matched) == ["yes1"]
        assert refs(group.uncertain) == ["unknown1"]
        assert group.matched_truncated and group.uncertain_truncated


async def test_historical_false_without_raw_is_unknown():
    async with db_session() as db:
        await seed(
            db,
            [
                {"id": "old-false", "parking": False, "raw": {}},
                {"id": "bad-false", "parking": False, "raw": {"주차 가능여부": "현장 문의"}},
                {"id": "explicit-no", "parking": False},
            ],
        )
        group = (
            await search_filtered_places(db, FilterState.model_validate(payload([parking(False)])))
        ).groups[0]
        assert refs(group.matched) == ["explicit-no"]
        assert set(refs(group.uncertain)) == {"old-false", "bad-false"}
        assert all(h.place.facts.parking is None for h in group.uncertain)
        reasons = {
            h.place.key.ref.removeprefix(PREFIX): h.filter_evaluation.atoms[0].unknown_reason
            for h in group.uncertain
        }
        assert reasons == {"old-false": "insufficient_evidence", "bad-false": "parse_failed"}


async def test_borrowed_parking_filters_and_explains_same_effective_value():
    async with db_session() as db:
        await seed(
            db,
            [
                {"id": "donor", "parking": True, "distance_m": 100},
                {
                    "id": "winner",
                    "source": "kto",
                    "category": "39",
                    "kind": "restaurant",
                    "parking": None,
                    "raw": {"contenttypeid": "39"},
                    "distance_m": 100,
                    "pet": {},
                },
            ],
        )
        # Same-kind donor is necessary for the existing merge authority.
        await db.execute(
            text("""
            UPDATE facility SET kind='restaurant', category3='식당'
            WHERE source='kcisa' AND source_ref=:ref
        """),
            {"ref": PREFIX + "donor"},
        )
        await db.execute(
            text("""
            INSERT INTO facility_link (facility_id, source, source_ref, method, distance_m)
            SELECT winner.id, 'facility', donor.id::text, 'test', 0
            FROM facility winner, facility donor
            WHERE winner.source_ref=:winner AND donor.source_ref=:donor
        """),
            {"winner": PREFIX + "winner", "donor": PREFIX + "donor"},
        )
        group = (
            await search_filtered_places(db, FilterState.model_validate(payload([parking(True)])))
        ).groups[1]
        assert refs(group.matched) == ["winner"]
        assert group.matched[0].place.facts.parking is True
        atom = group.matched[0].filter_evaluation.atoms[0]
        assert atom.borrowed and atom.ref == PREFIX + "donor"


async def test_submeter_distance_precedes_ref_tiebreak_and_cutoff():
    async with db_session() as db:
        await seed(
            db,
            [
                {"id": "a-far", "parking": True, "distance_m": 100.8},
                {"id": "z-near", "parking": True, "distance_m": 100.2},
            ],
        )
        group = (
            await search_filtered_places(
                db,
                FilterState.model_validate(
                    payload(
                        [parking(True)],
                        unknown_policy="exclude",
                        result_policy={"limit_per_kind": 1},
                    )
                ),
            )
        ).groups[0]
        assert refs(group.matched) == ["z-near"]
        assert group.matched_truncated


async def test_medical_unknown_and_precise_order():
    async with db_session() as db:
        for ref, meters in (("z-near", 100.2), ("a-far", 100.8)):
            await db.execute(
                text("""
                INSERT INTO place (kind, name, location, source, source_id, active)
                VALUES ('hospital', :name,
                    ST_Project(ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography,CAST(:meters AS double precision),pi()/2),
                    :source, :ref, true)
            """),
                {
                    "name": PREFIX + ref,
                    "ref": PREFIX + ref,
                    "meters": meters,
                    "lat": TEST_ORIGIN[0],
                    "lng": TEST_ORIGIN[1],
                    "source": MOIS_SOURCES["hospital"].source,
                },
            )
        value = payload(
            [parking(False)],
            candidate_kinds=["hospital"],
            result_policy={"limit_per_kind": 1, "uncertain_limit_per_kind": 1},
        )
        group = (await search_filtered_places(db, FilterState.model_validate(value))).groups[0]
        assert not group.matched
        assert refs(group.uncertain) == ["z-near"]
        assert group.uncertain_truncated


async def test_filter_free_state_preserves_v2_candidates_and_dog_evaluations():
    from daengs_place.place.search import PlaceSearchRequest, search_place_groups

    async with db_session() as db:
        await seed(db, FIXTURE["candidates"])
        dogs = [{"ref": "small", "dog_weight_kg": 5}, {"ref": "large", "dog_weight_kg": 35}]
        state = FilterState.model_validate(
            payload(
                dogs=dogs,
                unknown_policy="exclude",
                result_policy={"limit_per_kind": 20},
                preferences=[parking(True, "prefer") | {"scope_kinds": ["cafe", "restaurant"]}],
            )
        )
        legacy = PlaceSearchRequest(
            **state.spatial.model_dump(),
            kinds=list(state.candidate_kinds),
            name_query=PREFIX,
            preferences={"parking": True},
            dogs=dogs,
        )
        old = await search_place_groups(db, legacy)
        new = await search_filtered_places(db, state)
        for old_group, new_group in zip(old.groups, new.groups, strict=True):
            assert refs(old_group.results) == refs(new_group.matched)
            assert [h.evaluations for h in old_group.results] == [
                h.evaluations for h in new_group.matched
            ]


async def test_duplicate_or_branches_do_not_duplicate_or_boost_a_hit():
    async with db_session() as db:
        await seed(db, FIXTURE["candidates"])
        state = FilterState.model_validate(
            payload(
                any=[
                    {"id": "a", "all": [parking(True, "p1")]},
                    {"id": "b", "all": [parking(True, "p2")]},
                ]
            )
        )
        response = await search_filtered_places(db, state)
        assert refs(response.groups[0].matched) == ["c1", "c4"]
        assert all(
            h.filter_evaluation.matched_branch_ids == ("a", "b") for h in response.groups[0].matched
        )


async def test_preference_stays_inside_its_kind_scope():
    async with db_session() as db:
        await seed(
            db,
            [
                {"id": "near", "parking": False, "distance_m": 50},
                {"id": "far", "parking": True, "distance_m": 450},
                {"id": "r-near", "kind": "restaurant", "parking": False, "distance_m": 50},
                {"id": "r-far", "kind": "restaurant", "parking": True, "distance_m": 450},
            ],
        )
        response = await search_filtered_places(
            db,
            FilterState.model_validate(
                payload(
                    preferences=[parking(True, "pref") | {"scope_kinds": ["cafe"]}],
                )
            ),
        )
        assert refs(response.groups[0].matched) == ["far", "near"]
        assert refs(response.groups[1].matched) == ["r-near", "r-far"]
