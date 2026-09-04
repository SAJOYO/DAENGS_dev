"""회원 조회와 마스킹 (콘솔 로드맵 A2 · #211).

HTTP 를 모릅니다 — 나가는 것은 예외와 dataclass 뿐이고, 404 로 바꿀지는 라우터가 정합니다.

--------------------------------------------------------------------------------
**마스킹도 복호화입니다. 그런데 감사 기록을 남기지 않습니다.**

`a***@gmail.com` 을 만들려면 먼저 원문을 열어야 하므로 이 파일은 `core/crypto.py` 를
부릅니다. 그래도 `admin_audit_log` 에 남기지 않는 것은 **남길 가치가 있는 것이 "가려서
봤다"가 아니라 "원문을 열어 봤다"** 이기 때문입니다. 회원 화면을 한 번 여는 것마다 행이
쌓이면 감사 테이블이 조회 로그가 되고, 그러면 진짜 따져야 하는 행위가 그 안에 묻힙니다
(`docs/console/roadmap.md` §6 — 감사 로그는 로그가 아니라 데이터).

그 선이 이 카드와 짝 카드(#212)를 가르는 자리입니다. 저쪽이 `pii:read` 로 원문을
돌려주면서 `admin.app_user.pii_revealed` 를 남깁니다. **여기에 감사를 붙이고 싶어지면
그건 마스킹이 원문을 너무 많이 흘리고 있다는 신호**이지, 기록을 늘릴 이유가 아닙니다.
--------------------------------------------------------------------------------

**검색은 값 하나가 통째로 맞아야 합니다.** `email_hash` 가 HMAC-SHA256 이라 부분
문자열로는 원천적으로 못 찾습니다 (D-012 · 03_auth.sql). 대소문자와 앞뒤 공백은
`blind_index` 가 안에서 `strip().lower()` 로 맞춰 주니 신경 쓰지 않아도 됩니다.
목록을 통째로 주는 길은 두지 않았습니다 — `find` 와 라우터의 `search` 를 보세요.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.crypto import blind_index, decrypt
from daengs_backend.models import AppUser, Pet
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo

logger = logging.getLogger(__name__)

__all__ = ["AppUserView", "find", "get_detail", "mask_email", "mask_name", "mask_phone"]


@dataclass(frozen=True)
class AppUserView:
    """회원 한 명 + 마스킹 결과 + 반려견. 라우터가 스키마로 옮깁니다.

    모델을 그대로 넘기지 않는 것은 마스킹된 값이 **컬럼이 아니라 계산 결과**라서입니다.
    `AppUser` 에 얹으면 다음 사람이 그걸 컬럼으로 착각합니다.
    """

    user: AppUser
    email_masked: str | None
    phone_masked: str | None
    name_masked: str | None
    pets: list[Pet]


def mask_email(value: str) -> str:
    """`daengs@example.com` → `da***@example.com`.

    **도메인은 남깁니다.** 도메인만으로는 사람이 특정되지 않고, 관리자가 화면에서 하려는
    일("메일 보낸 사람이 이 회원이 맞나")에 필요한 단서입니다.

    로컬 파트가 2자 이하면 통째로 가립니다 — 2자를 남기면 그게 전부가 됩니다.
    `@` 가 없는 값은 이메일이 아니므로 일반 규칙으로 넘깁니다 (카카오가 준 값이라
    형태를 우리가 보장하지 못합니다).
    """
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_tail(value)
    head = local[:2] if len(local) > 2 else ""
    return f"{head}***@{domain}"


def mask_phone(value: str) -> str:
    """`01012345678` → `***-****-5678`. 숫자만 뽑아 **뒤 4자리**를 남깁니다.

    구분자(`-` · 공백 · `+82`)가 섞여 들어와도 같은 모양이 나오게 숫자만 봅니다.
    뒤 4자리는 본인 확인 통화에서 쓰는 관행이고, 앞자리를 남기면 통신사·지역이
    드러나면서 특정에 더 가까워집니다.
    """
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 4:
        return "***"
    return f"***-****-{''.join(digits[-4:])}"


def mask_name(value: str) -> str:
    """`홍길동` → `홍**`, `김유나` → `김**`, `이서` → `이*`.

    한글 이름은 성 한 자를 남기는 것이 관행입니다. 한 자짜리 이름은 통째로 가립니다 —
    남길 것이 성밖에 없어 마스킹이 아무 일도 안 하게 됩니다.
    """
    if len(value) <= 1:
        return "*"
    return value[0] + "*" * (len(value) - 1)


def _mask_tail(value: str) -> str:
    """이메일 모양이 아닌 값을 위한 기본 규칙. 앞 2자만 남깁니다."""
    if len(value) <= 2:
        return "***"
    return f"{value[:2]}***"


def _decrypt_or_none(blob: bytes | None, *, field: str, user_id: uuid.UUID) -> str | None:
    """암호문을 열어 원문으로. `None` 이면 `None` 입니다.

    **`None` 은 오류가 아닙니다.** 탈퇴한 회원은 `*_enc` 가 파기되어 있고
    (`services/app_auth.py`), 카카오에서 그 항목 동의를 못 받은 회원도 처음부터
    비어 있습니다. 여기서 예외를 내면 화면이 통째로 500 이 됩니다.

    **열다가 실패하는 것은 다릅니다 — 그건 올립니다.** 암호문이 있는데 안 열린다는 것은
    `DAENGS_AES_KEY` 가 그때와 다르다는 뜻이고, 조용히 `***` 로 가리면 **키가 갈렸다는
    사실이 마스킹 뒤에 숨습니다.** 이 저장소에서 AES 키를 잃는 것은 복구가 없는
    사고라(CLAUDE.md), 드러나야 합니다.
    """
    if blob is None:
        return None
    try:
        return decrypt(blob)
    except Exception:
        logger.error(
            "개인정보 복호화 실패 — AES 키가 저장 시점과 다를 수 있습니다 "
            "(app_user=%s, field=%s)",
            user_id,
            field,
        )
        raise


def _to_view(user: AppUser, pets: list[Pet]) -> AppUserView:
    email = _decrypt_or_none(user.email_enc, field="email", user_id=user.id)
    phone = _decrypt_or_none(user.phone_enc, field="phone", user_id=user.id)
    name = _decrypt_or_none(user.name_enc, field="name", user_id=user.id)
    return AppUserView(
        user=user,
        email_masked=mask_email(email) if email else None,
        phone_masked=mask_phone(phone) if phone else None,
        name_masked=mask_name(name) if name else None,
        pets=pets,
    )


async def find(
    session: AsyncSession,
    *,
    email: str | None = None,
    kakao_id: int | None = None,
) -> list[AppUserView]:
    """이메일 또는 카카오 회원번호로 찾습니다. 목록은 0개나 1개입니다.

    둘 다 UNIQUE 라 언제나 한 명 이하인데도 **목록으로 돌려주는** 이유는, "못 찾음"을
    404 가 아니라 빈 결과로 다루기 위해서입니다. 검색이 0건인 것은 오류가 아닙니다.

    **`email` 을 여기서 정규화하지 마세요.** `core.crypto.blind_index` 가 안에서
    `strip().lower()` 를 합니다 — 가입(`services/app_auth.py`)도 검색도 같은 함수를
    지나므로 어긋날 자리가 애초에 없습니다. 여기에 한 겹 더 얹으면 정규화 규칙이 두
    곳이 되고, 그때부터는 한쪽만 고치는 날이 옵니다. 규칙을 바꿔야 하면 `crypto.py`
    한 곳을 고치고 **기존 행의 해시를 전부 다시 계산**해야 합니다.

    대소문자와 앞뒤 공백은 그래서 저절로 맞습니다. 그래도 **부분 검색은 안 됩니다** —
    HMAC 이라 `Daengs` 나 `@example.com` 같은 조각으로는 아무것도 안 나옵니다.

    이 검색으로는 **탈퇴한 회원을 이메일로 찾을 수 없습니다** — 탈퇴가 `email_hash` 를
    지웁니다. 그 경우 `kakao_id` 로 찾습니다.
    """
    if email is not None:
        user = await app_user_repo.get_by_email_hash(session, blind_index(email))
    elif kakao_id is not None:
        user = await app_user_repo.get_by_kakao_id(session, kakao_id)
    else:
        # 라우터가 먼저 막습니다. 여기까지 왔다면 부르는 쪽의 실수입니다 —
        # 조건 없는 조회를 조용히 "전체 목록"으로 해석하지 않습니다.
        raise ValueError("email 이나 kakao_id 중 하나는 있어야 합니다.")

    if user is None:
        return []
    # 검색 결과 줄에는 반려견을 싣지 않습니다. 상세에서 한 번 더 부릅니다.
    return [_to_view(user, [])]


async def get_detail(
    session: AsyncSession, app_user_id: uuid.UUID
) -> AppUserView | None:
    """한 명 + 반려견. 없으면 `None` 입니다.

    `status` 로 거르지 않습니다 — 정지·탈퇴한 회원도 관리 화면에서는 보여야 합니다
    (`repositories/app_user.py` 의 같은 규칙).
    """
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        return None
    pets = await pet_repo.list_for_owner(session, user.id)
    return _to_view(user, list(pets))
