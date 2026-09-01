"""인증 API 의 요청 / 응답 형태.

**토큰 문자열은 어느 응답에도 들어가지 않습니다.** 쿠키로만 나갑니다 —
httpOnly 쿠키를 쓰는 이유가 "JS 가 토큰을 못 만지게" 하는 것인데, 응답 본문에
같이 실어 보내면 그 자리에서 무의미해집니다.

models/ 의 AdminUser 를 그대로 내보내지 않는 이유도 같습니다. 그쪽에는
password_hash 가 있고, 한 번이라도 응답에 실리면 사고입니다.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """로그인 요청.

    길이 제한은 DB 컬럼(VARCHAR(50))과 맞춘 것이 아니라 **입력을 자르기 위한** 것입니다.
    아이디가 50자를 넘으면 어차피 없는 계정이고, 비밀번호가 1KB 씩 들어오면
    Argon2id 검증에만 시간을 쓰게 됩니다.
    """

    login_id: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(BaseModel):
    """로그인 / 재발급 성공. **토큰은 여기 없고 쿠키로 갔습니다.**

    만료 시각을 주는 이유는 프론트가 미리 재발급을 걸 수 있게 하기 위해서입니다.
    access 가 5분이라 만료를 맞고 나서 대응하면 사용자가 한 박자 멈칫합니다.
    쿠키는 httpOnly 라 JS 가 직접 읽어서 알아낼 방법이 없습니다.
    """

    admin_id: uuid.UUID
    role: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class MeResponse(BaseModel):
    """`GET /auth/me` — 지금 로그인한 관리자.

    **DB 를 다시 읽어서 만듭니다.** access token 안의 role 은 최대 5분 낡을 수 있는데,
    화면 구성을 그 값으로 하면 권한을 내린 사람에게 잠시 버튼이 보입니다.

    JWE 라서 프론트가 토큰을 까서 이걸 알아낼 수 없습니다 — 이 엔드포인트가
    필요한 이유입니다. role 은 원래 서버에 물어봐야 하는 값이라 손해는 아닙니다.
    """

    admin_id: uuid.UUID
    login_id: str
    name: str
    role: str
    #: ROLE_PERMISSIONS 를 펼친 것. 화면이 role 을 직접 비교하지 않게 하려는 것입니다.
    permissions: list[str]
    last_login_at: datetime | None
