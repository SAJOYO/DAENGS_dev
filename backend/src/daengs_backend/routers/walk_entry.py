"""앱 소유 산책 기록 API. 기존 위치 finalize와 수명을 나누어 둔다."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.routers.walk_entry_errors import translate
from daengs_backend.schemas.walk_entry import (
    EntryList,
    EntryResponse,
    EntryWrite,
    RecordProfileQuery,
    RecordProfileResponse,
)
from daengs_backend.schemas.walk_entry_context import EntryContexts
from daengs_backend.services.walk_records import v1 as service

router = APIRouter(prefix="/app/walks", tags=["walks"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/record-profile/query", response_model=RecordProfileResponse)
async def record_profile(
    body: RecordProfileQuery,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_snapshot_session)],
):
    return await translate(service.profile(session, user.app_user_id, body))


@router.get("/{walk_id}/entries", response_model=EntryList)
async def entries(
    walk_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_snapshot_session)],
):
    return EntryList(
        entries=await translate(service.list_entries(session, user.app_user_id, walk_id))
    )


@router.get("/{walk_id}/entries/{entry_id}/contexts", response_model=EntryContexts)
async def contexts(
    walk_id: uuid.UUID,
    entry_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_snapshot_session)],
):
    from daengs_backend.services.walk_records.context import read

    return await translate(read(session, user.app_user_id, walk_id, entry_id))


@router.put("/{walk_id}/entries/{entry_id}", response_model=EntryResponse)
async def put(
    walk_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: EntryWrite,
    user: CurrentAppUser,
    session: Session,
):
    return await translate(service.write(session, user.app_user_id, walk_id, entry_id, body))


@router.delete("/{walk_id}/entries/{entry_id}", response_model=EntryResponse)
async def delete(
    walk_id: uuid.UUID,
    entry_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
    mutation_id: uuid.UUID,
    expected_revision: Annotated[int, Query(ge=0)],
):
    return await translate(
        service.remove(session, user.app_user_id, walk_id, entry_id, expected_revision, mutation_id)
    )
