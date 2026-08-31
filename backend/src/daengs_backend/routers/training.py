"""메인 백엔드의 훈련 RAG gateway HTTP 경계."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from daengs_backend.config import settings
from daengs_backend.core.deps import Perm, Principal, current_admin, require
from daengs_backend.schemas.training import TrainingChatRequest, TrainingChatResponse
from daengs_backend.services.training_rag import (
    TrainingRagService,
    TrainingRagUnavailableError,
)

router = APIRouter(prefix="/training", tags=["training"])


def get_training_rag_service() -> TrainingRagService:
    return TrainingRagService()


#: 콘솔의 `검색 점검` 메뉴와 같은 기준입니다 (`frontend/app/console/page.tsx`).
#: 이 권한이 없는 role(VIEWER)은 메뉴에서도 안 보입니다.
_require_search_inspect = require(Perm.SEARCH_INSPECT)


async def require_training_access(request: Request) -> Principal | None:
    """로컬 데모만 익명 허용하고, 기본값은 **관리자**를 요구한다.

    **앱 회원은 안 받는다.** 이건 `#25` 가 랜딩에서 RAG 를 시연하려고 만든 임시
    게이트웨이이고, 부르는 곳은 콘솔의 `검색 점검` 화면 하나뿐이다. 앱이 쓰는
    `/walk` 과 달리 앱 클라이언트가 없어서, 앱 회원 경로를 열어 둘 이유가 없다.

    권한이 모자란 관리자는 403 이고 종류가 안 맞는 토큰은 401 이다. 그 차이는
    `core/deps.py` 를 보라 — 401 을 주면 프론트가 재발급하며 돈다.
    """
    if settings.training_rag_allow_anonymous_demo:
        return None
    return await _require_search_inspect(await current_admin(request))


@router.post("/chat", response_model=TrainingChatResponse)
async def chat(
    payload: TrainingChatRequest,
    response: Response,
    _user: Annotated[Principal | None, Depends(require_training_access)],
    service: Annotated[TrainingRagService, Depends(get_training_rag_service)],
) -> TrainingChatResponse:
    """질문을 로컬 Training 컴포넌트로 전달하고 사용자용 상태로 정규화한다."""
    trace_id = str(uuid.uuid4())
    response.headers["X-Request-ID"] = trace_id
    try:
        result = await service.ask(question=payload.question.strip(), trace_id=trace_id)
        return result.to_public_response()
    except TrainingRagUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "훈련 도우미에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
            headers={"X-Request-ID": trace_id},
        ) from None
