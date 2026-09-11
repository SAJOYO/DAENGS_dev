"""app_users 조회·저장. 쿼리만 있고 판단은 없습니다.

**암복호화를 여기서 하지 않습니다.** 들어오고 나가는 것은 바이트열 그대로이고,
평문 ↔ 암호문 변환은 services 계층이 `core/crypto.py` 로 합니다 (models/app_user.py
주석과 같은 규칙). 그래야 "어디서 복호화했는지"가 한 곳에 모입니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AppUser

__all__ = [
    "create",
    "escape_like",
    "get_active_for_update",
    "get_by_email_hash",
    "get_by_id",
    "get_by_kakao_id",
    "is_nickname_taken",
    "list_page",
    "nicknames_by_ids",
    "search_by_nickname",
]


async def get_by_id(session: AsyncSession, app_user_id: uuid.UUID) -> AppUser | None:
    """PK 로 한 명. 토큰의 subject 를 회원으로 되돌릴 때 씁니다."""
    return await session.get(AppUser, app_user_id)


async def get_active_for_update(
    session: AsyncSession, app_user_id: uuid.UUID
) -> AppUser | None:
    """활성 회원 한 명을 요청 트랜잭션이 끝날 때까지 잠급니다.

    access token 은 탈퇴 뒤에도 최대 5분 유효합니다. 앱 소유 데이터 API 가 이 잠금을
    공통으로 잡으면 탈퇴와 새 쓰기가 한 회원 안에서 직렬화됩니다. 먼저 끝난 쓰기는
    탈퇴가 지우고, 탈퇴가 먼저 끝났으면 이 조회가 아무 행도 돌려주지 않습니다.
    """
    stmt = (
        select(AppUser)
        .where(AppUser.id == app_user_id, AppUser.status == "active")
        .with_for_update()
    )
    return await session.scalar(stmt)


async def nicknames_by_ids(
    session: AsyncSession, app_user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """id → 닉네임. **암호화 컬럼이 아니라 평문 `nickname` 이라 그대로 읽습니다.**

    구성원 목록·케어 로그의 `actor` 라벨이 씁니다. 여기는 조회만 하고, "지금도
    구성원인가"는 부르는 쪽(`services/pet_member.py`)이 따로 검사합니다 — 탈퇴자의
    새 닉네임이 옛 기록에 새는 것을 막는 규칙이 그 검사입니다.
    """
    if not app_user_ids:
        return {}
    stmt = select(AppUser.id, AppUser.nickname).where(AppUser.id.in_(list(app_user_ids)))
    return {row.id: row.nickname for row in await session.execute(stmt)}


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


async def is_nickname_taken(session: AsyncSession, nickname: str) -> bool:
    """이 닉네임을 이미 누가 쓰고 있나. **소문자로 접어서** 봅니다.

    `lower(nickname)` UNIQUE 인덱스와 **같은 식으로 물어야** 인덱스를 탑니다
    (03_auth.sql 의 `idx_app_users_nickname`). 대소문자만 다른 이름을 허용하면
    화면에서 같은 이름으로 읽혀서 구분이 안 됩니다.

    ⚠️ **이 답은 참고용입니다.** 여기서 비었다고 나와도 저장하기 전에 남이 채갈 수
    있습니다. 진짜 방어는 UNIQUE 인덱스이고, 부르는 쪽은 IntegrityError 를 받을 준비가
    되어 있어야 합니다.
    """
    stmt = select(AppUser.id).where(func.lower(AppUser.nickname) == nickname.lower())
    return await session.scalar(stmt) is not None


def escape_like(term: str) -> str:
    """LIKE 의 메타문자를 막습니다. **탈출 문자는 백슬래시입니다.**

    안 하면 `%` 하나로 전 회원이 나옵니다 — 조건 없는 목록을 막아 둔 것
    (`routers/app_user_admin.py`)이 그 한 글자로 무의미해집니다. `_` 는 "아무 글자
    하나"라 엉뚱한 사람이 딸려 나옵니다.

    **백슬래시를 먼저 바꿉니다.** 나중에 하면 앞에서 넣은 탈출 문자까지 다시 탈출해서
    `%` 가 도로 살아납니다.

    쿼리에서 떼어내 둔 것은 **DB 없이 시험할 수 있게** 하려는 것입니다. 가짜 저장소는
    부분 일치로 흉내 내므로, 이스케이프가 실제로 도는지는 여기서만 볼 수 있습니다.
    """
    return term.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")


async def search_by_nickname(
    session: AsyncSession, term: str, *, limit: int = 20
) -> list[AppUser]:
    """닉네임 **부분 일치**로 여러 명. 관리 화면의 회원 검색용입니다.

    `email_hash` 로는 조각 검색이 안 되지만(HMAC — D-012) 닉네임은 평문이라 됩니다.
    이 앱키로는 이메일 동의를 못 받아 `?email=` 이 영영 안 맞으므로, 콘솔에서 실제로
    쓰이는 검색은 이쪽입니다.

    `%` 와 `_` 는 LIKE 의 메타문자라 [escape_like] 로 막습니다 — 그 함수의 주석을 보세요.
    """
    stmt = (
        select(AppUser)
        .where(AppUser.nickname.ilike(f"%{escape_like(term)}%", escape="\\"))
        .order_by(AppUser.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def list_page(
    session: AsyncSession,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> list[AppUser]:
    """가입 최근 순 한 쪽. **조건이 없습니다 — 전 회원이 대상입니다.**

    위의 검색들과 쓰임이 다릅니다. 저기는 "메일 보낸 그 사람을 찾는 것"이고 여기는
    "회원을 훑는 것"이라, 부르는 자리의 권한이 다릅니다 (`ADMIN_MANAGE` —
    `routers/app_user_admin.py`). 그래서 [search_by_nickname] 에 조건을 비울 수 있는
    인자를 더하지 않고 함수를 따로 뒀습니다.

    **`status` 로 거르지 않습니다.** 정지·탈퇴한 회원도 관리 화면에서는 보여야 합니다
    ([get_by_id] · `services/app_user_admin.py` 의 `get_detail` 과 같은 규칙).
    거르면 "찾는 사람이 목록에 없다" 가 생깁니다.

    **키셋입니다 — OFFSET 이 아닙니다.** 읽는 사이에 가입이 하나 들어오면 OFFSET 은
    한 명을 건너뛰거나 두 번 보여 줍니다. `created_at` 은 같은 초에 둘이 가입하면
    겹치므로 `id` 로 한 번 더 갈라, 두 값을 묶어 비교합니다 (튜플 비교라 인덱스가
    그대로 듣습니다).
    """
    stmt = select(AppUser).order_by(AppUser.created_at.desc(), AppUser.id.desc())
    if before is not None:
        at, last_id = before
        stmt = stmt.where(
            tuple_(AppUser.created_at, AppUser.id) < tuple_(at, last_id)
        )
    return list(await session.scalars(stmt.limit(limit)))


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
