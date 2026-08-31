"""강아지 프로필 API 의 요청 / 응답 형태.

**모르는 것은 `None` 으로 받습니다.** 성별·중성화·생일이 그렇습니다. 필수로 하면
모르는 사람이 아무 값이나 넣고, 그러면 그 값은 데이터로 못 씁니다. `None` 을
`false` 나 기본값으로 바꾸지 마세요 — 안 물어본 것과 아니라고 답한 것은 다릅니다.
"""

import uuid
from datetime import date
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

    #: 이 아이가 대표인가. `app_users.primary_pet_id` 에서 옵니다 —
    #: pets 테이블에는 그런 칸이 없습니다 (05_pets.sql 주석 참고).
    is_primary: bool


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
