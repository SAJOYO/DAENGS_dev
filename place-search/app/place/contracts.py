"""Place-first 외부 계약의 값 객체.

`PlaceRef`는 원천 레코드의 안정 키이지 물리 장소 통합 ID가 아니다. 현재 name+150m 링크는
오탐이 있으므로 `aliases`에는 검증된 것만 들어갈 수 있고, adapter는 링크를 자동 승격하지
않는다. 원천 레코드 하나의 kind는 scalar지만 통합 Place는 복수 classification을 가질 수 있다.
대표 identity와 이번 검색에서 후보가 된 분류도 서로 다른 개념으로 다룬다.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.geo.icons import IconGroup, icon_group

# **데려갈 개**의 크기 등급. 시설이 받아주는 축(`geo.pet.SizeClass` — `any` 포함)과 다른
# 개념이라 이름을 가른다. 값 집합은 프로필 계약(docs/contracts/dog-profile.md)과 같지만
# place 는 프로필을 소유하지 않으므로 타입도 여기서 소유한다 — 검색은 identity 가 아니라
# 값을 받는다.
DogSize = Literal["small", "medium", "large"]


class PlaceRef(BaseModel):
    """원천 레코드 키 `(source, ref)`. DB 내부 PK와 물리 장소 ID가 아니다."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class PlaceClassification(BaseModel):
    """어느 원천 category를 어떤 버전의 규칙으로 canonical kind에 옮겼는가."""

    source: PlaceRef
    source_category: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    mapping_version: str = Field(min_length=1)
    as_of: str | None = None


class PlaceMatch(BaseModel):
    """이번 검색에서 이 Place가 후보가 된 분류. 대표 `key`와 독립적이다."""

    source: PlaceRef
    kind: str = Field(min_length=1)


class FieldProvenance(BaseModel):
    """대표 레코드가 아닌 다른 원천에서 빌린 필드의 출처."""

    source: PlaceRef
    as_of: str | None = None


class PetAccessFacts(BaseModel):
    """시설 원문의 반려동물 봉투와 결정론적으로 파생한 축. None은 미상이다."""

    raw: dict = Field(default_factory=dict)
    allowed: bool | None = None
    exclusive: bool | None = None
    dog_ok: bool | None = None
    size_class: str | None = None
    max_kg: float | None = None


class MedicalFacts(BaseModel):
    """의료 원천에만 있는 사실. 이름에서 만든 태그·영업 형태 추론은 넣지 않는다."""

    active: bool
    license_status_code: str | None = None
    license_status_name: str | None = None
    open_now: bool | None = None
    hours_today: list[tuple[str, str]] | None = None
    area_m2: float | None = None
    staff_count: int | None = None


class RestrictionChip(BaseModel):
    """동반 조건 하나 — 코드는 기계가, 라벨은 사람이 읽는다.

    라벨을 서버가 소유하는 이유는 웹과 Android 가 같은 의미를 봐야 하기 때문이다
    (결정 #65 §6). 클라이언트가 각자 번역하면 두 표면의 문구가 갈라진다.

    `applies_to` 가 `all` 이 아니면 **모두에게 걸리는 조건이 아니다.** 개 조건이 없는
    요청에서는 `label` 에 한정어가 붙고(`입마개·대형견`), 개가 지정되면 소비자가
    해당 없는 칩을 걸러낸다.
    """

    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    applies_to: str = "all"
    # 원문이 밝힌 수치·조건. `최대 2마리` 와 `최대 5마리` 를 가르는 유일한 자리다.
    params: dict[str, str] = Field(default_factory=dict)
    # `soft` 는 원문이 단정하지 않았다는 뜻이다 — "어려울 수 있음"·"신규예약 불가".
    # 칩으로 보여주되 판정에 쓰지 않는다.
    certainty: str = "firm"


class RestrictionFacts(BaseModel):
    """`pet.restrictions` 문장에서 파생한 동반 조건 (결정 #70).

    **칩이 0개인 이유가 셋이고 사용자가 할 행동이 다르다.** 그래서 `state` 를 함께 낸다:

        unknown         원문 자체가 없다 → 전화로 확인해야 한다
        none_confirmed  원천이 "제한 없음" 이라고 말했다 → 확인된 사실이다
        not_applicable  원천이 "해당없음" 이라고 말했다 → 입장 가능의 근거가 아니다
        restricted      제한이 있다 → 칩 또는 `raw` 를 보면 된다

    `parse_state` 가 `partial`·`raw_only` 면 `raw` 가 함께 온다. 칩 목록만 보이면
    완결로 읽히는데, 그건 조용한 truncation 과 같은 거짓말이다.
    """

    state: str = "unknown"
    parse_state: str | None = None
    chips: list[RestrictionChip] = Field(default_factory=list)
    raw: str | None = None


class PlaceFacts(BaseModel):
    """종류를 가로질러 전달하는 사실. 데이터가 없으면 false가 아니라 None이다."""

    address: str | None = None
    phone: str | None = None
    homepage: str | None = None
    hours_text: str | None = None
    closed_days: str | None = None
    parking: bool | None = None
    indoor: bool | None = None
    outdoor: bool | None = None
    pet_access: PetAccessFacts | None = None
    restrictions: RestrictionFacts | None = None
    medical: MedicalFacts | None = None


class PlaceResult(BaseModel):
    """resolver 종류와 무관하게 웹·Android가 받을 장소 결과 한 건."""

    key: PlaceRef
    # verified-only. 현재 facility_link를 자동으로 넣지 않는다.
    aliases: list[PlaceRef] = Field(default_factory=list)
    name: str
    lat: float
    lng: float
    distance_m: int = Field(ge=0)
    match: PlaceMatch
    classifications: list[PlaceClassification] = Field(min_length=1)
    facts: PlaceFacts
    # `facts.hours_text`처럼 계약 경로를 키로 쓴다. 대표 원천의 값은 key/classification으로
    # 설명되므로, 이 맵에는 다른 레코드에서 빌린 값만 들어간다.
    field_sources: dict[str, FieldProvenance] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def icon_group(self) -> IconGroup:
        return icon_group(self.match.kind)

    @model_validator(mode="after")
    def identity_and_match_are_consistent(self) -> Self:
        if self.key in self.aliases:
            raise ValueError("key cannot also be an alias")
        alias_keys = {(alias.source, alias.ref) for alias in self.aliases}
        if len(alias_keys) != len(self.aliases):
            raise ValueError("aliases must be unique")
        classified_keys = {
            (item.source.source, item.source.ref) for item in self.classifications
        }
        if len(classified_keys) != len(self.classifications):
            raise ValueError("each source record must have exactly one classification")
        key = (self.key.source, self.key.ref)
        if key not in classified_keys:
            raise ValueError("key must have classification provenance")
        if not alias_keys <= classified_keys:
            raise ValueError("every alias must have classification provenance")
        if not any(
            item.source == self.match.source and item.kind == self.match.kind
            for item in self.classifications
        ):
            raise ValueError("match must reference one of the Place classifications")
        return self
