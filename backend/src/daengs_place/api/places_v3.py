"""Public, stateless manual filter search. Revision is a client correlation token, not CAS."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictInt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.db import get_session
from daengs_place.place.filters.capabilities import CAPABILITIES
from daengs_place.place.filters.contract import FilterState, Identifier
from daengs_place.place.filters.service import FilterResponse, search_filtered_places
from daengs_place.place.planning.contract import PlaceKind, PlanningModel

router = APIRouter(prefix="/v3/places", tags=["places-v3"])


class FilterSearchRequest(PlanningModel):
    revision: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    search_request_id: Identifier
    state: FilterState


class FilterSearchResponse(FilterResponse):
    revision: int
    search_request_id: str


class PublicCapability(PlanningModel):
    id: str
    label: str
    value_type: str
    operators: tuple[str, ...]
    prefer_values: tuple[bool, ...]
    fact_path: str
    always_unknown_kinds: tuple[PlaceKind, ...]


class FilterCapabilities(PlanningModel):
    contract_version: Literal["place-filter-v1"] = "place-filter-v1"
    candidate_kinds: tuple[PlaceKind, ...] = tuple(PlaceKind)
    max_candidate_kinds: int = 6
    max_branches: int = 4
    max_atoms_per_group: int = 8
    max_hard_atoms: int = 24
    unknown_policies: tuple[str, ...] = ("exclude", "separate")
    capabilities: tuple[PublicCapability, ...]


@router.get("/capabilities", response_model=FilterCapabilities)
async def capabilities():
    return FilterCapabilities(
        capabilities=tuple(
            PublicCapability(
                id=s.id,
                label=s.label,
                value_type=s.value_type,
                operators=s.operators,
                prefer_values=s.prefer_values,
                fact_path=s.fact_path,
                always_unknown_kinds=()
                if s.id == "purpose.kind"
                else (
                    PlaceKind.HOSPITAL,
                    PlaceKind.PHARMACY,
                ),
            )
            for s in CAPABILITIES
        )
    )


@router.post("/search", response_model=FilterSearchResponse)
async def search(
    request: FilterSearchRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
):
    try:
        async with asyncio.timeout(15):
            result = await search_filtered_places(db, request.state)
    except (SQLAlchemyError, TimeoutError) as exc:
        # Never disguise partial/failed retrieval as a complete, empty search.
        raise HTTPException(503, detail={"code": "filter_search_unavailable"}) from exc
    return FilterSearchResponse(
        **result.model_dump(),
        revision=request.revision,
        search_request_id=request.search_request_id,
    )
