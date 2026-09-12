"""Internal key lookup; nginx does not expose /internal routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.api.conversation_internal import provider
from daengs_place.core.db import get_session
from daengs_place.place.bookmarks import BookmarkLookup, BookmarkLookupResult, lookup_bookmarks
from daengs_place.place.conversation.saved_search import (
    SavedSearchPlan,
    SavedSearchRequest,
    plan_saved,
)
from daengs_place.place.providers.gemini import GeminiIntentProposerError

router = APIRouter(prefix="/internal/place/bookmarks", tags=["place-bookmark-lookup"])


@router.post("/lookup", response_model=BookmarkLookupResult)
async def lookup(request: BookmarkLookup, db: Annotated[AsyncSession, Depends(get_session)]):
    return await lookup_bookmarks(db, request)


@router.post("/interpret", response_model=SavedSearchPlan)
async def interpret(request: SavedSearchRequest):
    try:
        intent = await provider().plan_saved(request)
        return plan_saved(
            request.filters,
            intent,
            search_policy=request.search_policy,
            query=request.query,
            candidate_pools=request.candidate_pools,
        )
    except GeminiIntentProposerError as exc:
        raise HTTPException(502, detail={"code": "conversation_provider_failed"}) from exc
    except (TypeError, ValueError):
        return SavedSearchPlan(
            action="clarify", message="요청을 이해하지 못했어요. 바꿀 조건을 구체적으로 알려주세요."
        )
