"""Internal facility-screen entry point; never routed publicly by nginx."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.api.discovery_internal import (
    PlaceDiscoveryNotConfiguredError,
    get_place_discovery_service,
)
from daengs_place.core.db import get_session
from daengs_place.place.discovery.facility import (
    FacilityDiscoveryRequest,
    FacilityInternalAction,
    FacilityInternalResponse,
    continue_facilities,
    start_facilities,
)
from daengs_place.place.providers.gemini import (
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)

router = APIRouter(prefix="/internal/place", tags=["facility-discovery-internal"])


@router.post("/facility-discovery", response_model=FacilityInternalResponse)
async def discover(
    request: FacilityDiscoveryRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FacilityInternalResponse:
    try:
        return await start_facilities(db, request, get_place_discovery_service())
    except PlaceDiscoveryNotConfiguredError as exc:
        raise HTTPException(503, detail={"code": "place_discovery_not_configured"}) from exc
    except (GeminiIntentProposerTimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, detail={"code": "place_discovery_timeout"}) from exc
    except (GeminiIntentProposerResponseError, httpx.RequestError) as exc:
        raise HTTPException(502, detail={"code": "place_discovery_provider_failure"}) from exc


@router.post("/facility-discovery/actions", response_model=FacilityInternalResponse)
async def act(request: FacilityInternalAction, db: Annotated[AsyncSession, Depends(get_session)]):
    try:
        return await continue_facilities(db, request, get_place_discovery_service())
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "facility_invalid_action"}) from exc
    except PlaceDiscoveryNotConfiguredError as exc:
        raise HTTPException(503, detail={"code": "place_discovery_not_configured"}) from exc
