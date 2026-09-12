"""Precision extension routes; registered under the existing /app/walks router."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_motion import MotionComplete
from daengs_backend.schemas.walk_precision import (
    PrecisionChunk,
    PrecisionChunkResponse,
    PrecisionManifest,
    PrecisionStatus,
)
from daengs_backend.services import walk_precision as service
from daengs_backend.services.walk import WalkNotFoundError
from daengs_backend.services.walk_motion import MotionUnavailable
from daengs_backend.services.walk_motion_contract import MotionConflict

router = APIRouter(tags=["walk-motion-precision"])
Session = Annotated[AsyncSession, Depends(get_session)]
Index = Annotated[int, Path(ge=0, le=390)]


async def _call(operation):
    try:
        return await operation
    except WalkNotFoundError:
        raise HTTPException(404, "정밀 측정 자료를 찾을 수 없습니다.") from None
    except MotionUnavailable:
        raise HTTPException(503, detail={"code": "precision_storage_unavailable"}) from None
    except MotionConflict as e:
        raise HTTPException(409, detail={"code": e.code}) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(409, detail={"code": "precision_invalid_input"}) from None


@router.put("/{walk_id}/motion-precision", response_model=PrecisionStatus)
async def begin(
    walk_id: uuid.UUID, body: PrecisionManifest, user: CurrentAppUser, session: Session
):
    return await _call(service.begin(session, user.app_user_id, walk_id, body))


@router.put("/{walk_id}/motion-precision/chunks/{index}", response_model=PrecisionChunkResponse)
async def upload(
    walk_id: uuid.UUID, index: Index, body: PrecisionChunk, user: CurrentAppUser, session: Session
):
    return await _call(service.upload(session, user.app_user_id, walk_id, index, body))


@router.post("/{walk_id}/motion-precision/complete", response_model=PrecisionStatus)
async def complete(
    walk_id: uuid.UUID, body: MotionComplete, user: CurrentAppUser, session: Session
):
    return await _call(service.complete(session, user.app_user_id, walk_id, body))


@router.get("/{walk_id}/motion-precision", response_model=PrecisionStatus)
async def read(walk_id: uuid.UUID, user: CurrentAppUser, session: Session):
    return await _call(service.read(session, user.app_user_id, walk_id))


@router.get("/{walk_id}/motion-precision/chunks/{index}", response_model=PrecisionChunkResponse)
async def read_chunk(walk_id: uuid.UUID, index: Index, user: CurrentAppUser, session: Session):
    return await _call(service.read(session, user.app_user_id, walk_id, index))
