"""감사 로그 조회 API 의 응답 형태 (콘솔 로드맵 A4-1 · #221).

`admin_audit_log` 는 **쓰는 쪽이 먼저 생겼습니다** (#203). 그래서 이 파일은 새 계약을
만드는 것이 아니라, 이미 쌓이고 있는 행을 화면이 읽을 수 있는 모양으로 옮기는 것뿐입니다.

--------------------------------------------------------------------------------
**`detail` 을 그대로 내보냅니다. 그게 안전한 것은 거기 값이 없기 때문입니다.**

`models/admin_audit_log.py` 가 "복호화된 개인정보를 넣지 마세요" 라고 정해 두었고,
`tests/test_app_user_pii.py` 가 그것을 지킵니다. 지금 들어 있는 것은 칸 **이름**
(`{"opened": ["email"]}`)과 끊은 세션 수, role 의 전/후, 시도된 login_id 뿐입니다.

**그 전제가 깨지면 이 화면이 그것을 그대로 뿌립니다.** 새 감사 action 을 더할 때
`detail` 에 값을 넣고 싶어지면, 넣기 전에 이 문단을 다시 보세요.
--------------------------------------------------------------------------------
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditActorOut(BaseModel):
    """행위자. **없을 수 있습니다.**

    없는 아이디로 두드린 로그인 실패(`admin.login.failed_unknown_id`)는 가리킬
    `admin_users` 행이 아예 없습니다. 그건 빠뜨린 데이터가 아니라 **그 사건의 성질**이라,
    화면이 "주체 없음" 을 정상으로 그려야 합니다. 무엇을 시도했는지는 `detail.login_id`
    에만 있습니다.
    """

    id: uuid.UUID
    login_id: str
    name: str


class AuditEntryOut(BaseModel):
    """감사 행 하나.

    `action` 은 자유 문자열입니다 — DB 에 CHECK 이 없습니다 (`models/admin_audit_log.py`:
    카드마다 늘어나는 목록이라 묶으면 화면 하나 생길 때마다 두 DB 에 ALTER 를 돌려야
    합니다). 그래서 **화면은 모르는 action 도 그릴 수 있어야 합니다.**
    """

    id: uuid.UUID
    created_at: datetime
    action: str
    actor: AuditActorOut | None
    target_type: str | None
    target_id: uuid.UUID | None
    #: action 마다 모양이 다릅니다. 위 파일 docstring 참고.
    detail: dict[str, Any] | None
    ip: str | None
    request_id: str | None


class AuditPageOut(BaseModel):
    """한 쪽. **키셋 커서라 `total` 이 없습니다.**

    총 개수를 안 주는 것은 세는 값이 비싸고(전체 스캔) 읽는 사이에도 늘어서, 화면에
    적어 봐야 곧 틀린 숫자가 되기 때문입니다. 얼마나 빨리 느는지를 보는 것은 A5 의 일이고,
    그때는 psql 로 셉니다.
    """

    entries: list[AuditEntryOut]
    #: 다음 쪽을 부를 때 그대로 돌려주는 값. `None` 이면 마지막 쪽입니다.
    next_cursor: str | None
