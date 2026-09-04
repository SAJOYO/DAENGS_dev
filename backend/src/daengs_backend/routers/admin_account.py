"""관리자 계정 관리 HTTP 경계 (콘솔 로드맵 A3).

판단은 여기 없습니다. "이 계정을 만들어도 되나", "이 변경이 스스로를 잠그나"는
`services/admin_account.py` 가 정하고, 여기서는 그 예외를 409 · 404 · 422 로
옮기기만 합니다.

**`Perm.ADMIN_MANAGE` 의 첫 사용처입니다.** 지금까지 `core/deps.py` 에 정의만 있었고,
그래서 D-014 의 role 5단계는 코드가 한 번도 실행되지 않은 상태였습니다. 이 라우터가
생기면서 ADMIN 과 OPERATOR 가 처음으로 실제로 갈립니다 — `ROLE_PERMISSIONS` 에서
OPERATOR 만 이 권한이 빠져 있습니다.

**DELETE 가 없습니다.** 계정은 지우지 않고 `status='suspended'` 로 막습니다.
`admin_audit_log.admin_user_id` 가 `ON DELETE RESTRICT` 라 DB 도 같은 것을 막고
있습니다 (#203) — 세션은 없어져야 하고 기록은 남아야 합니다.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.schemas.admin_account import (
    AdminAccountCreate,
    AdminAccountOut,
    AdminAccountPatch,
)
from daengs_backend.services import admin_account as admin_account_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/admins", tags=["admin-accounts"])


def _client_ip(request: Request) -> str:
    """감사 행에 남길 IP. `routers/auth.py` 의 같은 함수와 판단이 같습니다.

    nginx 가 `X-Real-IP` 를 덮어쓰므로 배포 환경에서는 위조할 수 없고, `uv run dev` 로
    직접 띄우면 앞에 nginx 가 없어 믿을 수 없습니다 (개발 PC 한정).
    """
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[AdminAccountOut])
async def list_accounts(
    _admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AdminAccountOut]:
    """계정 전부. 정지된 것도 같이 옵니다 — 화면이 해제 버튼을 그려야 합니다."""
    accounts = await admin_account_service.list_accounts(session)
    return [AdminAccountOut.model_validate(a) for a in accounts]


@router.post("", response_model=AdminAccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AdminAccountCreate,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminAccountOut:
    """계정 발급. 초기 비밀번호는 요청한 사람이 정해서 당사자에게 전달합니다.

    **응답에 비밀번호가 없습니다.** 보낸 쪽이 이미 아는 값이고, 여기서 돌려주면
    그 값이 프록시 로그와 브라우저 히스토리를 한 번 더 지나갑니다.
    """
    try:
        account = await admin_account_service.create_account(
            session,
            actor_id=admin.admin_id,
            login_id=body.login_id,
            password=body.password,
            name=body.name,
            role=body.role,
            ip=_client_ip(request),
        )
    except admin_account_service.LoginIdTakenError:
        # 409 입니다 — 요청 자체는 올바르고 지금 상태와 충돌하는 것입니다.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 쓰고 있는 아이디입니다."
        ) from None

    return AdminAccountOut.model_validate(account)


@router.patch("/{admin_id}", response_model=AdminAccountOut)
async def update_account(
    admin_id: uuid.UUID,
    body: AdminAccountPatch,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminAccountOut:
    """role·status 변경. 준 것만 바뀝니다.

    **막히는 두 자리는 409 입니다** (자기 자신 · 마지막 ADMIN). 403 이 아닌 이유는
    권한이 모자란 것이 아니기 때문입니다 — ADMIN 이 맞고, 그 요청이 지금 상태와
    충돌하는 것입니다. 403 으로 주면 프론트가 "권한이 없습니다" 를 띄우고, 그건
    다시 로그인하면 될 것 같은 오해를 만듭니다.
    """
    try:
        account = await admin_account_service.update_account(
            session,
            actor_id=admin.admin_id,
            target_id=admin_id,
            role=body.role,
            status=body.status,
            ip=_client_ip(request),
        )
    except admin_account_service.AdminNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 계정입니다.") from None
    except admin_account_service.SelfChangeError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "자기 계정의 권한과 상태는 바꿀 수 없습니다. 다른 관리자에게 요청하세요.",
        ) from None
    except admin_account_service.LastAdminError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "마지막 ADMIN 계정입니다. 다른 계정을 ADMIN 으로 올린 뒤에 바꾸세요.",
        ) from None

    return AdminAccountOut.model_validate(account)
