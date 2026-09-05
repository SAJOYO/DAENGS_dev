"""회원 조회 API 의 응답 형태 (콘솔 로드맵 A2 · #211).

**이 파일의 첫째 일은 무엇이 나가지 *않는지* 입니다.**

    email_enc · phone_enc · name_enc   암호문. 나가면 키만 있으면 열립니다
    email_hash                         blind index. 아래를 보세요
    password 관련                       app_users 에는 없습니다 (카카오 소셜)

**`email_hash` 를 절대 내보내지 마세요.** 마스킹된 값(`a***@gmail.com`)과 같이 나가면
후보 이메일을 만들어 HMAC 을 돌려 맞춰 볼 수 있습니다 — pepper 를 쓴 이유가 그것입니다
(`db/init/03_auth.sql` 의 `email_hash` 주석). 마스킹은 "덜 보여 주는 것"이지 "못 알아보게
하는 것"이 아니라서, 대조할 수 있는 값이 같이 나가면 마스킹 자체가 무의미해집니다.

**`kakao_id` 는 평문으로 나갑니다.** 03_auth.sql 이 "카카오 밖에서는 의미가 없는 가명
식별자"로 보고 암호화하지 않기로 한 값이고, **탈퇴한 회원을 찾을 수 있는 유일한 열쇠**라
화면에 필요합니다 (탈퇴는 `email_hash` 를 지웁니다 — `services/app_auth.py`).

원문 복호화(`pii:read`)는 이 카드에 없습니다. 짝 카드(#212)가 감사 기록과 함께 붙입니다.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from daengs_backend.schemas.pet import PetResponse


class AppUserOut(BaseModel):
    """회원 한 줄. **개인정보는 전부 가려진 값입니다.**

    `*_masked` 가 `None` 인 것은 두 가지 뜻인데 **`status` 로 갈립니다** —
    `withdrawn` 이면 파기된 것이고(`services/app_auth.py` 가 `*_enc` 를 지웁니다),
    아니면 카카오에서 그 항목 동의를 못 받은 것입니다. 화면이 그 둘을 다르게
    말해 줘야 합니다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kakao_id: int

    email_masked: str | None
    phone_masked: str | None
    name_masked: str | None

    #: active · suspended · withdrawn (03_auth.sql). 셋 다 화면에 나옵니다.
    status: str
    #: 미니룸 이름표. 사용자가 직접 지은 별명이라 개인정보로 보지 않습니다.
    room_name: str | None
    #: 사람 이름. 가입할 때 서버가 발급하고 회원이 고칠 수 있습니다.
    #:
    #: **이 화면에서 회원을 알아보는 거의 유일한 값입니다.** 지금 카카오 앱키로는
    #: 이메일·전화번호·이름 동의를 못 받아 위 `*_masked` 가 전부 None 이라서,
    #: 이 칸이 없으면 한 줄이 "UUID · 숫자 · None · None · None" 입니다.
    #:
    #: None 은 **아직 발급 전**입니다 — 이 칸보다 먼저 가입한 회원이고 다음 로그인에
    #: 채워집니다. `*_masked` 의 None 과 뜻이 다릅니다 (저건 동의를 못 받았거나 파기).
    nickname: str | None
    created_at: datetime


