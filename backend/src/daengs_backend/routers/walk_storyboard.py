"""Authenticated walk-owned storyboard generation and current-source read endpoint."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_storyboard import (
    MAX_PREPARATION_BUDGET_MS,
    BundleFormat,
    DiaryStoryboardResponse,
    StoryboardRequest,
    StoryboardResponse,
)
from daengs_backend.services import walk_storyboard as service
from daengs_backend.services.walk_storyboard_context import lookup_contexts
from daengs_backend.services.walk_storyboard_titles import title_storyboard
from daengs_walk.diary_board_output import BOARD_FORMAT

router = APIRouter(prefix="/app/walks", tags=["walk-storyboard"])


def get_context_lookup():
    return lookup_contexts


def get_title_generator():
    return title_storyboard


def get_diary_writer() -> Callable | None:
    # Stored-format negotiation may change the requested format. Let the service
    # choose its default writer from the negotiated input; overrides stay explicit.
    return None


@router.get("/storyboard/capabilities")
async def capabilities(user: CurrentAppUser):
    from daengs_backend.config import settings

    return {
        "diary_formats": ["walk-diary-bundle-v1", BOARD_FORMAT]
        if settings.walk_diary_enabled
        else [],
        "target_scene_count": {"min": 1, "max": 50},
        "photo_manifest_required_if_available": True,
        "diary_publication": {"format": BOARD_FORMAT, "budget_ms": MAX_PREPARATION_BUDGET_MS}
        if settings.walk_diary_enabled
        else None,
    }


@router.get("/{walk_id}/storyboard", response_model=StoryboardResponse | DiaryStoryboardResponse)
async def get_storyboard(
    walk_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    bundle_format: BundleFormat = "walk-storyboard-candidates-v1",
    target_scene_count: Annotated[int | None, Query(ge=1, le=50)] = None,
):
    try:
        if bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT}:
            return await service.get(
                session,
                user.app_user_id,
                walk_id,
                bundle_format,
                target_scene_count=target_scene_count,
            )
        return await service.get(session, user.app_user_id, walk_id, bundle_format)
    except service.StoryboardNotFound:
        raise HTTPException(404, "산책 기록을 찾을 수 없습니다.") from None
    except service.StoryboardConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{walk_id}/storyboard", response_model=StoryboardResponse | DiaryStoryboardResponse)
async def generate_storyboard(
    walk_id: uuid.UUID,
    body: StoryboardRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    lookup: Annotated[Callable, Depends(get_context_lookup)],
    titles: Annotated[Callable, Depends(get_title_generator)],
    diary_writer: Annotated[Callable | None, Depends(get_diary_writer)],
):
    try:
        if body.bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT}:
            return await service.generate(
                session, user.app_user_id, walk_id, body, lookup, titles, diary_writer=diary_writer
            )
        return await service.generate(session, user.app_user_id, walk_id, body, lookup, titles)
    except service.StoryboardNotFound:
        raise HTTPException(404, "산책 기록을 찾을 수 없습니다.") from None
    except service.StoryboardConflict as exc:
        raise HTTPException(409, str(exc)) from None
