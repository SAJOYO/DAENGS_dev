"""Authenticated walk-owned storyboard generation and current-source read endpoint."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_storyboard import StoryboardRequest, StoryboardResponse
from daengs_backend.services import walk_storyboard as service
from daengs_backend.services.walk_storyboard_context import lookup_contexts

router = APIRouter(prefix="/app/walks", tags=["walk-storyboard"])


def get_context_lookup():
    return lookup_contexts


@router.get("/{walk_id}/storyboard", response_model=StoryboardResponse)
async def get_storyboard(
    walk_id: uuid.UUID, user: CurrentAppUser, session: Annotated[AsyncSession, Depends(get_session)]
):
    try:
        return await service.get(session, user.app_user_id, walk_id)
    except service.StoryboardNotFound:
        raise HTTPException(404, "산책 기록을 찾을 수 없습니다.") from None
    except service.StoryboardConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{walk_id}/storyboard", response_model=StoryboardResponse)
async def generate_storyboard(
    walk_id: uuid.UUID,
    body: StoryboardRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    lookup: Annotated[Callable, Depends(get_context_lookup)],
):
    try:
        return await service.generate(session, user.app_user_id, walk_id, body, lookup)
    except service.StoryboardNotFound:
        raise HTTPException(404, "산책 기록을 찾을 수 없습니다.") from None
    except service.StoryboardConflict as exc:
        raise HTTPException(409, str(exc)) from None
