"""Authenticated photo-metadata transport; capabilities do not touch the optional table."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_photo import PhotoManifestResponse, PhotoManifestWrite
from daengs_backend.services.walk_photos import api as service

router = APIRouter(prefix="/app/walks", tags=["walk-photo-metadata"])


@router.get("/photo-metadata/capabilities")
async def capabilities(user: CurrentAppUser):
    return service.capabilities()


@router.get("/{walk_id}/photo-metadata", response_model=PhotoManifestResponse)
async def get_metadata(
    walk_id: uuid.UUID, user: CurrentAppUser, session: Annotated[AsyncSession, Depends(get_session)]
):
    try:
        return await service.get(session, user.app_user_id, walk_id)
    except service.PhotoNotFound:
        raise HTTPException(404, "사진 메타데이터를 찾을 수 없습니다.") from None


@router.put("/{walk_id}/photo-metadata", response_model=PhotoManifestResponse)
async def put_metadata(
    walk_id: uuid.UUID,
    body: PhotoManifestWrite,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    try:
        return await service.put(session, user.app_user_id, walk_id, body)
    except service.PhotoNotFound:
        raise HTTPException(404, "사진 메타데이터를 찾을 수 없습니다.") from None
    except service.PhotoConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except service.PhotoInvalid as exc:
        raise HTTPException(422, str(exc)) from None
