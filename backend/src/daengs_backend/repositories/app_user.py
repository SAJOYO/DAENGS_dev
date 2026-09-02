"""app_users 조회·저장. 쿼리만 있고 판단은 없습니다.

**암복호화를 여기서 하지 않습니다.** 들어오고 나가는 것은 바이트열 그대로이고,
평문 ↔ 암호문 변환은 services 계층이 `core/crypto.py` 로 합니다 (models/app_user.py
주석과 같은 규칙). 그래야 "어디서 복호화했는지"가 한 곳에 모입니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AppUser

__all__ = ["create", "get_by_email_hash", "get_by_id", "get_by_kakao_id"]


async def get_by_id(session: AsyncSession, app_user_id: uuid.UUID) -> AppUser | None:
    """PK 로 한 명. 토큰의 subject 를 회원으로 되돌릴 때 씁니다."""
    return await session.get(AppUser, app_user_id)


async def get_by_kakao_id(session: AsyncSession, kakao_id: int) -> AppUser | None:
    """카카오 회원번호로 한 명. **로그인의 첫 단계입니다.**

    `status` 로 거르지 않습니다 — 탈퇴·정지한 회원도 있는 그대로 돌려줍니다.
    "다시 로그인해도 되나"는 services 가 정합니다. 여기서 None 으로 뭉개면
    탈퇴한 회원이 로그인할 때 **같은 kakao_id 로 새 행을 만들려다 UNIQUE 에 걸립니다.**
    """
    stmt = select(AppUser).where(AppUser.kakao_id == kakao_id)
    return await session.scalar(stmt)


async def get_by_email_hash(session: AsyncSession, email_hash: str) -> AppUser | None:
    """이메일 blind index 로 한 명. 관리 화면의 회원 검색용입니다.

    **암호문(`email_enc`)으로는 찾을 수 없습니다.** 같은 이메일이라도 암호문이 매번
    달라서입니다. `core.crypto.blind_index(email)` 로 만든 해시를 넘기세요 (03_auth.sql).
    """
    stmt = select(AppUser).where(AppUser.email_hash == email_hash)
    return await session.scalar(stmt)


async def create(
    session: AsyncSession,
    *,
    kakao_id: int,
    email_enc: bytes | None = None,
    email_hash: str | None = None,
) -> AppUser:
    """회원 한 줄을 만듭니다. 최초 로그인에서만 부릅니다.

    받는 것은 **평문이 아니라 암호문과 해시**입니다. 암호화는 services 가 끝내고
    넘깁니다 — 이 파일이 crypto 를 import 하기 시작하면 "어디서 암호화되는가"가
    두 군데로 흩어집니다.

    `email_hash` 에 **빈 문자열을 넣지 마세요.** UNIQUE 컬럼이라 이메일 동의를 안 받은
    회원끼리 충돌합니다. 값이 없으면 반드시 None 입니다.
    """
    user = AppUser(kakao_id=kakao_id, email_enc=email_enc, email_hash=email_hash)
    session.add(user)
    # id 와 created_at 은 DB 가 채웁니다. 방금 만든 회원으로 바로 토큰을 발급해야 해서
    # 여기서 flush 해 둡니다 (commit 은 아닙니다 — 롤백은 그대로 됩니다).
    await session.flush()
    return user


async def delete(session: AsyncSession, user: AppUser) -> None:
    """회원 한 줄을 지웁니다. 탈퇴의 마지막 단계입니다.

    **자식 행은 먼저 비우고 부르세요.** 강아지·산책은 DB 캐스케이드가 따라 지우긴
    하지만, 그 수를 세어 로그에 남기는 것은 services 가 하므로 순서가 거꾸로면
    "몇 건 지웠는지"를 모릅니다. refresh_tokens 도 같습니다.
    """
    await session.delete(user)
