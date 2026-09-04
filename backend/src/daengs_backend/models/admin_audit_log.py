"""관리자 행위 감사 기록. 원본 스키마는 `db/init/03_auth.sql` 입니다.

**로그가 아니라 데이터입니다.** 운영 로그(에러 · 스택트레이스)는 파일로 가고 여기
들어오지 않습니다 — 그 선은 `docs/console/roadmap.md` §6 에 있습니다.

append-only 라 `updated_at` 이 없습니다. 다른 모델에 다 있는 것이 여기만 없는 것은
빠뜨린 게 아닙니다.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

# `action` 에 들어가는 값. **SQL 쪽에 CHECK 가 없습니다** — 카드마다 늘어나는
# 목록이라 DB 에 묶으면 화면이 하나 생길 때마다 두 DB 에 ALTER 를 돌려야 합니다.
# 그래서 "실제로 쓰는 값이 무엇인지"는 이 상수가 유일한 목록입니다. 새 행위를
# 기록하기 시작하면 여기에 먼저 더하세요.
#
# 이름은 `주체.무엇을.어떻게` 입니다. 앞자리를 `admin.` 으로 맞춰 두면 나중에
# 앱 회원 행위를 같은 테이블에 넣게 되더라도 접두어로 갈립니다.
AUDIT_LOGIN_SUCCESS = "admin.login.success"
AUDIT_LOGIN_FAILED_UNKNOWN_ID = "admin.login.failed_unknown_id"
AUDIT_LOGIN_FAILED_PASSWORD = "admin.login.failed_password"
AUDIT_LOGIN_DENIED_SUSPENDED = "admin.login.denied_suspended"

# 관리자 계정 관리 (`services/admin_account.py`). `target_type='admin_user'` ·
# `target_id` 가 채워집니다 — 대상이 없는 로그인 기록과 다른 점입니다.
#
# **정지와 해제를 한 action 으로 합치지 않습니다.** `detail` 을 펼쳐 봐야 어느
# 쪽인지 알게 되면, "누가 정지시켰나"를 세는 것이 집계가 아니라 파싱이 됩니다.
AUDIT_ACCOUNT_CREATED = "admin.account.created"
AUDIT_ACCOUNT_ROLE_CHANGED = "admin.account.role_changed"
AUDIT_ACCOUNT_SUSPENDED = "admin.account.suspended"
AUDIT_ACCOUNT_REACTIVATED = "admin.account.reactivated"

# 본인 비밀번호 변경 (#222). **이 목록에서 유일하게 주체와 대상이 같은 행위입니다** —
# `admin_user_id` 와 `target_id` 에 같은 값이 들어갑니다. 위 넷은 전부 남에게 한 일이라
# 둘이 다르고, 그 차이를 모르고 "누가 누구에게" 를 세면 이 행이 자기 자신에게 한 일로
# 잡힙니다 (맞는 말이지만, 계정 관리 행위로 세면 안 됩니다).
#
# `detail` 은 `{"sessions_dropped": n}` 입니다. **정지(`suspended`)와 키 이름이 같지만
# 읽는 법이 다릅니다** — 저기서는 n 이 전부 남의 세션이고, 여기서는 **지금 이 요청을 보낸
# 본인 브라우저가 그 안에 포함**됩니다. 화면이 n 을 "다른 데서 로그인돼 있던 수" 로 그리면
# 항상 하나씩 많습니다 (2026-09-04 사람 결정: 남기되 화면이 "본인 것 포함"을 밝힌다).
AUDIT_ACCOUNT_PASSWORD_CHANGED = "admin.account.password_changed"

# 앱 회원을 상대로 한 관리 행위 (`services/app_user_admin.py` · #212). 대상은 `app_users`
# 행이라 `target_type='app_user'` 입니다 — 위 계정 관리(`admin_user`)와 접두어로 갈립니다.
#
# **`pii_revealed` 가 이 테이블이 생긴 첫째 이유입니다** (로드맵 §1 "누가 복호화를 봤나").
# 가려서 보는 것(`services/app_user_admin.py` 의 마스킹)은 남기지 않습니다 — 남길 가치가
# 있는 것은 "가려서 봤다"가 아니라 "원문을 열어 봤다" 이고, 화면을 열 때마다 행이 쌓이면
# 진짜 따져야 하는 행위가 그 안에 묻힙니다.
AUDIT_APP_USER_PII_REVEALED = "admin.app_user.pii_revealed"
AUDIT_APP_USER_SUSPENDED = "admin.app_user.suspended"
AUDIT_APP_USER_REACTIVATED = "admin.app_user.reactivated"

# AI 답변 신고 처리 (`services/answer_report.py` · A1). **`turn_revealed` 는 신고 상세를
# 열어 대화 원문을 본 순간에만 남깁니다 — 목록을 훑는 것은 남기지 않습니다** (D-053 ③).
# `pii_revealed` 가 그은 선과 같습니다: 남길 가치가 있는 것은 "가려서 봤다" 가 아니라
# "원문을 열어 봤다" 이고, 화면을 열 때마다 행이 쌓이면 진짜 따져야 하는 행위가 묻힙니다.
#
# `target_type` 은 `app_user` 입니다 — 신고 id 가 아니라 **누구의 대화를 봤나**가
# 나중에 따질 대상이기 때문입니다. 어느 신고였는지는 `detail.report_id` 에 남습니다.
AUDIT_REPORT_TURN_REVEALED = "admin.report.turn_revealed"
AUDIT_REPORT_RESOLVED = "admin.report.resolved"

AUDIT_ACTIONS = (
    AUDIT_LOGIN_SUCCESS,
    AUDIT_LOGIN_FAILED_UNKNOWN_ID,
    AUDIT_LOGIN_FAILED_PASSWORD,
    AUDIT_LOGIN_DENIED_SUSPENDED,
    AUDIT_ACCOUNT_CREATED,
    AUDIT_ACCOUNT_ROLE_CHANGED,
    AUDIT_ACCOUNT_SUSPENDED,
    AUDIT_ACCOUNT_REACTIVATED,
    AUDIT_ACCOUNT_PASSWORD_CHANGED,
    AUDIT_APP_USER_PII_REVEALED,
    AUDIT_APP_USER_SUSPENDED,
    AUDIT_APP_USER_REACTIVATED,
    AUDIT_REPORT_TURN_REVEALED,
    AUDIT_REPORT_RESOLVED,
)

# `target_type` 에 들어가는 값. 대상이 없는 행위(로그인)는 NULL 입니다.
AUDIT_TARGET_TYPES = ("app_user", "admin_user")


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    # 모델은 SQL 을 따라가는 쪽이라 제약을 여기서 만들지는 않습니다
    # (create_all 을 부르지 않으므로 DDL 로 나가지 않습니다).
    __table_args__ = (
        CheckConstraint(
            "detail IS NULL OR jsonb_typeof(detail) = 'object'",
            name="admin_audit_log_detail_object_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # **NULL 이 허용되는 이유는 로그인 실패입니다** — 없는 아이디로 두드린 시도는
    # 가리킬 `admin_users` 행이 아예 없습니다. 무엇을 시도했는지는 `detail` 에 남습니다.
    #
    # RESTRICT 는 계정을 지우지 않고 `status='suspended'` 로 막는다는 기존 설계를
    # DB 가 지키게 하는 것입니다. `RefreshToken` 의 CASCADE 와 반대인 것은 의도한
    # 차이입니다 — 세션은 없어져야 하고 기록은 남아야 합니다.
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("admin_users.id", ondelete="RESTRICT")
    )

    action: Mapped[str] = mapped_column(String(60))

    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    # **복호화된 개인정보를 넣지 마세요** — "무엇을 열었나"(대상 id · 컬럼 이름)
    # 까지입니다. 넣기 시작하면 이 테이블이 두 번째 개인정보 저장소가 되고,
    # 탈퇴 시 파기 대상이 하나 늘어납니다 (관측에 질문 원문을 금지한 D-037 과 같은 선).
    #
    # ⚠ **`none_as_null=True` 를 빼면 detail 없는 행이 전부 500 이 됩니다.**
    # SQLAlchemy 의 JSON/JSONB 기본값은 파이썬 `None` 을 **SQL NULL 이 아니라 JSON `null`**
    # 로 넣습니다. 그러면 `jsonb_typeof(detail)` 이 `'null'` 이라 위 CHECK
    # (`detail IS NULL OR jsonb_typeof(detail) = 'object'`)에 걸립니다.
    #
    # 대상이 없는 행위(정지 **해제** 처럼 남길 값이 없는 것)가 그 경우이고, 2026-09-04 에
    # `admin.account.reactivated` 가 실제로 그렇게 죽었습니다. 가짜 세션을 쓰는 테스트는
    # DB CHECK 을 모르므로 **이 자리는 테스트가 아니라 이 한 줄이 지킵니다.**
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    request_id: Mapped[str | None] = mapped_column(String(64))

    # nginx 가 넘긴 X-Real-IP (D-005). `RefreshToken.ip` 와 같은 값입니다.
    ip: Mapped[str | None] = mapped_column(INET)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
