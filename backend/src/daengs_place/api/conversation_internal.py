"""Internal conversation prepare/answer endpoints; the gateway commits between them."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.config import settings
from daengs_place.core.db import get_session
from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import AnswerRequest, PrepareRequest
from daengs_place.place.conversation.service import ConversationService, public_search
from daengs_place.place.providers.conversation_gemini import GeminiConversation
from daengs_place.place.providers.gemini import GeminiIntentProposerError

router = APIRouter(prefix="/internal/place/facility-conversation", tags=["facility-conversation"])


def provider():
    key = settings.gemini_api_key.get_secret_value().strip()
    if not key:
        raise HTTPException(503, detail={"code": "conversation_not_configured"})
    return GeminiConversation(key, settings.gemini_model, timeout=settings.gemini_timeout_ms / 1000)


@router.post("/prepare")
async def prepare(request: PrepareRequest, db: Annotated[AsyncSession, Depends(get_session)]):
    try:
        prepared = await ConversationService(
            provider() if request.mode == "chat" else None
        ).prepare(db, request)
        return {
            "prepared": prepared.model_dump(mode="json"),
            "search": public_search(prepared.state.snapshot),
        }
    except GeminiIntentProposerError as exc:
        raise HTTPException(502, detail={"code": "conversation_provider_failed"}) from exc
    except (SQLAlchemyError, TimeoutError) as exc:
        raise HTTPException(503, detail={"code": "conversation_search_failed"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "conversation_invalid_input"}) from exc


@router.post("/answer")
async def answer(request: AnswerRequest):
    return await compose_answer(request)
