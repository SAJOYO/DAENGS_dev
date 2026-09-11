"""Independent GPS backup capability; does not imply v2 pins or server motion parity."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.repositories import walk_motion as repo
from daengs_backend.schemas.walk_motion import (
    BACKUP_VERSION,
    CHUNK_SIZE,
    MAX_EPOCHS,
    MAX_POINTS,
    MotionBackupStatus,
    MotionChunkResponse,
    MotionChunkUpload,
    MotionComplete,
    MotionManifest,
)
from daengs_backend.services import walk_motion as service
from daengs_backend.services.walk import WalkNotFoundError
from daengs_backend.services.walk_motion_contract import MotionConflict

router = APIRouter(prefix="/app/walks", tags=["walk-motion-backup"])
Session = Annotated[AsyncSession, Depends(get_session)]
ChunkIndex = Annotated[int, Path(ge=0, lt=(MAX_POINTS + CHUNK_SIZE - 1) // CHUNK_SIZE)]


async def _call(operation):
    try:
        return await operation
    except WalkNotFoundError:
        raise HTTPException(404, "산책 측정 자료를 찾을 수 없습니다.") from None
    except service.MotionUnavailable:
        raise HTTPException(503, detail={"code": "motion_storage_unavailable"}) from None
    except MotionConflict as error:
        raise HTTPException(409, detail={"code": error.code}) from None


@router.get("/motion-capabilities")
async def capabilities(user: CurrentAppUser, session: Session):
    return {
        "version": BACKUP_VERSION,
        "backup_supported": await repo.available(session),
        "calculation_verified": False,
        "chunk_size": CHUNK_SIZE,
        "max_points": MAX_POINTS,
        "max_epochs": MAX_EPOCHS,
    }


@router.put("/{walk_id}/motion-backup", response_model=MotionBackupStatus)
async def begin(walk_id: uuid.UUID, body: MotionManifest, user: CurrentAppUser, session: Session):
    return await _call(service.begin(session, user.app_user_id, walk_id, body))


@router.put("/{walk_id}/motion-backup/chunks/{index}", response_model=MotionChunkResponse)
async def upload_chunk(
    walk_id: uuid.UUID,
    index: ChunkIndex,
    body: MotionChunkUpload,
    user: CurrentAppUser,
    session: Session,
):
    return await _call(service.upload_chunk(session, user.app_user_id, walk_id, index, body))


@router.post("/{walk_id}/motion-backup/complete", response_model=MotionBackupStatus)
async def complete(
    walk_id: uuid.UUID, body: MotionComplete, user: CurrentAppUser, session: Session
):
    return await _call(service.complete(session, user.app_user_id, walk_id, body))


@router.get("/{walk_id}/motion-backup", response_model=MotionBackupStatus)
async def read(walk_id: uuid.UUID, user: CurrentAppUser, session: Session):
    return await _call(service.read(session, user.app_user_id, walk_id))


@router.get("/{walk_id}/motion-backup/chunks/{index}", response_model=MotionChunkResponse)
async def read_chunk(walk_id: uuid.UUID, index: ChunkIndex, user: CurrentAppUser, session: Session):
    return await _call(service.read(session, user.app_user_id, walk_id, index))
