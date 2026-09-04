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
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.schemas.app_user_admin import (
    AdminPetOut,
    AppUserDetailOut,
    AppUserOut,
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
