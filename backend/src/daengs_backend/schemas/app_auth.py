"""앱 회원 인증 API 의 요청 / 응답 형태.

**여기는 토큰이 응답 본문에 들어갑니다.** 관리자(schemas/auth.py)와 정반대인데,
이유가 있습니다 — 관리자 웹은 브라우저라 httpOnly 쿠키를 쓸 수 있고, 그게 XSS 로부터
토큰을 지키는 수단입니다. 네이티브 앱에는 쿠키 저장소가 없고 XSS 도 없어서, 토큰을
받아 보관하고 `Authorization: Bearer` 로 보냅니다.

**앱은 이 토큰을 안전한 저장소에 넣어야 합니다** (iOS Keychain / Android
EncryptedSharedPreferences). 평문 파일이나 로그에 남기면 쿠키를 안 쓴 의미가 없습니다.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class KakaoLoginRequest(BaseModel):
    """앱이 카카오에서 받은 `id_token` 을 그대로 넘깁니다.

    **access token 이 아닙니다.** OIDC 를 켜고 `scope` 에 `openid` 를 넣어야 나옵니다
    (D-017). 앱이 access token 을 보내면 서명 검증에서 막힙니다.
    """

    #: 길이 제한은 형식에 맞춘 것이 아니라 **입력을 자르기 위한** 것입니다.
    #: 카카오 id_token 은 1KB 안쪽인데, 수십 KB 짜리를 보내면 서명 검증에만 시간을 씁니다.
    id_token: str = Field(min_length=1, max_length=4096)

    #: 앱이 로그인 요청에 nonce 를 넣었다면 같은 값을 여기에도 넣습니다.
    #: 넣으면 가로챈 id_token 의 재사용을 막을 수 있습니다. 안 넣으면 검사하지 않습니다.
    nonce: str | None = Field(default=None, max_length=256)


class RefreshRequest(BaseModel):
    """재발급 / 로그아웃에 쓰는 refresh 토큰.

    관리자는 이것을 쿠키로 보내지만 앱은 쿠키가 없어서 본문으로 받습니다.
    """

    refresh_token: str = Field(min_length=1, max_length=256)


class AppSessionResponse(BaseModel):
    """로그인 / 재발급 성공. **토큰이 여기 들어 있습니다** (위 모듈 주석 참고).

    `refresh_token` 은 **쓸 때마다 바뀝니다**(회전, D-015). 앱은 응답을 받을 때마다
    저장해 둔 값을 덮어써야 합니다. 옛 것을 계속 쓰면 재사용 감지에 걸려 그 회원의
    세션이 전부 끊깁니다.
    """

    app_user_id: uuid.UUID
    access_token: str
    refresh_token: str
    #: 항상 "Bearer" 입니다. 앱이 헤더를 조립할 때 쓰라고 넣어 둡니다.
    token_type: str = "Bearer"
    #: access 는 5분입니다. 만료를 맞고 대응하면 사용자가 한 박자 멈칫하므로,
    #: 앱이 미리 재발급을 걸 수 있게 시각을 줍니다.
    access_expires_at: datetime
    #: refresh 는 7일이고 **회전해도 연장되지 않습니다.** 이 시각이 지나면
    #: 카카오 로그인부터 다시 해야 합니다.
    refresh_expires_at: datetime


class AppMeResponse(BaseModel):
    """`GET /auth/app/me` — 지금 로그인한 앱 회원.

    **DB 를 다시 읽어서 만듭니다.** 토큰에는 회원 id 밖에 없고, 정지 여부는
    최대 5분 낡을 수 있습니다.

    `email` 은 복호화한 평문입니다. **본인에게만 나갑니다** — 이 엔드포인트는 자기
    토큰으로만 부를 수 있습니다. 관리자가 남의 개인정보를 보는 것은 권한
    (`Perm.PII_READ`)이 걸린 별도 API 이고 여기가 아닙니다.
    """

    app_user_id: uuid.UUID
    kakao_id: int
    #: 카카오에서 이메일 동의를 못 받았으면 None 입니다.
    email: str | None
    status: str
    created_at: datetime
