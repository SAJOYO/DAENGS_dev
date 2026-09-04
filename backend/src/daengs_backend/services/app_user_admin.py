"""회원 조회와 마스킹 (콘솔 로드맵 A2 · #211).

HTTP 를 모릅니다 — 나가는 것은 예외와 dataclass 뿐이고, 404 로 바꿀지는 라우터가 정합니다.

--------------------------------------------------------------------------------
**마스킹도 복호화입니다. 그런데 감사 기록을 남기지 않습니다.**

`a***@gmail.com` 을 만들려면 먼저 원문을 열어야 하므로 이 파일은 `core/crypto.py` 를
부릅니다. 그래도 `admin_audit_log` 에 남기지 않는 것은 **남길 가치가 있는 것이 "가려서
봤다"가 아니라 "원문을 열어 봤다"** 이기 때문입니다. 회원 화면을 한 번 여는 것마다 행이
쌓이면 감사 테이블이 조회 로그가 되고, 그러면 진짜 따져야 하는 행위가 그 안에 묻힙니다
(`docs/console/roadmap.md` §6 — 감사 로그는 로그가 아니라 데이터).

그 선이 같은 파일 안의 `find`·`get_detail`(마스킹, 기록 없음)과 `reveal`(원문, 기록
남김)을 가릅니다 — #212 가 아래쪽을 더했습니다. **마스킹 경로에 감사를 붙이고 싶어지면
그건 마스킹이 원문을 너무 많이 흘리고 있다는 신호**이지, 기록을 늘릴 이유가 아닙니다.
--------------------------------------------------------------------------------

**상태 변경은 `withdrawn` 을 건드리지 않습니다** (`update_status`). 탈퇴는 본인 요청이고
개인정보 파기가 따라온 일이라, 관리자가 되돌리면 삭제 요청을 관리자가 무르는 것이 됩니다.
되살아나는 길은 하나뿐입니다 — **본인이 카카오로 다시 로그인**하면
`services/app_auth.py` 의 `login_with_kakao` 가 `active` 로 돌리고 프로필을 다시 채웁니다.

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
from daengs_backend.core.subject import SubjectType
from daengs_backend.models import (
    AUDIT_APP_USER_PII_REVEALED,
    AUDIT_APP_USER_REACTIVATED,
    AUDIT_APP_USER_SUSPENDED,
    AppUser,
    Pet,
)
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.services import audit
from daengs_backend.services import session as session_service

logger = logging.getLogger(__name__)

__all__ = [
    "AppUserNotFoundError",
    "AppUserView",
    "RevealedPii",
    "WithdrawnMemberError",
    "find",
    "get_detail",
    "mask_email",
    "mask_name",
    "mask_phone",
    "reveal",
    "update_status",
]


class AppUserAdminError(Exception):
    """회원 관리 요청을 받아들일 수 없습니다. 아래 것들의 부모입니다."""


class AppUserNotFoundError(AppUserAdminError):
    """그 id 의 회원이 없습니다."""


class WithdrawnMemberError(AppUserAdminError):
    """탈퇴한 회원입니다. 관리자가 상태를 되돌리지 않습니다 (파일 docstring)."""


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


@dataclass(frozen=True)
class RevealedPii:
    """복호화된 원문. **이 객체는 로그에도 감사 `detail` 에도 들어가면 안 됩니다.**

    `None` 인 칸은 처음부터 암호문이 없던 것입니다 — 탈퇴로 파기됐거나, 카카오에서 그
    항목 동의를 못 받았거나. 어느 쪽인지는 `AppUser.status` 가 가릅니다.
    """

    email: str | None
    phone: str | None
    name: str | None

    def opened(self) -> list[str]:
        """실제로 값이 나온 칸 이름. **감사에 남기는 것은 이것뿐입니다.**"""
        return [
            field
            for field, value in (
                ("email", self.email),
                ("phone", self.phone),
                ("name", self.name),
            )
            if value is not None
        ]


async def reveal(
    session: AsyncSession,
    *,
    app_user_id: uuid.UUID,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> RevealedPii | None:
    """개인정보 원문. **부를 때마다 감사 행이 남습니다.** 없는 회원이면 `None` 입니다.

    로드맵 §1 의 "누가 복호화를 봤나 — 알 수 없다" 를 닫는 자리입니다.

    --------------------------------------------------------------------------
    **기록을 먼저 확정하고 원문을 돌려줍니다.** 순서가 이 함수의 전부입니다.

    `record_and_commit` 이 실패하면 예외가 그대로 올라가고 호출자는 원문을 받지
    못합니다. 반대로 했다면 "본 사람은 있는데 기록은 없는" 조회가 생기고, 그건
    감사 로그가 있는 것이 없는 것보다 나쁜 상태입니다 (`services/audit.py`).

    이 경로는 읽기만 하므로 `record_and_commit` 이 함께 확정할 다른 변경이 없습니다 —
    그 함수가 경고하는 조건을 만족합니다.
    --------------------------------------------------------------------------

    **`detail` 에 값을 넣지 않습니다.** 남기는 것은 "어느 회원의 어느 칸을 열었나"
    까지입니다. 값을 넣으면 `admin_audit_log` 가 두 번째 개인정보 저장소가 되고,
    탈퇴 시 파기 대상이 하나 늘어납니다 (`models/admin_audit_log.py` 의 `detail` 주석).
    """
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        return None

    pii = RevealedPii(
        email=_decrypt_or_none(user.email_enc, field="email", user_id=user.id),
        phone=_decrypt_or_none(user.phone_enc, field="phone", user_id=user.id),
        name=_decrypt_or_none(user.name_enc, field="name", user_id=user.id),
    )

    await audit.record_and_commit(
        session,
        action=AUDIT_APP_USER_PII_REVEALED,
        admin_user_id=actor_id,
        target_type="app_user",
        target_id=user.id,
        # 칸 **이름**만입니다. 값은 절대 넣지 마세요.
        detail={"opened": pii.opened()},
        ip=ip,
    )
    logger.warning(
        "개인정보 원문 조회 (admin=%s, app_user=%s, 칸=%s)",
        actor_id,
        user.id,
        pii.opened(),
    )
    return pii


async def update_status(
    session: AsyncSession,
    *,
    app_user_id: uuid.UUID,
    actor_id: uuid.UUID,
    status: str,
    ip: str | None = None,
) -> AppUser:
    """회원을 정지하거나 정지를 풉니다. `active` 와 `suspended` 둘뿐입니다.

    **`withdrawn` 은 받지 않습니다** — 스키마가 먼저 막고, 여기서는 *대상이* 탈퇴한
    회원일 때 막습니다. 탈퇴는 본인 요청이고 개인정보 파기가 따라온 일이라, 관리자가
    `active` 로 돌리면 삭제 요청을 관리자가 무르는 것이 됩니다. 되살아나는 길은
    본인이 카카오로 다시 로그인하는 것 하나입니다 (파일 docstring).

    **정지는 `status` 와 세션을 둘 다 건드립니다.** `status` 는 새 로그인을 막고
    (`app_auth.login_with_kakao` 의 `SuspendedError`), 이미 나간 세션은 `drop_all` 이
    끊습니다. 앱 회원의 `refresh` 도 status 를 보지만(`app_auth.refresh`), 그건 그쪽이
    다음에 재발급을 시도할 때이고 여기서 끊으면 지금입니다.

    ⚠ **access token 5분은 못 줄입니다.** 무상태라 요청마다 DB 를 보지 않기로 한 것이
    D-015 입니다. 정지가 완전히 반영되는 데 최대 그만큼 걸립니다 — 관리자 계정 정지
    (`services/admin_account.py`)와 같습니다.
    """
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        raise AppUserNotFoundError

    if user.status == "withdrawn":
        logger.warning(
            "회원 상태 변경 거부: 탈퇴한 회원 (admin=%s, app_user=%s)", actor_id, user.id
        )
        raise WithdrawnMemberError

    if user.status == status:
        # 이미 그 값입니다. 사건이 아니므로 기록하지 않습니다
        # (`services/admin_account.py` 의 같은 판단).
        return user

    user.status = status
    if status == "suspended":
        dropped = await session_service.drop_all(session, SubjectType.APP, user.id)
        await audit.record(
            session,
            action=AUDIT_APP_USER_SUSPENDED,
            admin_user_id=actor_id,
            target_type="app_user",
            target_id=user.id,
            # 끊은 세션 수. 0이면 "이미 안 쓰던 계정", 여럿이면 "쓰던 사람을 끊었다".
            detail={"sessions_dropped": dropped},
            ip=ip,
        )
        logger.warning(
            "회원 정지 (admin=%s, app_user=%s, 세션 %d개 끊음)", actor_id, user.id, dropped
        )
    else:
        await audit.record(
            session,
            action=AUDIT_APP_USER_REACTIVATED,
            admin_user_id=actor_id,
            target_type="app_user",
            target_id=user.id,
            ip=ip,
        )
        logger.info("회원 정지 해제 (admin=%s, app_user=%s)", actor_id, user.id)

    # 상태 변경과 그 기록을 한 번에 확정합니다 — 정지는 됐는데 기록만 없는 상태를
    # 만들지 않으려는 것입니다 (`services/audit.py` 의 성공 경로).
    await session.commit()
    return user
