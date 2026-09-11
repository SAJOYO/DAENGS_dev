"""Explicit experimental preview; does not participate in the published storyboard API."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_diary_slots import SlotPreviewRequest, SlotPreviewResponse
from daengs_backend.services.walk_diary_slot_writing import write_slot_preview
from daengs_backend.services.walk_diary_slots import preview_saved_slots

router = APIRouter(prefix="/app/walks", tags=["walk-diary-preview"])


def get_slot_writer():
    return write_slot_preview


@router.post("/{walk_id}/diary-slots/preview", response_model=SlotPreviewResponse)
async def preview_slots(
    walk_id: uuid.UUID,
    body: SlotPreviewRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    writer: Annotated[Callable, Depends(get_slot_writer)],
):
    if not settings.walk_diary_enabled or not settings.walk_diary_slots_preview_enabled:
        raise HTTPException(404, "산책 일기 미리보기가 꺼져 있습니다.")
    try:
        return await preview_saved_slots(session, user.app_user_id, walk_id, body, writer=writer)
    except LookupError:
        raise HTTPException(404, "산책 기록을 찾을 수 없습니다.") from None
    except ValueError:
        raise HTTPException(409, "산책 입력을 준비할 수 없습니다.") from None
