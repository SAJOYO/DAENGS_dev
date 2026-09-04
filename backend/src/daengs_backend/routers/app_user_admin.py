"""회원 조회 HTTP 경계 (콘솔 로드맵 A2 · #211).

판단은 여기 없습니다. 무엇을 어떻게 가릴지는 `services/app_user_admin.py` 가 정하고,
여기서는 그 결과를 스키마로 옮기기만 합니다.

**`/app/*` 와 헷갈리지 마세요.** 저기는 앱 회원이 **자기 것**을 보는 문이고
(`current_app_user` 가 관리자 토큰을 401 로 막습니다), 여기는 관리자가 **남의 것**을
보는 문입니다. 같은 `app_users` 를 읽지만 인증 주체도 권한도 다릅니다 — 그래서
라우터를 따로 둡니다.

**권한은 `READ` 입니다.** 나가는 값이 전부 마스킹된 것이라 `/admin/crawl` 의 조회와 같은
자리입니다. 여기를 `pii:read` 로 잠그면 "API 는 열려 있는데 화면만 안 보이는 계정" 이
생깁니다 (`console/page.tsx` 의 카드 권한 주석). 원문을 여는 문은 짝 카드(#212)가
`pii:read` 로 따로 냅니다.

**조건 없는 목록이 없습니다.** `?email=` 이나 `?kakao_id=` 중 하나가 반드시 있어야
합니다 — 이유는 아래 `search` docstring.

**권한이 셋으로 갈립니다** (#212 가 뒤의 둘을 더했습니다).

    GET  /admin/app-users            READ       가려진 값만
    GET  /admin/app-users/{id}       READ       가려진 값만 + 반려견
    GET  /admin/app-users/{id}/pii   PII_READ   **원문. 부를 때마다 감사 행이 남는다**
    PATCH /admin/app-users/{id}      OPS_WRITE  정지 / 정지 해제

원문 조회를 `?reveal=true` 같은 질의 인자로 두지 않은 이유는 **같은 경로가 어떤 때는
기록을 남기고 어떤 때는 안 남게 되기 때문**입니다. 권한도 다르고(FastAPI 의존성은
경로 단위입니다), "감사에 남는 호출"이 경로 이름으로 드러나는 편이 낫습니다.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, client_ip, require
from daengs_backend.schemas.app_user_admin import (
    AdminPetOut,
    AppUserDetailOut,
    AppUserOut,
    AppUserPiiOut,
    AppUserStatusPatch,
)
from daengs_backend.services import app_user_admin as service
from daengs_backend.services import dog_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/app-users", tags=["admin-app-users"])


def _to_out(view: service.AppUserView) -> AppUserOut:
    return AppUserOut(
        id=view.user.id,
        kakao_id=view.user.kakao_id,
        email_masked=view.email_masked,
        phone_masked=view.phone_masked,
        name_masked=view.name_masked,
        status=view.user.status,
        room_name=view.user.room_name,
        created_at=view.user.created_at,
    )


@router.get("", response_model=list[AppUserOut])
async def search(
    _admin: Annotated[Principal, Depends(require(Perm.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    email: Annotated[str | None, Query(max_length=320)] = None,
    kakao_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[AppUserOut]:
    """이메일 **정확 일치** 또는 카카오 회원번호로 찾습니다. 0개나 1개입니다.

    **부분 검색이 원천적으로 안 됩니다.** `email_hash` 가 HMAC-SHA256 이라
    `LIKE '%@gmail.com'` 같은 조각 검색이 성립하지 않습니다 (D-012) — 주소를 통째로
    알아야 찾습니다. 화면이 그것을 안내해야 합니다. 안 그러면 "검색이 고장났다"로
    읽힙니다. (대소문자·앞뒤 공백은 `blind_index` 가 맞춰 주므로 예외입니다.)

    **조건 없이 전체를 주지 않습니다.** 이 API 는 `Perm.READ` 라 VIEWER 까지 전부
    통과하는데, 목록을 열면 로그인한 사람 누구나 전 회원의 (가려졌더라도) 개인정보를
    넘겨볼 수 있게 됩니다. 이 카드가 없애려는 일은 "메일 보낸 그 사람을 찾는 것"이지
    "회원을 훑는 것"이 아닙니다. 목록이 필요해지면 그때 **권한과 함께** 정합니다
    (#211 `## 남은 것`).

    못 찾은 것은 오류가 아니라 빈 목록입니다. 404 로 주면 "그 이메일은 가입돼 있다/없다"를
    상태 코드로 알려 주게 되는데, 이 문은 로그인 뒤라 그 자체가 사고는 아니어도
    빈 목록으로 충분합니다.
    """
    given = [v for v in (email, kakao_id) if v is not None]
    if len(given) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "email 이나 kakao_id 중 하나만 주세요.",
        )

    views = await service.find(session, email=email, kakao_id=kakao_id)
    return [_to_out(v) for v in views]


@router.get("/{app_user_id}", response_model=AppUserDetailOut)
async def detail(
    app_user_id: uuid.UUID,
    _admin: Annotated[Principal, Depends(require(Perm.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUserDetailOut:
    """한 명 + 반려견. 정지·탈퇴한 회원도 그대로 보여 줍니다.

    탈퇴한 회원은 `*_masked` 가 전부 `None` 입니다 — 개인정보가 파기돼서고, 화면은
    그것을 "동의를 안 받음"과 다르게 말해야 합니다. 가르는 값은 `status` 입니다.
    """
    view = await service.get_detail(session, app_user_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 회원입니다.")

    base = _to_out(view)
    return AppUserDetailOut(
        **base.model_dump(),
        pets=[
            AdminPetOut(
                id=p.id,
                name=p.name,
                breed=p.breed,
                sex=p.sex,
                neutered=p.neutered,
                weight_kg=p.weight_kg,
                birth_date=p.birth_date,
                birth_date_kind=p.birth_date_kind,
                farewell_on=p.farewell_on,
                is_primary=p.id == view.user.primary_pet_id,
                # 모르는 아바타 id 면 원래 값을 그대로 보여 줍니다 — 관리 화면은
                # "저장된 것이 무엇인가"를 알아야 합니다 (`AdminPetOut` 주석).
                breed_label=dog_context.breed_label(p.breed) or p.breed,
            )
            for p in view.pets
        ],
    )


@router.get("/{app_user_id}/pii", response_model=AppUserPiiOut)
async def reveal(
    app_user_id: uuid.UUID,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.PII_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUserPiiOut:
    """개인정보 원문. **`pii:read` 만 통과하고, 부를 때마다 감사 행이 남습니다.**

    `Perm.PII_READ` 를 가진 role 은 ADMIN 과 OPERATOR 뿐입니다 (`core/deps.py`).
    CURATOR · ANALYST · VIEWER 는 여기서 403 이고, 그 셋도 위의 마스킹된 조회는
    그대로 씁니다 — 그것이 이 카드가 A3(#207) 뒤에 온 이유입니다.

    **GET 인데 쓰기가 일어납니다.** 감사 기록이 그 쓰기이고, 그래서 이 호출은
    멱등하지 않습니다. 그래도 GET 인 것은 클라이언트 입장에서 **자원을 바꾸지 않기**
    때문입니다 — 브라우저가 미리 불러 두는 자리에 이 경로를 두지만 마세요.

    값이 전부 `None` 이어도 200 입니다. 없는 것과 못 여는 것은 다릅니다
    (`services/app_user_admin.py` 의 `_decrypt_or_none`).
    """
    pii = await service.reveal(
        session,
        app_user_id=app_user_id,
        actor_id=admin.admin_id,
        ip=client_ip(request),
    )
    if pii is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 회원입니다.")

    return AppUserPiiOut(email=pii.email, phone=pii.phone, name=pii.name)


@router.patch("/{app_user_id}", response_model=AppUserOut)
async def update_status(
    app_user_id: uuid.UUID,
    body: AppUserStatusPatch,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.OPS_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUserOut:
    """정지 / 정지 해제. **`ops:write` 입니다** — 원문 조회(`pii:read`)와 다른 권한입니다.

    ⚠ **지금 `ROLE_PERMISSIONS` 로는 둘이 같은 두 role 을 가립니다** — ADMIN 과
    OPERATOR 만 둘 다 가집니다 (CURATOR 의 쓰기는 `KB_WRITE` 하나뿐입니다). 그래도
    나눠 두는 것은 두 일이 실제로 다르고, role 구성을 바꿀 때 고칠 자리가
    `ROLE_PERMISSIONS` 한 곳이어야 하기 때문입니다 (`core/deps.py` 첫 문단 —
    "엔드포인트는 role 이 아니라 권한을 선언합니다"). 화면도 두 버튼을 따로 가립니다.

    **탈퇴한 회원은 409 입니다.** 권한 문제가 아니라 요청이 지금 상태와 충돌하는
    것입니다 — 되살리는 길은 본인이 카카오로 다시 로그인하는 것 하나뿐입니다
    (`services/app_user_admin.py` 의 `update_status`).
    """
    try:
        user = await service.update_status(
            session,
            app_user_id=app_user_id,
            actor_id=admin.admin_id,
            status=body.status,
            ip=client_ip(request),
        )
    except service.AppUserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 회원입니다.") from None
    except service.WithdrawnMemberError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "탈퇴한 회원입니다. 상태를 되돌릴 수 없습니다 — "
            "본인이 카카오로 다시 로그인하면 되살아납니다.",
        ) from None

    # 응답은 가려진 값입니다. 상태를 바꿨다고 원문을 딸려 보내지 않습니다.
    view = await service.get_detail(session, user.id)
    assert view is not None
    return _to_out(view)
