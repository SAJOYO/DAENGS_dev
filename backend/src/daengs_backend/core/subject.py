"""인증 주체의 종류 — 관리자냐 앱 회원이냐.

토큰(`core/token.py`) · 세션 행(`models/refresh_token.py`) · 의존성(`core/deps.py`) 이
전부 이 값으로 갈립니다. **어느 한쪽이 주인이 아니라서** 여기 따로 둡니다 —
token.py 에 두면 repositories 가 토큰 모듈을 import 하게 되고, models 에 두면
DB 를 모르는 token.py 가 모델을 import 하게 됩니다.

값을 더할 일이 생기면 (예: 외부 파트너 서버) **여기만 고치는 것으로 끝나지 않습니다.**
`refresh_tokens` 의 소유자 컬럼과 CHECK 제약이 같이 늘어나야 합니다 (D-016).
"""

from enum import StrEnum

__all__ = ["SubjectType"]


class SubjectType(StrEnum):
    """이 토큰·세션이 **누구의 것인가**. access token 에는 클레임 `typ` 로 들어갑니다.

    관리자와 앱 회원은 발급기와 회전 로직을 공유하지만 **서로 다른 테이블의 서로 다른
    사람**입니다. 이 값이 없으면 `sub` 는 그냥 UUID 한 개라, 앱 회원 토큰을 관리자
    API 에 그대로 들이밀 수 있습니다. 지금은 `ROLE_PERMISSIONS` 에 없는 role 이 빈
    권한으로 떨어져 우연히 막히지만, **우연히 막히는 것에 기대면 안 됩니다** —
    권한을 안 거는 엔드포인트가 하나라도 생기면 그 자리로 들어옵니다.

    그래서 `core/deps.py` 의 의존성은 자기 종류가 아닌 토큰을 **401 로 거부합니다.**
    """

    #: 관리자 콘솔 계정 (`admin_users`). role 이 반드시 있습니다.
    ADMIN = "admin"
    #: 앱 회원 (`app_users`, 카카오 소셜 로그인). role 이 없습니다.
    APP = "app"
