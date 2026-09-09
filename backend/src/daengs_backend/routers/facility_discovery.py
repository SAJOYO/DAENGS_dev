"""Facility-screen AI search, independent of chat persistence and walk context."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from daengs_backend.core.deps import CurrentAppMemberTokenOnly
from daengs_backend.schemas.facility_conversation import ConversationRequest, ConversationResponse
from daengs_backend.schemas.facility_discovery import (
    FacilityActionRequest,
    FacilityDiscoveryRequest,
    FacilityDiscoveryResponse,
)
from daengs_backend.services.facility_conversation import (
    FacilityConversationService,
    get_facility_conversation_service,
)
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    FacilityDiscoveryService,
    get_facility_discovery_service,
    require_active_facility_owner,
)

router = APIRouter(prefix="/app/places", tags=["facility-discovery"])

_ERRORS = {
    "facility_login_required": (401, "다시 로그인해 주세요."),
    "facility_expired": (410, "검색이 만료됐어요. 다시 검색해 주세요."),
    "facility_conflict": (409, "검색 상태가 바뀌었어요. 다시 검색해 주세요."),
    "facility_invalid_action": (422, "현재 검색에서 선택할 수 없는 조건이에요."),
    "facility_session_unavailable": (503, "검색 상태를 저장하거나 불러올 수 없어요."),
    "facility_timeout": (504, "AI 검색 시간이 초과됐어요."),
    "facility_unavailable": (503, "AI 검색 서버에 연결할 수 없어요."),
    "facility_upstream_error": (502, "AI 검색 서버가 요청을 처리하지 못했어요."),
    "facility_invalid_response": (502, "AI 검색 결과를 확인할 수 없어요."),
}


async def facility_owner(user: CurrentAppMemberTokenOnly) -> str:
    try:
        await require_active_facility_owner(user.app_user_id)
    except FacilityDiscoveryError as exc:
        status, message = _ERRORS[exc.code]
        raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc
    return str(user.app_user_id)


@router.post("/conversation", response_model=ConversationResponse)
async def converse(
    request: ConversationRequest,
    owner: Annotated[str, Depends(facility_owner)],
    service: Annotated[FacilityConversationService, Depends(get_facility_conversation_service)],
):
    try:
        return await service.turn(request, owner)
    except FacilityDiscoveryError as exc:
        status_code, message = _ERRORS[exc.code]
        raise HTTPException(status_code, detail={"code": exc.code, "message": message}) from exc


@router.post(
    "/discovery",
    response_model=FacilityDiscoveryResponse,
)
async def discover(
    request: FacilityDiscoveryRequest,
    owner: Annotated[str, Depends(facility_owner)],
    service: Annotated[FacilityDiscoveryService, Depends(get_facility_discovery_service)],
) -> FacilityDiscoveryResponse:
    try:
        return await service.search(request, owner)
    except FacilityDiscoveryError as exc:
        status_code, message = _ERRORS[exc.code]
        raise HTTPException(status_code, detail={"code": exc.code, "message": message}) from exc


@router.post("/discovery/actions", response_model=FacilityDiscoveryResponse)
async def act(
    request: FacilityActionRequest,
    owner: Annotated[str, Depends(facility_owner)],
    service: Annotated[FacilityDiscoveryService, Depends(get_facility_discovery_service)],
):
    try:
        return await service.act(request, owner)
    except FacilityDiscoveryError as exc:
        status, message = _ERRORS[exc.code]
        raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc
