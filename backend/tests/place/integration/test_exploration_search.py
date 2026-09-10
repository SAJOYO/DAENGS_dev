"""The full candidate pool must be omitted before LIMIT, for both SQL paths."""

from sqlalchemy import text

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.service import search_filtered_places
from daengs_place.place.source_catalog import MOIS_SOURCES
from tests.place.integration.test_condition_filters import PREFIX, payload, refs, seed
from tests.place.support.database import TEST_ORIGIN, db_session


async def test_facility_next_and_exclusion_before_limit_with_source_scoped_keys():
    async with db_session() as db:
        await seed(db, [{"id": str(i), "distance_m": i + 1, "parking": True} for i in range(26)])
        state = FilterState.model_validate(
            payload(
                candidate_kinds=["cafe"],
                unknown_policy="exclude",
                result_policy={"limit_per_kind": 20},
            )
        )
        first = (await search_filtered_places(db, state)).groups[0]
        omitted = tuple(h.place.key for h in first.matched)
        second = (await search_filtered_places(db, state, omitted=omitted)).groups[0]
        assert refs(second.matched) == [str(i) for i in range(20, 26)]
        assert first.matched_truncated and not second.matched_truncated
        empty = (
            await search_filtered_places(
                db,
                state,
                omitted=(*omitted, *(h.place.key for h in second.matched)),
            )
        ).groups[0]
        assert not empty.matched and not empty.matched_truncated
        different_source = (
            await search_filtered_places(
                db,
                state,
                omitted=(PlaceRef(source="not-kcisa", ref=PREFIX + "0"),),
            )
        ).groups[0]
        assert refs(different_source.matched)[0] == "0"


async def test_unknown_candidates_have_independent_omissions_and_lookahead():
    async with db_session() as db:
        await seed(
            db,
            [{"id": f"u{i}", "distance_m": i + 1, "parking": None} for i in range(3)]
            + [{"id": "yes", "distance_m": 20, "parking": True}],
        )
        state = FilterState.model_validate(
            payload(
                all=[{"id": "p", "capability": "operations.parking", "op": "eq", "value": True}],
                candidate_kinds=["cafe"],
                result_policy={"limit_per_kind": 1, "uncertain_limit_per_kind": 1},
            )
        )
        first = (await search_filtered_places(db, state)).groups[0]
        second = (
            await search_filtered_places(
                db,
                state,
                omitted=(first.uncertain[0].place.key,),
            )
        ).groups[0]
        assert refs(second.matched) == ["yes"]
        assert refs(second.uncertain) == ["u1"]
        assert second.uncertain_truncated and not second.matched_truncated


async def test_medical_next_and_exclusion_before_limit():
    async with db_session() as db:
        for i in range(26):
            await db.execute(
                text("""
                INSERT INTO place(kind,name,location,source,source_id,active)
                VALUES ('hospital',:ref,
                    ST_Project(ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography,
                               CAST(:meters AS double precision),pi()/2),
                    :source,:ref,true)
            """),
                {
                    "ref": PREFIX + str(i),
                    "meters": i + 1,
                    "lat": TEST_ORIGIN[0],
                    "lng": TEST_ORIGIN[1],
                    "source": MOIS_SOURCES["hospital"].source,
                },
            )
        state = FilterState.model_validate(
            payload(
                candidate_kinds=["hospital"],
                unknown_policy="exclude",
                result_policy={"limit_per_kind": 20},
            )
        )
        first = (await search_filtered_places(db, state)).groups[0]
        second = (
            await search_filtered_places(
                db,
                state,
                omitted=tuple(h.place.key for h in first.matched),
            )
        ).groups[0]
        assert refs(second.matched) == [str(i) for i in range(20, 26)]
        assert first.matched_truncated and not second.matched_truncated
        other_source = (
            await search_filtered_places(
                db,
                state,
                omitted=(PlaceRef(source="other", ref=PREFIX + "0"),),
            )
        ).groups[0]
        assert refs(other_source.matched)[0] == "0"
