"""메인 백엔드의 훈련 RAG gateway HTTP 경계."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from daengs_backend.config import settings
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.schemas.training import TrainingChatRequest, TrainingChatResponse
from daengs_backend.services.training_rag import (
    TrainingRagClient,
    TrainingRagTimeoutError,
    TrainingRagUnavailableError,
)

router = APIRouter(prefix="/training", tags=["training"])


def get_training_rag_client() -> TrainingRagClient:
    return TrainingRagClient(
        base_url=settings.training_rag_base_url,
        connect_timeout_seconds=settings.training_rag_connect_timeout_seconds,
        read_timeout_seconds=settings.training_rag_read_timeout_seconds,
    )


async def require_training_access(request: Request) -> AppPrincipal | None:
    """로컬 데모만 익명 허용하고, 기본값은 기존 앱 토큰을 요구한다."""
    if settings.training_rag_allow_anonymous_demo:
        return None
    return await current_app_user(request)


@router.post("/chat", response_model=TrainingChatResponse)
async def chat(
    payload: TrainingChatRequest,
    response: Response,
    _user: Annotated[AppPrincipal | None, Depends(require_training_access)],
    client: Annotated[TrainingRagClient, Depends(get_training_rag_client)],
) -> TrainingChatResponse:
    """질문을 별도 RAG 서비스로 전달하고 사용자용 상태로 정규화한다."""
    trace_id = str(uuid.uuid4())
    response.headers["X-Request-ID"] = trace_id
    try:
        return await client.ask(question=payload.question.strip(), trace_id=trace_id)
    except TrainingRagTimeoutError:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "훈련 도우미 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.",
            headers={"X-Request-ID": trace_id},
        ) from None
    except TrainingRagUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "훈련 도우미에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
            headers={"X-Request-ID": trace_id},
        ) from None
