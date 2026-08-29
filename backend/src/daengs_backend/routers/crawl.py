"""크롤 관리 HTTP 경계 (RAG-001 요구사항 ②③ · RAG-047).

판단은 여기 없습니다. "어느 큐로 보내나", "브로커가 죽었나"는 services/crawl.py 가 정하고
여기서는 그 결과를 상태 코드로 옮기기만 합니다.

**권한을 role 이 아니라 Perm 으로 겁니다** (core/deps.py 의 규칙 그대로).
읽기는 `READ`, 트리거는 `OPS_WRITE` 입니다 — 크롤은 외부 사이트로 실제 요청을 내보내고
`data/raw/` 를 바꾸므로 조회와 같은 문이면 안 됩니다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.schemas.crawl import (
    CrawlRunOut,
    CrawlStatusOut,
    CrawlTriggerAccepted,
    CrawlTriggerRequest,
)
from daengs_backend.services import crawl as crawl_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/crawl", tags=["admin-crawl"])


@router.get("", response_model=CrawlStatusOut)
async def status_(
    _admin: Annotated[Principal, Depends(require(Perm.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CrawlStatusOut:
    """소스별 마지막 실행 + 아직 안 끝난 수. 화면이 이것을 폴링합니다."""
    runs = await crawl_service.latest(session)
    return CrawlStatusOut(
        runs=[CrawlRunOut.model_validate(r) for r in runs],
        running=await crawl_service.running_count(session),
    )


@router.get("/{source_id}", response_model=list[CrawlRunOut])
async def history(
    source_id: str,
    _admin: Annotated[Principal, Depends(require(Perm.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 20,
) -> list[CrawlRunOut]:
    """한 소스의 이력. 없는 소스면 빈 목록입니다 — 404 로 하지 않습니다.

    시드에는 있는데 아직 한 번도 안 돈 소스가 정상으로 존재하기 때문입니다.
    """
    runs = await crawl_service.history(session, source_id, limit=min(limit, 100))
    return [CrawlRunOut.model_validate(r) for r in runs]


@router.post("", response_model=CrawlTriggerAccepted,
             status_code=status.HTTP_202_ACCEPTED)
async def trigger(
    body: CrawlTriggerRequest,
    _admin: Annotated[Principal, Depends(require(Perm.OPS_WRITE))],
) -> CrawlTriggerAccepted:
    """수동 트리거. **202 이고 결과가 아니라 접수증입니다.**

    크롤 하나가 분 단위라 요청을 붙들 수 없습니다. 진행은 `GET /admin/crawl` 을 폴링해서
    보고, 결과는 `crawl_runs` 에 남습니다 (RAG-001 원칙 6).

    없는 소스 이름을 보내도 여기서 막지 않습니다 — 태스크가 그대로 시도하고 결과에
    `KeyError` 를 담습니다 (RAG-044). 시드 목록을 두 곳에서 검사하면 반드시 어긋납니다.
    """
    try:
        task_id = crawl_service.trigger(body.source_ids)
    except crawl_service.BrokerUnavailable as e:
        # 500 이 아닙니다 — 앱은 멀쩡하고 워커/브로커가 없는 것이라 사람이 고칠 일입니다.
        logger.warning("크롤 트리거 실패 — %s", e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    return CrawlTriggerAccepted(task_id=task_id, source_ids=body.source_ids)