class AppUserRosterItem(BaseModel):
    """훑는 목록의 한 줄 (A2c · #257). **[AppUserOut] 과 일부러 다릅니다.**

    가려진 개인정보(`*_masked`)도 `kakao_id` 도 없습니다 — 이 목록은 조건 없이 전
    회원을 주는 자리라, 개인정보를 아예 안 들고 있는 것이 이 화면을 열 수 있게 된
    이유입니다 (`services/app_user_admin.py` 의 `list_roster`).

    **`AppUserOut` 을 상속하지 않은 것이 의도입니다.** 상속하면 저쪽에 칸이 느는 날
    이 목록에도 조용히 따라 들어옵니다. `AdminPetOut` 이 상속인 것과 반대 방향의
    판단인데, 저기는 "같이 늘어야 하는 것"이고 여기는 "같이 늘면 안 되는 것"입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: 사람이 회원을 알아보는 값. `None` 이면 아직 발급 전(다음 로그인에 채워집니다).
    nickname: str | None
    #: 미니룸 이름표. 사용자가 지은 별명이라 개인정보로 보지 않습니다.
    room_name: str | None
    #: active · suspended · withdrawn. 셋 다 목록에 나옵니다 — 거르면 "찾는 사람이
    #: 목록에 없다" 가 생깁니다.
    status: str
    created_at: datetime
    #: 마릿수. 이름은 대표 한 마리만 오고, 나머지와 견종은 상세에서 봅니다.
    pet_count: int
    #: 대표 강아지 이름. 대표가 없으면 `None` 입니다.
    #:
    #: **개인정보가 아닙니다** — `models/pet.py` 에 암호화 컬럼이 없고, 마스킹 상세가
    #: 이미 `Perm.READ` 로 전 반려견의 이름을 내보냅니다. `ADMIN_MANAGE` 인 이 목록에
    #: 한 마리 이름을 싣는 것으로 새로 열리는 것은 없습니다.
    #:
    #: 실을 이유는 닉네임이 아직 안 채워진 회원이 많아서입니다 (다음 로그인에 발급).
    #: 그때 목록은 "이름 없음" 만 줄줄이 뜨고 사람을 가릴 값이 가입일뿐입니다.
    primary_pet_name: str | None


class AppUserRosterPage(BaseModel):
    """훑는 목록 한 쪽. **총 개수를 주지 않습니다.**

    신고·감사 목록과 같은 이유입니다 — 읽는 사이에도 늘어서 곧 틀린 숫자가 됩니다.
    `next_cursor` 를 그대로 돌려주면 다음 쪽이고, `None` 이면 마지막입니다.
    """

    users: list[AppUserRosterItem]
    next_cursor: str | None = None


class AdminPetOut(PetResponse):
    """앱이 쓰는 `PetResponse` + 사람이 읽을 견종 이름 한 칸.

    **상속입니다. 베껴 쓰지 않았습니다** — `PetResponse` 에 칸이 늘면 여기도 같이
    늘어야 하고, 따로 정의하면 한쪽만 고치는 날이 옵니다.

    `breed` 는 앱의 아바타 id(`dog_toy_poodle_light_brown`)라 화면에 그대로 두면 사람이
    못 읽습니다. 번역표는 `services/dog_context.py` 의 `BREED_LABELS` 하나뿐이라
    프론트에 사본을 두지 않고 서버가 붙여 보냅니다 (그 표부터가 DAENGS_APP
    `DogShapes.kt` 의 사본이라, 셋으로 늘리면 어긋납니다).

    **Life 와 규칙이 다릅니다.** `dog_context.breed_label` 은 모르는 id 와 `mix` 에
    `None` 을 주는데, 그건 모델에게 지어낼 거리를 주지 않으려는 것입니다. 관리 화면은
    반대로 **저장된 값을 알아야** 하므로 모르면 원래 id 를 그대로 보여 줍니다.
    """

    #: 한국어 견종명. 모르는 아바타 id 면 `breed` 와 같은 값입니다.
    breed_label: str


class AppUserDetailOut(AppUserOut):
    """상세. 반려견을 같이 실어 보냅니다.

    `pets` 를 별도 엔드포인트로 빼지 않은 것은 **화면이 언제나 같이 그리기** 때문입니다.
    강아지 정보에는 암호화 컬럼이 없어(`models/pet.py`) 여기 실어도 가려야 할 것이
    늘지 않습니다.
    """

    pets: list[AdminPetOut]


class AppUserPiiOut(BaseModel):
    """복호화된 원문 (`pii:read` · #212). **이 응답은 감사 기록과 짝입니다.**

    서버가 이것을 만들 때 `admin_audit_log` 에 행이 하나 남습니다
    (`services/app_user_admin.py` 의 `reveal`). 그래서 화면도 누르기 전에 그 사실을
    알려 줘야 합니다 — 모르고 누르는 기록은 감사가 아니라 함정입니다.

    `None` 은 오류가 아닙니다. 처음부터 암호문이 없던 칸이고, 이유는 `AppUserOut.status`
    가 가릅니다 — `withdrawn` 이면 파기된 것, 아니면 카카오 동의를 못 받은 것.
    """

    email: str | None
    phone: str | None
    name: str | None


class AppUserStatusPatch(BaseModel):
    """정지 / 정지 해제. **`withdrawn` 은 받지 않습니다.**

    탈퇴는 본인 요청이고 개인정보 파기가 따라오는 일이라, 관리자가 status 만 바꿔서
    만들면 **파기가 안 된 채로 '탈퇴함'이 되는 행**이 생깁니다. 그 경로는
    `services/app_auth.py` 의 `withdraw` 하나뿐입니다.

    `Literal` 로 쓰는 이유: `models` 의 `APP_USER_STATUSES` 는 셋(`withdrawn` 포함)이라
    그대로 검증하면 관리자가 탈퇴를 만들 수 있게 됩니다. 여기서 좁히는 것이 맞습니다.
    """

    status: Literal["active", "suspended"]
