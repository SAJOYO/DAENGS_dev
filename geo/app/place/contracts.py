"""Place-first 외부 계약의 값 객체.

`PlaceRef`는 원천 레코드의 안정 키이지 물리 장소 통합 ID가 아니다. 현재 name+150m 링크는
오탐이 있으므로 `aliases`에는 검증된 것만 들어갈 수 있고, adapter는 링크를 자동 승격하지
않는다. 원천 레코드 하나의 kind는 scalar지만 통합 Place는 복수 classification을 가질 수 있다.
대표 identity와 이번 검색에서 후보가 된 분류도 서로 다른 개념으로 다룬다.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.geo.icons import IconGroup, icon_group


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
