"""Separate wire version. Legacy clients never receive nullable v2 behavior as a v1 success."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.routers.walk_entry_errors import translate as translate_entry
from daengs_backend.schemas.walk_entry import RecordProfileQuery
from daengs_backend.schemas.walk_entry_context import EntryContexts
from daengs_backend.schemas.walk_entry_v2 import (
    EntryListV2,
    EntryResponseV2,
    EntryWriteV2,
    PinWrite,
    ProfileV2,
    Tombstone,
)
from daengs_backend.services import walk_entry_v2 as service

router = APIRouter(prefix="/app/v2/walks", tags=["walk-entry-v2"])
capabilities_router = APIRouter(prefix="/app/walks", tags=["walk-entry-v2"])
Session = Annotated[AsyncSession, Depends(get_session)]
Snapshot = Annotated[AsyncSession, Depends(get_snapshot_session)]


async def translate(operation):
    try:
        return await translate_entry(operation)
    except service.EntryDeleted:
        raise HTTPException(410, {"code": "walk_entry_deleted"}) from None
    except service.EntryWritesDisabled:
        raise HTTPException(409, {"code": "walk_entry_v2_writes_disabled"}) from None


@capabilities_router.get("/entry-capabilities")
async def capabilities(user: CurrentAppUser):
    return service.capabilities()


@router.post("/record-profile/query", response_model=ProfileV2)
async def profile(body: RecordProfileQuery, user: CurrentAppUser, session: Snapshot):
    return await translate(service.profile(session, user.app_user_id, body))


@router.get("/{walk_id}/entries", response_model=EntryListV2)
async def entries(walk_id: uuid.UUID, user: CurrentAppUser, session: Snapshot):
    return await translate(service.list_entries(session, user.app_user_id, walk_id))


@router.put("/{walk_id}/entries/{entry_id}", response_model=EntryResponseV2)
async def write(
    walk_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: EntryWriteV2,
    user: CurrentAppUser,
    session: Session,
):
    return await translate(service.write(session, user.app_user_id, walk_id, entry_id, body))


@router.get("/{walk_id}/entries/{entry_id}/contexts", response_model=EntryContexts)
async def contexts(
    walk_id: uuid.UUID, entry_id: uuid.UUID, user: CurrentAppUser, session: Snapshot
):
    from daengs_backend.services.walk_entry_context import read

    return await translate(read(session, user.app_user_id, walk_id, entry_id, v2=True))


@router.put("/{walk_id}/entries/{entry_id}/pin", response_model=EntryResponseV2)
async def pin(
    walk_id: uuid.UUID, entry_id: uuid.UUID, body: PinWrite, user: CurrentAppUser, session: Session
):
    return await translate(service.finalize_pin(session, user.app_user_id, walk_id, entry_id, body))


@router.delete("/{walk_id}/entries/{entry_id}", response_model=Tombstone)
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
