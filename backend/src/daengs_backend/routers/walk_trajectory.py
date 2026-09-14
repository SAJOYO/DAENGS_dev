"""Owner-only candidate calculation with explicit wire version negotiation."""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.repositories import walk_measurement as measurement_repo
from daengs_backend.repositories import walk_motion as repo
from daengs_backend.schemas.walk_measurement import (
    MeasurementSummary,
    MeasurementVersion,
    RoutePage,
)
from daengs_backend.schemas.walk_trajectory import (
    MAX_POINTS,
    VERSION,
    MeasurementId,
    TrajectoryCalculation,
    TrajectoryVersion,
)
from daengs_backend.services import walk_measurement as measurements
from daengs_backend.services import walk_trajectory as service
from daengs_backend.services.walk_session.errors import WalkNotFoundError
from daengs_backend.services.walk_session.motion import MotionUnavailable
from daengs_backend.services.walk_session.motion_contract import MotionConflict

router = APIRouter(tags=["walk-trajectory"])
Session = Annotated[AsyncSession, Depends(get_session)]
PRIVATE = {"Cache-Control": "private, no-store", "Vary": "Authorization"}


@router.get("/trajectory-capabilities")
async def capabilities(user: CurrentAppUser, session: Session, response: Response):
    try:
        available = await repo.available(session)
        stored = await measurement_repo.available(session)
    finally:
        await session.rollback()
    response.headers.update(PRIVATE)
    return {
        "calculation_versions": [VERSION] if available else [],
        "max_points": MAX_POINTS,
        "delivery": "whole_result",
        "status": "candidate",
        "device_result_verified": False,
        "observation_policy_status": "experimental",
        "persisted_measurements_supported": stored,
        "measurement_versions": ["walk-measurement-v1"] if stored else [],
        "active_read_view_supported": False,
    }


@router.get(
    "/{walk_id}/trajectory-calculation",
    response_model=TrajectoryCalculation,
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Quoted SHA-256 of the complete UTF-8 JSON response bytes.",
                    "schema": {"type": "string"},
                }
            }
        },
        404: {"description": "Owner-scoped walk or motion backup not found."},
        409: {"description": "Incomplete/invalid evidence, changed measurement, or point limit."},
        503: {"description": "Motion backup storage is unavailable."},
    },
)
async def calculation(
    walk_id: UUID,
    user: CurrentAppUser,
    session: Session,
    version: Annotated[TrajectoryVersion, Query()],
    expected_measurement_id: Annotated[MeasurementId | None, Query()] = None,
):
    try:
        content = await service.calculate(
            session, user.app_user_id, walk_id, expected_measurement_id=expected_measurement_id
        )
    except WalkNotFoundError:
        raise HTTPException(404, "산책 측정 자료를 찾을 수 없습니다.", headers=PRIVATE) from None
    except MotionUnavailable:
        raise HTTPException(
            503, detail={"code": "motion_storage_unavailable"}, headers=PRIVATE
        ) from None
    except MotionConflict as error:
        raise HTTPException(409, detail={"code": error.code}, headers=PRIVATE) from None
    return Response(
        content,
        media_type="application/json",
        headers={**PRIVATE, "ETag": '"' + hashlib.sha256(content).hexdigest() + '"'},
    )


async def measurement_response(work):
    try:
        content = await work
    except WalkNotFoundError:
        raise HTTPException(404, "산책 측정 결과를 찾을 수 없습니다.", headers=PRIVATE) from None
    except MotionUnavailable:
        raise HTTPException(
            503, detail={"code": "measurement_storage_unavailable"}, headers=PRIVATE
        ) from None
    except MotionConflict as error:
        raise HTTPException(409, detail={"code": error.code}, headers=PRIVATE) from None
    return Response(
        content,
        media_type="application/json",
        headers={**PRIVATE, "ETag": '"' + hashlib.sha256(content).hexdigest() + '"'},
    )


@router.post("/{walk_id}/measurements", response_model=MeasurementSummary)
async def prepare_measurement(
    walk_id: UUID,
    user: CurrentAppUser,
    session: Session,
    version: Annotated[MeasurementVersion, Query()],
):
    return await measurement_response(measurements.prepare(session, user.app_user_id, walk_id))


@router.get("/{walk_id}/measurements/{measurement_id}", response_model=MeasurementSummary)
async def measurement_summary(
    walk_id: UUID,
    measurement_id: MeasurementId,
    user: CurrentAppUser,
    session: Session,
    version: Annotated[MeasurementVersion, Query()],
):
    return await measurement_response(
        measurements.read(session, user.app_user_id, walk_id, measurement_id)
    )


@router.get("/{walk_id}/measurements/{measurement_id}/chunks/{index}", response_model=RoutePage)
async def measurement_chunk(
    walk_id: UUID,
    measurement_id: MeasurementId,
    index: int,
    user: CurrentAppUser,
    session: Session,
    version: Annotated[MeasurementVersion, Query()],
):
    if not 0 <= index < 2000:
        raise HTTPException(422, "경로 청크 범위를 확인해 주세요.", headers=PRIVATE)
    return await measurement_response(
        measurements.read(session, user.app_user_id, walk_id, measurement_id, index)
    )
