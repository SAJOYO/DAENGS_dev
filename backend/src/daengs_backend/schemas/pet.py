"""강아지 프로필 API 의 요청 / 응답 형태.

**모르는 것은 `None` 으로 받습니다.** 성별·중성화·생일이 그렇습니다. 필수로 하면
모르는 사람이 아무 값이나 넣고, 그러면 그 값은 데이터로 못 씁니다. `None` 을
`false` 나 기본값으로 바꾸지 마세요 — 안 물어본 것과 아니라고 답한 것은 다릅니다.
"""

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

#: 생일 칸의 날짜가 무슨 날인지. 모델 쪽 `PET_BIRTH_DATE_KINDS` 와 같은 값입니다.
BirthDateKind = Literal["birthday", "family_day"]
PetSex = Literal["male", "female"]

#: 급식 방식. 모델 쪽 `PET_FEEDING_STYLES` 와 같은 값입니다.
#:   free       자율급식 — 그릇에 늘 두고 알아서 먹는다
#:   scheduled  시간제 — 정해진 때에 준다 (`feeding_times`)
FeedingStyle = Literal["free", "scheduled"]

#: 급식 시각 한 칸의 모양. **화면이 고른 시각**이 오는 자리라 `HH:MM` 만 받습니다 —
#: "아침" 같은 말은 나중에 알림(roadmap F5)이 시각으로 못 바꿉니다.
_FEEDING_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: 지병·약 자유 텍스트의 상한. 비서 프롬프트에 실리는 값이라 무한정 받지 않습니다.
CARE_TEXT_MAX = 200


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

    # ── 돌봄 (#331) ────────────────────────────────────────────────────
    # 넷 다 **None 이 '모름'** 입니다. 안 물어본 것과 "없다"고 답한 것이 다릅니다 —
    # 약 칸이 비어 있다고 "약 안 먹는 아이"가 아니고, 비서도 그렇게 읽지 않습니다.

    #: 자율급식인지 시간제인지. 비서가 "밥 몇 번 줘요?" 류에 이 아이 기준으로 답합니다.
    feeding_style: FeedingStyle | None = None

    #: 시간제일 때의 급식 시각 목록(`HH:MM`). **시간제가 아니면 못 옵니다** — 자율급식에
    #: 시각이 붙으면 어느 쪽이 맞는지 알 수 없는 행이 됩니다. 시간제인데 시각을 모르면 None.
    feeding_times: list[str] | None = Field(default=None, min_length=1, max_length=12)

    #: 앓는 병. 자유 텍스트 — 비서 프롬프트에 그대로 실립니다.
    health_conditions: str | None = Field(default=None, max_length=CARE_TEXT_MAX)

    #: 정기적으로 먹는 약. 자유 텍스트. **비서 프롬프트에는 이름이 아니라 "복약 중" 여부만
    #: 갑니다** (`services/dog_context.py`) — 약 이름을 주면 약·용량 질문을 거절하는 방어가
    #: 지시문 한 줄로 약해집니다. 이름의 소비자는 케어 기록과 알림입니다.
    medications: str | None = Field(default=None, max_length=CARE_TEXT_MAX)

    @field_validator("health_conditions", "medications")
    @classmethod
    def _blank_care_text_is_unknown(cls, value: str | None) -> str | None:
        """공백만 있는 값은 **모름(None)** 입니다. 빈 문자열이 저장되면 "적었는데 내용이
        없는" 행이 되고, 비서가 그걸 "복약 중"으로 읽습니다."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("feeding_times")
    @classmethod
    def _feeding_times_shape(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        for item in value:
            if not _FEEDING_TIME.match(item):
                raise ValueError(f"급식 시각은 HH:MM 이어야 합니다: {item!r}")
        return value

    @model_validator(mode="after")
    def _feeding_times_need_schedule(self) -> Self:
        """급식 시각은 **시간제일 때만** 있을 수 있습니다.

        DB 에도 같은 CHECK 가 있지만 여기서 막아야 422 로 이유를 말해 줄 수 있습니다 —
        DB 까지 가면 500 입니다 (`_birth_date_pair` 와 같은 이유).
        """
        if self.feeding_times is not None and self.feeding_style != "scheduled":
            raise ValueError("급식 시각은 feeding_style 이 scheduled 일 때만 보낼 수 있습니다.")
        return self

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
    updated_at: datetime | None = None
    id: uuid.UUID
    name: str
    breed: str
    sex: PetSex | None
    neutered: bool | None
    weight_kg: Decimal | None
    birth_date: date | None
    birth_date_kind: BirthDateKind | None
    farewell_on: date | None

    #: 돌봄 (#331). 넷 다 None 이 '모름'입니다 — `PetUpsert` 의 같은 칸 주석 참고.
    feeding_style: FeedingStyle | None = None
    feeding_times: list[str] | None = None
    health_conditions: str | None = None
    medications: str | None = None

    #: 이 아이가 대표인가. `app_users.primary_pet_id` 에서 옵니다 —
    #: pets 테이블에는 그런 칸이 없습니다 (05_pets.sql 주석 참고).
    is_primary: bool

    #: **내가 이 아이의 대표인가** (docs/co-care.md §2). 목록에 돌보미로 참여 중인
    #: 아이가 같이 오므로, 앱이 수정·배웅·삭제·사진 버튼을 이 값으로 가립니다 —
    #: false 인 아이에 그 버튼을 그리면 서버가 404 로 답합니다.
    #:
    #: `is_primary` 와 다른 값입니다: 저건 "내 대표 강아지인가"(미니룸의 주인공),
    #: 이건 "내가 이 아이의 보호자 대표인가"(권한)입니다. 기본값을 True 로 둔 것은
    #: 공동 돌봄 이전의 응답이 전부 내 강아지였기 때문입니다.
    is_owner: bool = True

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
    """**내가 돌보는 아이 전부.** 등록 순서대로입니다.

    앱이 그 순서로 카드를 늘어놓고, 대표를 지웠을 때 승계되는 아이도 이 순서의
    첫 번째입니다.

    돌보미로 참여 중인 아이도 여기 섞여 옵니다 (docs/co-care.md §2) — `is_owner` 로
    갈립니다. `max_pets` 도 그래서 **구성원 기준**의 상한입니다.
    """

    pets: list[PetResponse]

    #: 한 계정에 등록할 수 있는 마릿수. 앱이 `+` 버튼을 언제 감출지 정하는 데 씁니다.
    #: **서버가 알려 줍니다** — 앱에 숫자를 박아 두면 서버와 갈라집니다.
    max_pets: int


class PrimaryPetRequest(BaseModel):
    """대표로 세울 강아지. 내 강아지가 아니면 404 입니다."""

    pet_id: uuid.UUID
