"""강아지 프로필 API 의 요청 / 응답 형태.

**모르는 것은 `None` 으로 받습니다.** 성별·중성화·생일이 그렇습니다. 필수로 하면
모르는 사람이 아무 값이나 넣고, 그러면 그 값은 데이터로 못 씁니다. `None` 을
`false` 나 기본값으로 바꾸지 마세요 — 안 물어본 것과 아니라고 답한 것은 다릅니다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

#: 생일 칸의 날짜가 무슨 날인지. 모델 쪽 `PET_BIRTH_DATE_KINDS` 와 같은 값입니다.
BirthDateKind = Literal["birthday", "family_day"]
PetSex = Literal["male", "female"]


class PetUpsert(BaseModel):
    """등록·수정에 함께 쓰는 본문. 수정은 전체를 다시 보냅니다(PUT).

    부분 수정(PATCH)을 안 두는 이유는 **`None` 의 뜻이 갈리기 때문**입니다.
    PATCH 에서 `sex: null` 은 "안 건드림"인지 "모름으로 바꿈"인지 알 수 없습니다.
    항목이 여섯 개뿐이라 전체를 보내는 편이 애매함이 없습니다.
    """

    name: str = Field(min_length=1, max_length=40)

    #: 앱 아바타 27종 + `mix`(믹스·잘 모르겠어요).
    #: **서버는 목록을 검사하지 않습니다** — 어휘의 주인이 앱이라서, 여기에 목록을
    #: 두면 앱에 견종 하나 더할 때마다 서버 배포가 딸려 옵니다.
    breed: str = Field(min_length=1, max_length=60)

    sex: PetSex | None = None
    neutered: bool | None = None

    #: kg. 소수 한 자리까지. 상한은 오타를 거르는 선입니다.
    weight_kg: Decimal | None = Field(default=None, gt=0, le=200, decimal_places=1)

    #: 태어난 날 또는 가족이 된 날. **둘 다 모르면 비워 둡니다.**
    birth_date: date | None = None
    birth_date_kind: BirthDateKind | None = None

    #: 배웅한 날. **None 이면 아직 함께 있는 아이입니다.**
    #:
    #: 삭제와 다른 일입니다 — 이 날짜가 차도 아이는 목록에 남고 함께한 산책도 남습니다.
    farewell_on: date | None = None

    @model_validator(mode="after")
    def _farewell_on(self) -> Self:
        """배웅한 날은 **앞날일 수 없고 태어나기 전일 수도 없습니다.**

        DB 에도 같은 CHECK 가 있지만 여기서 막아야 422 로 이유를 말해 줄 수 있습니다 —
        DB 까지 가면 500 입니다 (`_birth_date_pair` 와 같은 이유).

        오늘을 서버 시간으로 봅니다. 기기 시간대와 하루가 어긋날 수 있지만, 여기서
        거르는 것은 "2033년" 같은 오타이지 하루 차이가 아닙니다.
        """
        if self.farewell_on is None:
            return self
        if self.farewell_on > date.today():
            raise ValueError("배웅한 날은 오늘보다 뒤일 수 없습니다.")
        if self.birth_date is not None and self.farewell_on < self.birth_date:
            raise ValueError("배웅한 날은 태어난 날보다 앞설 수 없습니다.")
        return self

    @model_validator(mode="after")
    def _birth_date_pair(self) -> Self:
        """날짜와 종류는 **같이 있거나 같이 없어야** 합니다.

        한쪽만 오면 "이 날짜가 무슨 날인지 모르는" 행이 생기고, 그건 안 받은 것만
        못합니다. DB 에도 같은 CHECK 가 있지만 여기서 막아야 422 로 이유를 말해 줄
        수 있습니다 — DB 까지 가면 500 입니다.
        """
        if (self.birth_date is None) != (self.birth_date_kind is None):
            raise ValueError("birth_date 와 birth_date_kind 는 같이 있거나 같이 없어야 합니다.")
        return self


class PetResponse(BaseModel):
    id: uuid.UUID
    name: str
    breed: str
    sex: PetSex | None
    neutered: bool | None
    weight_kg: Decimal | None
    birth_date: date | None
    birth_date_kind: BirthDateKind | None
    farewell_on: date | None

    #: 이 아이가 대표인가. `app_users.primary_pet_id` 에서 옵니다 —
    #: pets 테이블에는 그런 칸이 없습니다 (05_pets.sql 주석 참고).
    is_primary: bool

    #: 프로필 사진이 있는가. **URL 을 여기 담지 않습니다** — 목록 한 번에 N 개의
    #: 주소를 만들면 저장소를 N 번 두드리게 되고, 앱이 안 그리는 아이 것까지
    #: 만들게 됩니다. 앱은 이 값이 true 인 아이에 대해서만 사진 주소를 따로 받습니다.
    has_photo: bool = False

    #: 사진이 마지막으로 바뀐 시각. **앱의 캐시 열쇠입니다** — 이 값이 그대로면
    #: 다시 안 받아도 됩니다. 사진이 없으면 None 입니다.
    photo_updated_at: datetime | None = None


#: 프로필 사진으로 받는 형식. 모델·DB CHECK 와 같은 목록입니다.
PetPhotoContentType = Literal["image/jpeg", "image/webp"]


class PetPhotoTicketRequest(BaseModel):
    """어떤 형식으로 올릴지. **키의 확장자를 이 값이 정합니다.**

    파일 이름을 안 받는 이유는 앱이 준 이름을 경로로 쓰면 안 되기 때문입니다
    (`build_object_key` 가 basename 의 suffix 만 쓰는 것과 같은 자리).
    """

    content_type: PetPhotoContentType = "image/jpeg"


class PetPhotoTicketResponse(BaseModel):
    """사진을 올릴 자리. 앱은 여기 적힌 곳으로 **직접 PUT** 합니다.

    보행·점령지의 티켓과 같은 모양입니다 — 저장소가 GCS 로 되돌아가도 앱 코드가
    안 바뀌게 하려는 것입니다 (그때는 이 주소가 Signed URL 이 됩니다).
    """

    #: backend 가 만든 키. **앱이 정하지 않습니다**(원칙 6).
    storage_key: str

    #: 여기로 PUT 합니다.
    upload_url: str

    #: 그대로 붙여야 하는 헤더. Content-Type 이 안 맞으면 confirm 에서 거절됩니다.
    upload_headers: dict[str, str]

    expires_in_seconds: int


class PetPhotoResponse(BaseModel):
    """사진을 내려받을 자리."""

    #: 이 주소로 GET 합니다. 저장소가 GCS 면 만료가 있는 Signed URL 입니다.
    #:
    #: ⚠️ 로컬 볼륨에서는 **만료가 없습니다.** 대신 bridge 라우터가 "backend 가
    #:    발급한 키인지"를 DB 로 확인합니다 (D-052).
    download_url: str
    content_type: str
    size_bytes: int
    updated_at: datetime


class PetListResponse(BaseModel):
    """내 강아지 전부. **등록 순서대로**입니다.

    앱이 그 순서로 카드를 늘어놓고, 대표를 지웠을 때 승계되는 아이도 이 순서의
    첫 번째입니다.
    """

    pets: list[PetResponse]

    #: 한 계정에 등록할 수 있는 마릿수. 앱이 `+` 버튼을 언제 감출지 정하는 데 씁니다.
    #: **서버가 알려 줍니다** — 앱에 숫자를 박아 두면 서버와 갈라집니다.
    max_pets: int


class PrimaryPetRequest(BaseModel):
    """대표로 세울 강아지. 내 강아지가 아니면 404 입니다."""

    pet_id: uuid.UUID
