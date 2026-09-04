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
    created_at: datetime


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
