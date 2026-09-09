"""Authenticated shared occupancy reads and session-bound actions."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.territory_claim import (
    ChallengeRequest,
    ClaimResponse,
    MarkRequest,
    PhotoAccessResponse,
    SessionPhase,
    SessionResponse,
    SessionStart,
    SiteId,
    SiteResponse,
)
from daengs_backend.schemas.territory_owner import TerritoryOwnerSummary
from daengs_backend.services import activity, territory_owner
from daengs_backend.services import territory_ownership as service
from daengs_backend.services.activity_core.game_policy import GameError
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteLookup,
    TerritorySiteUnavailableError,
    get_territory_site_lookup,
)

router = APIRouter(prefix="/app/territory", tags=["territory-ownership"])
Session = Annotated[AsyncSession, Depends(get_session)]
Lookup = Annotated[TerritorySiteLookup, Depends(get_territory_site_lookup)]
Snapshot = Annotated[AsyncSession, Depends(get_snapshot_session)]


@router.get("/owner-summary", response_model=TerritoryOwnerSummary)
async def owner_summary(site_id: SiteId, user: CurrentAppUser, db: Snapshot):
    try:
        return await territory_owner.summary(db, user.app_user_id, site_id)
    except activity.ActivityDisabled:
        raise HTTPException(503, {"code": "activity_disabled"}) from None


async def _call(operation):
    try:
        return await operation
    except service.ClaimNotFound:
        raise HTTPException(404, "산책 게임 세션 또는 시도를 찾을 수 없습니다.") from None
    except service.ClaimConflict as exc:
        raise HTTPException(409, {"code": exc.code}) from None
    except GameError as exc:
        raise HTTPException(409, {"code": str(exc)}) from None
    except TerritorySiteUnavailableError:
        raise HTTPException(503, "점령지 게임판을 확인할 수 없습니다.") from None


@router.put("/claim-sessions/{client_session_id}", response_model=SessionResponse)
async def start_session(
    client_session_id: uuid.UUID, body: SessionStart, user: CurrentAppUser, db: Session
):
    return await _call(service.start_session(db, user.app_user_id, client_session_id, body))


@router.get("/claim-sessions/{client_session_id}", response_model=SessionResponse)
async def get_game_session(client_session_id: uuid.UUID, user: CurrentAppUser, db: Session):
    return await _call(service.get_session(db, user.app_user_id, client_session_id))


@router.patch("/claim-sessions/{client_session_id}", response_model=SessionResponse)
async def change_phase(
    client_session_id: uuid.UUID, body: SessionPhase, user: CurrentAppUser, db: Session
):
    return await _call(service.change_phase(db, user.app_user_id, client_session_id, body))


@router.get("/occupancies", response_model=list[SiteResponse])
async def occupancies(
    user: CurrentAppUser,
    db: Session,
    site_ids: Annotated[list[SiteId], Query(min_length=1, max_length=100)],
):
    return await service.list_sites(db, user.app_user_id, site_ids)


@router.post("/claims", response_model=ClaimResponse)
async def mark(body: MarkRequest, user: CurrentAppUser, db: Session, lookup: Lookup):
    return await _call(service.mark(db, user.app_user_id, body, lookup))


@router.get("/claims/{claim_id}", response_model=ClaimResponse)
async def claim(claim_id: uuid.UUID, user: CurrentAppUser, db: Session):
    return await _call(service.get_claim(db, user.app_user_id, claim_id))


@router.put("/claims/{claim_id}/photos/{photo_id}", response_model=ClaimResponse)
async def bind_photo(claim_id: uuid.UUID, photo_id: uuid.UUID, user: CurrentAppUser, db: Session):
    """Bind a freshly issued photo attempt before upload; verdict worker then resolves the claim."""
    return await _call(service.bind_photo(db, user.app_user_id, claim_id, photo_id))


@router.get("/claims/{claim_id}/photo-access", response_model=PhotoAccessResponse)
async def photo_access(claim_id: uuid.UUID, user: CurrentAppUser, db: Session):
    return await _call(service.photo_access(db, user.app_user_id, claim_id))


@router.put("/claims/{claim_id}/challenges/{challenge_id}")
async def challenge(
    claim_id: uuid.UUID,
    challenge_id: uuid.UUID,
    body: ChallengeRequest,
    user: CurrentAppUser,
    db: Session,
):
    return await _call(service.admit_challenge(db, user.app_user_id, claim_id, challenge_id, body))
