"""Bounded saved-record lookup. Place owns facts, never member identity or ownership."""

import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from sqlalchemy import text

from daengs_place.core.clock import SystemClock
from daengs_place.geo.contract import SearchMust, SearchPlan
from daengs_place.geo.search import find_authoritative_places
from daengs_place.place.adapters import facility_place_result, medical_place_result
from daengs_place.place.contracts import PlaceRef
from daengs_place.place.facility_resolver import (
    CANONICAL_SOURCES,
    FACILITY_CANDIDATES_SQL,
    MEDICAL,
    facility_from_row,
)
from daengs_place.place.filters.contract import HardFilters
from daengs_place.place.filters.evaluation import evaluate_atoms
from daengs_place.place.filters.resolver import _PARKING
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlaceKind
from daengs_place.place.search import PerDogEvaluation, PlaceDogSnapshot, PlaceSearchHit, _hit
from daengs_place.place.source_catalog import MOIS_SOURCES


class BookmarkKey(PlaceRef):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["kcisa", "kto", "public:mois:animal_hospital", "public:mois:animal_pharmacy"]
    ref: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")


class BookmarkFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float | None = Field(None, ge=32, le=40)
    lng: float | None = Field(None, ge=123, le=133)
    radius_m: int | None = Field(None, ge=100, le=20000)
    kinds: list[PlaceKind] = Field(default_factory=list, max_length=18)
    name_query: PlaceNameQuery = ""
    parking: bool = False
    hard: HardFilters = Field(default_factory=HardFilters)
    dogs: list[PlaceDogSnapshot] = Field(default_factory=list, max_length=20)
    excluded_keys: list[PlaceRef] = Field(default_factory=list, max_length=120)

    @model_serializer(mode="wrap")
    def omit_empty_exclusions(self, handler):
        data = handler(self)
        if not self.excluded_keys:
            data.pop("excluded_keys", None)
        return data

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        if (self.lat is None) != (self.lng is None) or (self.radius_m and self.lat is None):
            raise ValueError("radius requires an origin")
        if len(set(self.kinds)) != len(self.kinds) or len({d.ref for d in self.dogs}) != len(
            self.dogs
        ):
            raise ValueError("duplicate kinds or dogs")
        return self


class BookmarkLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[BookmarkKey] = Field(max_length=200)
    filters: BookmarkFilters = Field(default_factory=BookmarkFilters)

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("duplicate saved keys")
        return self


class BookmarkLookupResult(BaseModel):
    filters: BookmarkFilters
    distance_available: bool
    hits: list[PlaceSearchHit]
    missing_keys: list[BookmarkKey]


def facility_lookup_sql():
    spatial = "AND ST_DWithin(f.location, o.geom, :radius_m)"
    # Keep canonical merge and identity exclusion exactly shared with ordinary search.
    assert FACILITY_CANDIDATES_SQL.count(spatial) == 1
    return text(
        FACILITY_CANDIDATES_SQL.replace(
            spatial,
            """
      AND EXISTS (SELECT 1 FROM jsonb_to_recordset(CAST(:saved_keys AS jsonb))
          AS saved(source text, ref text)
          WHERE saved.source=f.source AND saved.ref=f.source_ref)
    """,
        )
        + f"SELECT merged.*, {_PARKING} AS filter_parking FROM merged"
    )


def matches(place, filters):
    if place.key in filters.excluded_keys:
        return False
    if filters.kinds and place.match.kind not in filters.kinds:
        return False
    if filters.name_query.casefold() not in place.name.casefold():
        return False
    if filters.radius_m is not None and place.distance_m > filters.radius_m:
        return False
    exclusive = place.facts.pet_access.exclusive if place.facts.pet_access else None
    args = (place.match.kind, place.facts.parking, exclusive)
    if evaluate_atoms(filters.hard.all, *args) is not True:
        return False
    return not filters.hard.any or any(
        evaluate_atoms(branch.all, *args) is True for branch in filters.hard.any
    )


async def lookup_bookmarks(db, request: BookmarkLookup) -> BookmarkLookupResult:
    f = request.filters
    found = {}
    # No fabricated user location: placeholder only supports existing adapters; distance is hidden.
    lat, lng = (f.lat, f.lng) if f.lat is not None else (37.0, 127.0)
    facility_keys = [k for k in request.keys if k.source in CANONICAL_SOURCES]
    if facility_keys:
        rows = (
            await db.execute(
                facility_lookup_sql(),
                {
                    "lat": lat,
                    "lng": lng,
                    "kind": None,
                    "name_query": "",
                    "medical": list(MEDICAL),
                    "require_canonical_identity": True,
                    "canonical_sources": list(CANONICAL_SOURCES),
                    "saved_keys": json.dumps([k.model_dump() for k in facility_keys]),
                },
            )
        ).all()
        for row in rows:
            place = facility_place_result(facility_from_row(row, set()))
            # False must be supported by source evidence, like the existing required-filter search.
            place.facts.parking = row.filter_parking
            found[(place.key.source, place.key.ref)] = place
    for kind, source in MOIS_SOURCES.items():
        refs = tuple(k.ref for k in request.keys if k.source == source.source)
        if not refs:
            continue
        rows = await find_authoritative_places(
            db,
            SearchPlan(
                must=SearchMust(
                    lat=lat,
                    lng=lng,
                    radius_m=20000,
                    kind=kind,
                    limit=len(refs),
                    judge_at=SystemClock().now(),
                )
            ),
            source=source.source,
            included_source_refs=refs,
            precise_order=True,
        )
        for row in rows:
            place = medical_place_result(row)
            found[(place.key.source, place.key.ref)] = place
    requested = {(k.source, k.ref) for k in request.keys}
    if not set(found).issubset(requested):
        raise RuntimeError("lookup returned an unrequested identity")
    hits, missing = [], []
    for key in request.keys:
        place = found.get((key.source, key.ref))
        if place is None:
            missing.append(key)
        elif matches(place, f):
            hit = _hit(place, None)
            hit.evaluations.dogs = [
                PerDogEvaluation(
                    ref=dog.ref,
                    dog_access=(evaluation := _hit(place, dog).evaluations).dog_access,
                    restrictions=evaluation.restrictions,
                )
                for dog in f.dogs
            ]
            if f.lat is None:
                hit.place.distance_m = (
                    0  # Consumers must use distance_available, not this placeholder.
                )
            hits.append(hit)
    order_bookmark_hits(hits, f)
    return BookmarkLookupResult(
        filters=f, distance_available=f.lat is not None, hits=hits, missing_keys=missing
    )


def order_bookmark_hits(hits, f):
    if f.lat is not None:
        hits.sort(
            key=lambda h: (
                h.place.distance_m // 500 if f.parking else h.place.distance_m,
                0 if f.parking and h.place.facts.parking is True else 1,
                h.place.distance_m,
                h.place.key.source,
                h.place.key.ref,
            )
        )
    elif f.parking:
        # Without an origin there are no distance bands. Preserve saved order within
        # each group; unknown and false remain visible, after confirmed parking.
        hits.sort(key=lambda h: h.place.facts.parking is not True)
