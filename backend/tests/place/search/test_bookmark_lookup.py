"""Exact saved keys across regions; use the same real PostGIS facts as normal search."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from daengs_place.ingest.facility_store import upsert_rows
from daengs_place.place.bookmarks import BookmarkLookup, lookup_bookmarks
from tests.place.search.test_search_v2 import _facility_row
from tests.place.support.database import TEST_ORIGIN, db_session


async def test_saved_keys_are_complete_without_radius_and_required_unknown_is_excluded():
    refs = ["test:bookmark:near", "test:bookmark:far", "test:bookmark:unknown"]
    keys = [{"source": "kcisa", "ref": ref} for ref in refs]
    missing = {"source": "kto", "ref": "test:bookmark:missing"}
    async with db_session() as db:
        # Deliberately outside the normal search's maximum 20 km radius.
        await upsert_rows(
            db,
            "kcisa",
            [
                _facility_row(refs[0], "가까운 카페", "cafe", "카페", 100, parking=True),
                _facility_row(refs[1], "먼 카페", "cafe", "카페", 80000, parking=False, raw={"주차 가능여부": "N"}),
                _facility_row(refs[2], "미확인 카페", "cafe", "카페", 200),
            ],
            "2026-09-11",
            datetime.now(UTC),
        )
        all_saved = await lookup_bookmarks(db, BookmarkLookup(keys=[*keys, missing]))
        assert [h.place.key.ref for h in all_saved.hits] == refs
        assert [k.model_dump() for k in all_saved.missing_keys] == [missing]
        assert not all_saved.distance_available
        assert all(h.place.distance_m == 0 for h in all_saved.hits)

        nearby = await lookup_bookmarks(
            db,
            BookmarkLookup(
                keys=keys,
                filters={
                    "lat": TEST_ORIGIN[0],
                    "lng": TEST_ORIGIN[1],
                    "radius_m": 3000,
                },
            ),
        )
        assert {h.place.key.ref for h in nearby.hits} == {refs[0], refs[2]}
        assert nearby.distance_available and nearby.missing_keys == []
        required = await lookup_bookmarks(
            db,
            BookmarkLookup(
                keys=keys,
                filters={
                    "hard": {
                        "all": [
                            {
                                "id": "no-parking",
                                "capability": "operations.parking",
                                "op": "eq",
                                "value": False,
                            }
                        ]
                    },
                },
            ),
        )
        assert [h.place.key.ref for h in required.hits] == [refs[1]]
        by_name = await lookup_bookmarks(
            db, BookmarkLookup(keys=keys, filters={"name_query": "먼"})
        )
        assert [h.place.key.ref for h in by_name.hits] == [refs[1]]
        other_kind = await lookup_bookmarks(
            db, BookmarkLookup(keys=keys, filters={"kinds": ["hospital"]})
        )
        assert other_kind.hits == [] and other_kind.missing_keys == []
        # db_session rolls back only this transaction and its fixtures.


async def test_medical_lookup_ignores_radius_but_excludes_inactive_and_unrequested():
    source = "public:mois:animal_hospital"
    async with db_session() as db:
        for ref, active in [
            ("test:bookmark:medical", True),
            ("test:bookmark:closed", False),
            ("test:bookmark:other", True),
        ]:
            await db.execute(
                text("""INSERT INTO place (kind,name,address,location,source,source_id,active)
                VALUES ('hospital','먼 동물병원','테스트',ST_SetSRID(ST_MakePoint(130,38),4326)::geography,:source,:ref,:active)"""),
                {"source": source, "ref": ref, "active": active},
            )
        result = await lookup_bookmarks(
            db,
            BookmarkLookup(
                keys=[
                    {"source": source, "ref": "test:bookmark:medical"},
                    {"source": source, "ref": "test:bookmark:closed"},
                ]
            ),
        )
        assert [h.place.key.ref for h in result.hits] == ["test:bookmark:medical"]
        assert [k.ref for k in result.missing_keys] == ["test:bookmark:closed"]


@pytest.mark.parametrize(
    "filters",
    [
        {"radius_m": 3000},
        {"lat": 37},
        {"kinds": ["cafe", "cafe"]},
        {"name_query": "x" * 121},
        {"unknown_policy": "include"},
    ],
)
def test_invalid_filter_scope_is_rejected(filters):
    with pytest.raises(ValidationError):
        BookmarkLookup(keys=[], filters=filters)
