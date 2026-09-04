"""관리자 계정 관리 API 의 요청 / 응답 형태 (콘솔 로드맵 A3).

**`password_hash` 가 나가지 않게 하는 것이 이 파일의 첫째 일입니다.** `models/AdminUser`
를 그대로 내보내면 Argon2id 해시가 응답에 실립니다 — 모델 파일이 "schemas 에서 걸러야
한다"고 적어 둔 자리가 여기입니다. 그래서 `AdminAccountOut` 은 컬럼을 하나씩 적습니다.
`model_config = ConfigDict(from_attributes=True)` 로 ORM 객체를 받되, 열거하지 않은
필드는 애초에 스키마에 없습니다.

**허용값은 `models/admin_user.py` 의 `ADMIN_ROLES` · `ADMIN_STATUSES` 에서 가져옵니다.**
여기에 문자열을 다시 적으면 role 을 하나 더할 때 고칠 자리가 셋(SQL · 모델 · 스키마)이
됩니다. `Literal` 로 못 쓰는 이유는 그 값들이 상수 튜플이라서고, 대신 `field_validator`
가 같은 목록으로 봅니다.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from daengs_backend.models import ADMIN_ROLES, ADMIN_STATUSES


class AdminAccountOut(BaseModel):
    """계정 한 줄. **비밀번호와 관련된 것은 아무것도 없습니다.**"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    login_id: str
    name: str
    role: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class AdminAccountCreate(BaseModel):
    """계정 발급.

    길이 제한은 `admin_users` 의 VARCHAR 와 맞췄습니다. 비밀번호는 컬럼이 TEXT 라
    맞출 대상이 없고, `LoginRequest` 와 같은 이유로 위쪽만 막습니다 — 1KB 짜리가
    들어오면 Argon2id 해싱에만 시간을 씁니다.

    **최소 12자입니다.** 로그인 잠금이 프로세스 메모리라 재시작에 풀리는 상태라
    (`services/login_attempts.py`), 잠금에만 기댈 수 없습니다. 발급하는 쪽이 사람이라
    "12자 이상" 정도가 화면에서 지킬 수 있는 선입니다.
    """

    login_id: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=1024)
    name: str = Field(min_length=1, max_length=50)
    role: str

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        if value not in ADMIN_ROLES:
            raise ValueError(f"role 은 {', '.join(ADMIN_ROLES)} 중 하나여야 합니다.")
        return value


class AdminAccountPatch(BaseModel):
    """role 과 status 변경. **둘 다 선택이고, 준 것만 바뀝니다.**

    한 엔드포인트로 묶은 이유는 화면이 한 줄에서 둘 다 만지기 때문입니다. 다만 감사
    기록은 **바뀐 것마다 따로 남습니다** (`services/admin_account.py`) — 나중에
    "누가 정지시켰나"를 셀 때 role 변경 행까지 세지 않으려는 것입니다.

    `None` 은 "안 바꾼다"입니다. 두 컬럼 다 NOT NULL 이라 "값을 지운다"와 헷갈릴
    자리가 없습니다.
    """

    role: str | None = None
    status: str | None = None

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str | None) -> str | None:
        if value is not None and value not in ADMIN_ROLES:
            raise ValueError(f"role 은 {', '.join(ADMIN_ROLES)} 중 하나여야 합니다.")
        return value

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in ADMIN_STATUSES:
            raise ValueError(
                f"status 는 {', '.join(ADMIN_STATUSES)} 중 하나여야 합니다."
            )
        return value


class PasswordChangeRequest(BaseModel):
    """**자기** 비밀번호 변경 (#222). 대상은 경로가 아니라 토큰이 정합니다.

    id 를 받는 필드가 없는 것이 이 스키마의 첫째 일입니다 — 받는 순간
    `PATCH /admin/admins/{id}` 와 같은 문이 되고, 그건 `ADMIN_MANAGE` 로 잠가야 하는
    문입니다. 이 카드는 `Perm.READ` 로 열려 있으므로 **누구를 바꾸는지는 토큰만이
    말합니다** (`routers/admin_account.py`).

    **`current_password` 에는 최소 길이를 걸지 않습니다.** `new_password` 와 같은 12자를
    걸고 싶어지는 자리지만, 그러면 12자 미만인 옛 비밀번호를 쓰는 사람이 **바로 그 이유로
    422 에 막혀 못 바꿉니다** — 이 카드가 없애려던 상태 그대로입니다 (`uv run seed-admin`
    으로 만든 최초 계정이 그럴 수 있습니다). 여기 값은 대조만 하고 저장하지 않으므로
    길이를 볼 이유도 없습니다.

    `new_password` 쪽 12자는 `AdminAccountCreate` 와 같은 값입니다. 발급 때만 길고 바꿀 때
    짧아지면 제한이 없는 것과 같습니다. 위쪽 1024 도 같은 이유입니다 — 1KB 짜리가 들어오면
    Argon2id 해싱에만 시간을 씁니다.
    """

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)

    @model_validator(mode="after")
    def _actually_different(self) -> "PasswordChangeRequest":
        """같은 값으로 바꾸는 요청을 422 로 막습니다.

        서버가 받아들여도 되는 요청처럼 보이지만 아닙니다 — 성공 경로가 **그 계정의
        세션을 전부 끊으므로**(`services/admin_account.py`), 아무것도 바뀌지 않은 채
        모든 기기에서 로그아웃만 되는 요청이 됩니다. 비밀번호를 확인하려다 오타로
        같은 값을 두 번 넣는 것이 실제로 일어나는 자리입니다.
        """
        if self.current_password == self.new_password:
            raise ValueError("새 비밀번호가 지금 쓰는 것과 같습니다.")
        return self
