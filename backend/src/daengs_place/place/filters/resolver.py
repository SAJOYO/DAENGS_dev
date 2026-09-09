"""Bounded filters over the existing canonical facility merge, before any LIMIT."""

from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.geo.ranking import DISTANCE_BAND_M
from daengs_place.place.facility_resolver import (
    CANONICAL_SOURCES,
    FACILITY_CANDIDATES_SQL,
    MEDICAL,
    FacilityOut,
    facility_from_row,
)
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.sql import compile_filter_sql

# Historical false columns may mean "not Y". Only a preserved N proves false.
# Missing historical raw does not erase an affirmative Y-derived value.
_PARKING = """
CASE
 WHEN (CASE WHEN parking_borrowed THEN borrowed_parking_present
            ELSE source_parking_present END)
 THEN CASE upper(left(btrim(
      CASE WHEN parking_borrowed THEN borrowed_parking_raw
           ELSE source_parking_raw END), 1))
      WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE ELSE NULL END
 WHEN parking IS TRUE THEN TRUE
 ELSE NULL
END
"""


async def resolve_filtered_facilities(
    db: AsyncSession,
    state: FilterState,
    kind: str,
) -> tuple[list[FacilityOut], list[FacilityOut], bool, bool]:
    compiled = compile_filter_sql(state)
    prefer_parking = any(kind in p.scope_kinds for p in state.preferences)
    order = (
        "floor(distance_m / :band_m), "
        "CASE WHEN :prefer_parking AND filter_parking IS TRUE THEN 0 ELSE 1 END, "
        "distance_m, source, source_ref, id"
        if prefer_parking
        else "distance_m, source, source_ref, id"
    )
    # SQL fragments come exclusively from constants/whitelisted compiler columns.
    query = text(
        FACILITY_CANDIDATES_SQL
        + f"""
, filter_facts AS (
    SELECT merged.*, {_PARKING} AS filter_parking,
           pet_exclusive AS filter_exclusive
    FROM merged
), evaluated AS (
    SELECT filter_facts.*, {compiled.expression} AS filter_value FROM filter_facts
), ranked AS (
    SELECT evaluated.*,
           row_number() OVER (PARTITION BY filter_value ORDER BY {order}) AS filter_rank
    FROM evaluated
    WHERE filter_value IS TRUE OR (:include_unknown AND filter_value IS NULL)
)
SELECT * FROM ranked
WHERE filter_rank <= CASE WHEN filter_value IS TRUE THEN :matched_limit + 1
                         ELSE :uncertain_limit + 1 END
ORDER BY filter_value DESC NULLS LAST, filter_rank
"""
    )
    policy = state.result_policy
    rows = (
        await db.execute(
            query,
            {
                **compiled.parameters,
                **state.spatial.model_dump(),
                "kind": kind,
                "medical": list(MEDICAL),
                "require_canonical_identity": True,
                "canonical_sources": list(CANONICAL_SOURCES),
                "name_query": state.name_query,
                "band_m": DISTANCE_BAND_M,
                "prefer_parking": prefer_parking,
                "include_unknown": state.unknown_policy == "separate",
                "matched_limit": policy.limit_per_kind,
                "uncertain_limit": policy.uncertain_limit_per_kind,
            },
        )
    ).all()
    buckets: dict[bool | None, list[FacilityOut]] = {True: [], None: []}
    for row in rows:
        values = dict(row._mapping)
        # The fact displayed and explained must be exactly the fact used by SQL.
        values["parking"] = values["filter_parking"]
        values["pet_exclusive"] = values["filter_exclusive"]
        facility = facility_from_row(
            SimpleNamespace(**values), {"parking"} if prefer_parking else set()
        )
        if row.filter_parking is None:
            raw = row.borrowed_parking_raw if row.parking_borrowed else row.source_parking_raw
            present = (
                row.borrowed_parking_present if row.parking_borrowed else row.source_parking_present
            )
            reason = "not_provided"
            if present and raw and raw.strip() not in {"", "정보없음", "-", "NULL"}:
                reason = "parse_failed"
            elif not present and row.parking is False:
                reason = "insufficient_evidence"
            facility.filter_unknown_reasons["operations.parking"] = reason
        if row.filter_exclusive is None:
            raw = (row.pet or {}).get("exclusive")
            facility.filter_unknown_reasons["pet_access.exclusive"] = (
                "insufficient_evidence" if raw else "not_provided"
            )
        buckets[row.filter_value].append(facility)
    matched, uncertain = buckets[True], buckets[None]
    return (
        matched[: policy.limit_per_kind],
        uncertain[: policy.uncertain_limit_per_kind],
        len(matched) > policy.limit_per_kind,
        len(uncertain) > policy.uncertain_limit_per_kind,
    )
