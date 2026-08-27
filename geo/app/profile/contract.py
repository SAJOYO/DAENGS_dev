"""반려견 프로필 — 외부 계약. docs/contracts/dog-profile.md

이 레포는 프로필을 소유하지 않는다. 이 형태로 받는다고 가정하고 소비만 한다.
"""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

SizeClass = Literal["small", "medium", "large"]
Activity = Literal["low", "mid", "high"]

BRACHY_BREEDS = {"pug", "bulldog", "french bulldog", "boston terrier", "shih tzu", "pekingese", "boxer"}


class BreedMix(BaseModel):
    breed: str
    ratio: float = 1.0


class DogProfile(BaseModel):
    dog_id: str
    name: str
    breed: list[BreedMix]
    birth_date: date
    sex: Literal["M", "F"]
    neutered: bool
    weight_kg: float
    size_class: SizeClass
    profile_version: int = 1

    brachycephalic: bool | None = None
    health_flags: list[str] = Field(default_factory=list)  # joint, heart, obesity, senior ...
    activity_level: Activity | None = None
    temperament: list[str] = Field(default_factory=list)
    has_car: bool | None = None   # DEPRECATED — 견주 속성이다. OwnerProfile.has_car 를 쓸 것

    @computed_field  # type: ignore[misc]
    @property
    def age_years(self) -> float:
        today = datetime.now(UTC).date()
        return round((today - self.birth_date).days / 365.25, 1)

    @computed_field  # type: ignore[misc]
    @property
    def is_brachy(self) -> bool:
        if self.brachycephalic is not None:
            return self.brachycephalic
        return any(b.breed.lower() in BRACHY_BREEDS for b in self.breed)

    @computed_field  # type: ignore[misc]
    @property
    def is_senior(self) -> bool:
        return "senior" in self.health_flags or self.age_years >= 10

    @computed_field  # type: ignore[misc]
    @property
    def has_joint_issue(self) -> bool:
        return "joint" in self.health_flags


# --------------------------------------------------------------------------- 견주
VetLiteracy = Literal["none", "basic", "experienced"]


class OwnerProfile(BaseModel):
    """견주 프로필 — 외부 계약. docs/contracts/owner-profile.md

    개가 아니라 **사람**이 정하는 제약만 넣는다. 필드 하나하나가 바꾸는 판정이 있어야 한다:

      has_car       이동수단 선택지. 없으면 응급에 택시·도보뿐              → journey mode 기본값
      can_carry_kg  안고 오를 수 있는 무게. 계단 판정은 재료가 없어 #66 으로 걷어냈고,
                    지금 이 값을 읽는 코드는 없다 — 계약 필드로만 남는다
      transit_ok    대중교통 동반 의사. 개 크기만으로 정할 수 없다           → journey.show_transit
      vet_literacy  병원의 역할(1차/2차·응급·과목)을 아는 정도              → reply 상세도, ask 발동

    '비용 민감도'는 의도적으로 없다 — 우리에게 진료비 데이터가 없다 (owner-profile.md 참조).
    """

    owner_id: str
    name: str
    dog_ids: list[str] = Field(default_factory=list)

    has_car: bool | None = None
    can_carry_kg: float | None = None      # None = 미상. 0 = 못 안음
    transit_ok: bool | None = None
    vet_literacy: VetLiteracy | None = None

    def can_carry(self, dog: DogProfile) -> bool | None:
        """이 개를 안고 이동할 수 있나. None = 판단 재료 없음 (모름 != 불가)."""
        if self.can_carry_kg is None:
            return None
        return dog.weight_kg <= self.can_carry_kg
