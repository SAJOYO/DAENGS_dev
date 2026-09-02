"""`POST /assistant/query` HTTP 경계.

판단은 여기 없다. 검증하고, 인증된 principal 로 `PrincipalContext` 를 만들고,
승인된 필드만으로 구조화 컨텍스트를 조립해 `AssistantOrchestrationService` 를
부른다. 의미 라우팅·결정론적 RoutePlan 조립·능력 실행·집계는 전부 Card 2B/Card 1
의 것이다 (`orchestration/service.py` · `planner.py` · `semantic.py` · `graph.py`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from daengs_backend.core.deps import AppPrincipal, Perm, Principal, admin_or_app_user
from daengs_backend.orchestration.contracts import AssistantResponse, PrincipalContext
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.schemas.assistant import AssistantQueryRequest

router = APIRouter(tags=["assistant"])


def get_assistant_orchestration_service() -> AssistantOrchestrationService:
    return AssistantOrchestrationService()


def _principal_context(principal: Principal | AppPrincipal) -> PrincipalContext:
    """인증된 principal 에서만 만든다 — 요청 본문의 신원 필드는 절대 쓰지 않는다."""
    if isinstance(principal, Principal):
        return PrincipalContext(
            subject=str(principal.admin_id),
            kind="ADMIN",
            permissions=tuple(p.value for p in principal.permissions),
        )
    return PrincipalContext(subject=str(principal.app_user_id), kind="APP_USER")


def _structured_context(body: AssistantQueryRequest) -> dict[str, Any]:
    """승인된 필드만 명시적으로 담는다 — `body.model_dump()` 를 그대로 쓰지 않는다."""
    context: dict[str, Any] = {}
    if body.source is not None:
        context["source"] = body.source
    if body.action is not None:
        context["action"] = body.action
    if body.active_dog_id is not None:
        context["active_dog_id"] = body.active_dog_id
    if body.location is not None:
        context["location"] = {"lat": body.location.lat, "lon": body.location.lon}
    return context


@router.post(
    "/assistant/query",
    response_model=AssistantResponse,
    summary="자연어 질의 → 의미/결정론적 라우팅 → 능력 실행",
)
async def query(
    body: AssistantQueryRequest,
    principal: Annotated[Principal | AppPrincipal, Depends(admin_or_app_user(Perm.READ))],
    service: Annotated[AssistantOrchestrationService, Depends(get_assistant_orchestration_service)],
) -> AssistantResponse:
    """`AssistantResponse` 를 그대로 돌려준다. FAILED 를 포함해 상태를 재해석하지
    않는다 — 그것은 orchestration 계약이 소유한다 (orchestration-contracts.md §5).
    """
    return await service.run(
        query=body.query,
        principal=_principal_context(principal),
        context=_structured_context(body),
        requested_capability=body.requested_capability,
    )


__all__ = ["router"]
