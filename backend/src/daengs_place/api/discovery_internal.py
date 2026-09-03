"""Container-network-only Place discovery endpoint.

nginx intentionally has no route for this prefix. The public backend will call it through the
Docker service name in the following adapter PR.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.config import settings
from daengs_place.core.db import get_session
from daengs_place.place.discovery.contract import (
    PlaceDiscoveryData,
    PlaceDiscoveryRequest,
)
from daengs_place.place.discovery.service import PlaceDiscoveryService
from daengs_place.place.intent.service import PlaceIntentSuggestionService
from daengs_place.place.planning.contract import (
    PlaceSearchConditions,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.providers.gemini import (
    GeminiIntentProposer,
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)

router = APIRouter(prefix="/internal/place", tags=["place-discovery-internal"])


class InternalPlaceDiscoveryRequest(PlanningModel):
    """Trusted structured context only; execution budgets remain server-owned."""

    query: str = Field(min_length=1, max_length=1_000)
    spatial: PlaceSpatialConstraint
    conditions: PlaceSearchConditions | None = None

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class PlaceDiscoveryNotConfiguredError(RuntimeError):
    """The optional provider is absent while the rest of Place remains available."""


def get_place_discovery_service() -> PlaceDiscoveryService:
    """Create provider state only when the internal endpoint is actually requested."""

    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        raise PlaceDiscoveryNotConfiguredError("Place intent provider is not configured")
    proposer = GeminiIntentProposer(
        api_key,
        settings.gemini_model,
        timeout_s=settings.gemini_timeout_ms / 1000,
    )
    return PlaceDiscoveryService(PlaceIntentSuggestionService(proposer))


@router.post("/discovery", response_model=PlaceDiscoveryData)
async def discover_place(
    request: InternalPlaceDiscoveryRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PlaceDiscoveryData:
    try:
        service = get_place_discovery_service()
        return await service.discover(
            db,
            PlaceDiscoveryRequest(
                query=request.query,
                spatial=request.spatial,
                conditions=request.conditions,
            ),
        )
    except PlaceDiscoveryNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "place_intent_not_configured",
                "message": "Place discovery is not configured",
            },
        ) from exc
    except GeminiIntentProposerTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "place_intent_provider_timeout",
                "message": "Place discovery provider timed out",
            },
        ) from exc
    except GeminiIntentProposerResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "place_intent_provider_failure",
                "message": "Place discovery provider failed",
            },
        ) from exc
