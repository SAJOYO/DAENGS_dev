"""Private filter compilation/execution. Owner and session CAS belong to backend."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictBool, StrictInt, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.api.places_v3 import FilterSearchResponse
from daengs_place.core.config import settings
from daengs_place.core.db import get_session
from daengs_place.place.filters.contract import FilterState, Identifier
from daengs_place.place.filters.edits import CompiledEdit, EditProposal, compile_edits
from daengs_place.place.filters.proposer import GeminiFilterProposer
from daengs_place.place.filters.service import search_filtered_places
from daengs_place.place.planning.contract import PlanningModel
from daengs_place.place.providers.gemini import (
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)

router = APIRouter(prefix="/internal/place/filter-edits", tags=["filter-edits-internal"])


class ProposeRequest(PlanningModel):
    query: str = Field(min_length=1, max_length=1000)
    base_state: FilterState

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class ExecuteRequest(ProposeRequest):
    proposal: EditProposal
    confirmed: StrictBool = False
    revision: StrictInt = Field(ge=1)
    search_request_id: Identifier


def get_proposer():
    key = settings.gemini_api_key.get_secret_value().strip()
    if not key:
        raise HTTPException(503, detail={"code": "filter_proposer_not_configured"})
    return GeminiFilterProposer(
        key, settings.gemini_model, timeout_s=settings.gemini_timeout_ms / 1000
    )


@router.post("", response_model=CompiledEdit)
async def propose(
    request: ProposeRequest, proposer: Annotated[GeminiFilterProposer, Depends(get_proposer)]
):
    try:
        proposal = await proposer.propose(request.query, request.base_state)
        return compile_edits(request.base_state, request.query, proposal)
    except GeminiIntentProposerTimeoutError as exc:
        raise HTTPException(504, detail={"code": "filter_proposer_timeout"}) from exc
    except (GeminiIntentProposerResponseError, ValueError) as exc:
        raise HTTPException(502, detail={"code": "filter_proposer_invalid"}) from exc


@router.post("/execute", response_model=FilterSearchResponse)
async def execute(request: ExecuteRequest, db: Annotated[AsyncSession, Depends(get_session)]):
    try:
        compiled = compile_edits(request.base_state, request.query, request.proposal)
        if compiled.proposed_state is None or (
            compiled.requires_confirmation and not request.confirmed
        ):
            raise ValueError("unresolved_edit")
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "unresolved_edit"}) from exc
    try:
        async with asyncio.timeout(15):
            result = await search_filtered_places(db, compiled.proposed_state)
    except (SQLAlchemyError, TimeoutError) as exc:
        raise HTTPException(503, detail={"code": "filter_search_unavailable"}) from exc
    return FilterSearchResponse(
        **result.model_dump(),
        revision=request.revision,
        search_request_id=request.search_request_id,
    )
