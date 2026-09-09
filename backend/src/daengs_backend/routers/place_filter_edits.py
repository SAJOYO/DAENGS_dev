"""Authenticated public filter-edit gateway. Legacy discovery remains compatible."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from daengs_backend.routers.facility_discovery import _ERRORS, facility_owner
from daengs_backend.schemas.place_filter_edits import (
    FilterEditAction,
    FilterEditRequest,
    FilterEditResponse,
)
from daengs_backend.services.facility_discovery import FacilityDiscoveryError
from daengs_backend.services.place_filter_edits import FilterEditService, get_filter_edit_service

router = APIRouter(prefix="/app/places/filter-edits", tags=["place-filter-edits"])


@router.post("", response_model=FilterEditResponse)
async def propose(
    request: FilterEditRequest,
    owner: Annotated[str, Depends(facility_owner)],
    service: Annotated[FilterEditService, Depends(get_filter_edit_service)],
):
    try:
        return await service.propose(request, owner)
    except FacilityDiscoveryError as exc:
        status, message = _ERRORS[exc.code]
        raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc


@router.post("/actions", response_model=FilterEditResponse)
async def apply(
    request: FilterEditAction,
    owner: Annotated[str, Depends(facility_owner)],
    service: Annotated[FilterEditService, Depends(get_filter_edit_service)],
):
    try:
        return await service.apply(request, owner)
    except FacilityDiscoveryError as exc:
        status, message = _ERRORS[exc.code]
        raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc
