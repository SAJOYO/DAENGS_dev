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

from pydantic import BaseModel, Field, field_validator


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
    #: 미니룸 이름표. **None 이면 아직 안 정한 것**이고, 그때 앱이 대표 강아지
    #: 이름으로 짓습니다 — 서버가 대신 지어 주지 않습니다. 그 규칙(받침에 따라
    #: "이네"/"네")은 한국어라 앱의 것이고, 서버가 지으면 규칙이 두 벌이 됩니다.
    room_name: str | None = None
    #: 사람 이름. **집 이름(`room_name`)과 다릅니다** — 저건 "네옹이네" 고 이건 그 집
    #: 사람입니다.
    #:
    #: 이름표와 달리 **서버가 지어 줍니다.** 카카오 로그인 한 번으로 시작하게 하는 것이
    #: 앱의 목표라, 첫 화면이 "이미 사용 중입니다"로 사용자를 거절하면 안 됩니다.
    #: 앱은 받은 이름을 보여 주고, 바꾸고 싶은 사람만 PATCH 로 바꿉니다.
    #:
    #: **None 은 아직 발급 전**입니다 — 이 칸보다 먼저 가입한 회원이고, 다음 로그인에
    #: 채워집니다. 앱은 그동안 이 줄을 비워 둡니다.
    nickname: str | None = None
    #: 영수증 OCR 항목을 진단 추천 모델 학습에 쓰는 데 대한 동의 여부.
    #:
    #: **시각이 아니라 불리언입니다.** 원본은 `app_users.ocr_consent_at`
    #: (`models/app_user.py`) 이지만, 앱에게는 "언제"가 아니라 "켜져 있나"만
    #: 필요합니다. 원본 시각을 그대로 내보내면 앱이 그것으로 만료 계산 같은 것을
    #: 재해석하려 들 여지가 생기는데, 그 판단은 서버의 것입니다.
    ocr_consent: bool = False
    #: 어느 판에 동의했는지. **미동의면 None** 입니다. 화면에 안 써도 되지만,
    #: 문의 대응에서 "이 사람이 최신 판에 동의했나"를 가리는 데 씁니다.
    ocr_consent_version: str | None = None


class AppProfileUpdate(BaseModel):
    """`PATCH /auth/app/me` — 회원이 스스로 고치는 것.

    ⚠️ **보낸 칸만 바뀝니다.** 라우터가 `model_fields_set` 으로 가릅니다. 안 그러면
    닉네임만 고치려고 부른 요청이 **이름표를 같이 지웁니다** — 둘 다 기본값이 None 이라
    "안 보냈다"와 "None 으로 바꿔 달라"가 모델에서는 같은 모양이기 때문입니다.
    칸이 하나뿐일 때는 드러나지 않던 함정입니다.
    """

    #: 미니룸 이름표. **None 을 보내면 되돌립니다** — 다시 대표 강아지를 따라갑니다.
    #: 공백만 보낸 것도 같게 봅니다 (빈 이름표를 걸 수는 없습니다).
    room_name: str | None = Field(default=None, max_length=20)

    #: 사람 이름. **이름표와 달리 비울 수 없습니다.**
    #:
    #: 이름표는 비우면 앱이 대표 강아지로 지어 주지만, 닉네임은 비우면 그 회원을 가리킬
    #: 말이 없어집니다 (그게 이 칸을 만든 이유입니다). 그래서 공백만 보내면 되돌리기가
    #: 아니라 **422** 입니다 — 조용히 무시하면 앱은 바뀐 줄 알고 옛 이름을 지웁니다.
    #:
    #: 바꾸지 않으려면 **칸 자체를 안 보내면** 됩니다.
    nickname: str | None = Field(default=None, max_length=30)

    #: 영수증 OCR 항목을 진단 추천 모델 학습에 쓰는 데 대한 동의.
    #:
    #: `True` 로 보내면 `ocr_consent_at` · `ocr_consent_version` 이 **둘 다** 채워지고
    #: (버전은 서버가 정합니다 — `routers/app_auth.py` 의 `OCR_CONSENT_VERSION`.
    #: 클라이언트가 판을 골라 보낼 수 있으면 동의 기록의 근거가 무의미해집니다),
    #: `False` 로 보내면 둘 다 NULL 로 되돌아갑니다. **이미 동의한 상태에서 다시
    #: `True` 를 보내도 무시하지 않습니다** — 새 동의 이벤트로 보고 시각·판을
    #: 새로 씁니다 (재동의).
    #:
    #: 다른 칸과 마찬가지로 **안 보내면 그대로**입니다 — 라우터가
    #: `model_fields_set` 으로 가립니다. 닉네임만 고치려는 요청이 동의를 몰래
    #: 꺼버리면, 그건 사용자가 동의를 취소한 적 없는데 취소된 것으로 남는
    #: 사고입니다.
    ocr_consent: bool | None = Field(default=None)

    @field_validator("room_name")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("nickname")
    @classmethod
    def _nickname_not_blank(cls, value: str | None) -> str | None:
        # None 은 여기서 통과시킵니다 — "안 보냄"과 "null 을 보냄"을 모델은 구분하지
        # 못해서, 그 판단은 `model_fields_set` 을 볼 수 있는 라우터가 합니다.
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("닉네임은 비울 수 없습니다.")
        return trimmed


class NicknameAvailability(BaseModel):
    """`GET /auth/app/nickname/available` — 이 이름을 쓸 수 있나.

    ⚠️ **참고용입니다.** 물어본 뒤 저장하기 전에 남이 채갈 수 있습니다. 진짜 방어는
    `lower(nickname)` UNIQUE 인덱스와 저장할 때의 409 이고, 앱은 "쓸 수 있어요" 를
    보여 준 뒤에도 409 를 받을 준비가 되어 있어야 합니다.

    **자기가 지금 쓰는 이름은 `true` 입니다.** 고치다가 원래 이름으로 되돌렸을 때
    "다른 사람이 쓰고 있어요" 가 뜨면 사용자는 그것을 오류로 읽습니다.
    """

    available: bool
