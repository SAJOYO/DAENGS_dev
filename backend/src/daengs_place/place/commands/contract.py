"""Action arguments describe changes, never an inferred intent or answer template."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.conversation.intent import Alternative, KindEdit, SemanticChanges
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.service import FilterResponse
from daengs_place.place.planning.contract import PlanningModel


class SearchChanges(PlanningModel):
    category: KindEdit | None = Field(
        None, description="업종 set=교체, add=추가, remove=제거; 생략은 유지"
    )
    parking: Literal["required", "forbidden", "preferred", "clear"] | None = Field(
        None, description="주차 필수/불가 필수/가능 우선/조건 해제; 생략은 유지"
    )
    exclusive: Literal["required", "forbidden", "clear"] | None = Field(
        None, description="반려동물 전용 조건; 동반 가능과 다름; 생략은 유지"
    )
    radius_m: StrictInt | None = Field(
        None, ge=100, le=20000, description="검색 중심 기준 반경(m); 생략은 유지"
    )
    name_query: str | None = Field(
        None, max_length=100, description="상호명 포함 검색; 빈 문자열은 해제"
    )
    alternatives: tuple[Alternative, ...] | None = Field(
        None, max_length=4, description="또는/거나 조건 분기 전체 교체; []는 해제; 생략은 유지"
    )

    def semantic(self):
        modes = {
            None: "keep",
            "required": "required_true",
            "forbidden": "required_false",
            "preferred": "preferred_true",
            "clear": "clear",
        }
        return SemanticChanges(
            kinds=self.category,
            parking=modes[self.parking],
            exclusive=modes[self.exclusive],
            radius_m=self.radius_m,
            name_query=self.name_query,
            alternatives=self.alternatives,
        )


class SearchPlaces(SearchChanges):
    """장소를 찾아달라거나 조건을 바꿔달라는 요청에 사용한다. 현재 목록에 있어도 검색한다.
    명시한 항목만 변경한다. 미지원 조건은 unavailable에 넣어 적용 전 확인 제안을 표시한다.
    needs_confirmation은 검색 미실행이며, 지원 가능한 조건만 적용해도 될지 사용자에게 묻는다.
    """

    apply_to: Literal["results", "filters_only"] = Field(
        "results",
        description="results=검색까지 실행, filters_only=사용자가 조건만 편집하라고 요청한 경우",
    )
    unavailable: tuple[str, ...] = Field(
        default=(),
        max_length=4,
        description="조용함·무료·동반 가능 등 도구가 지원하지 않는 요청 조건",
    )


class NextPlaces(PlanningModel):
    """현재 조건을 유지하고 아직 보여주지 않은 다른 후보를 검색한다."""


class PlaceDetails(PlanningModel):
    """특정 장소의 속성을 묻는 질문에 사용한다. 조건·목록·선택은 바꾸지 않는다.
    조건에 맞는 장소를 찾아달라는 요청은 search_places를 사용한다.
    """

    place_refs: tuple[str, ...] = Field(
        min_length=1, max_length=6, description="화면에 주어진 장소 참조"
    )
    attributes: tuple[
        Literal[
            "parking", "exclusive", "pet_allowed", "address", "distance", "hours", "quiet", "free"
        ],
        ...,
    ] = Field(min_length=1, max_length=8, description="질문한 속성; 값이 없으면 미상으로 반환")


class SelectPlace(PlanningModel):
    """현재 화면의 장소 하나를 선택한다. 검색 조건을 바꾸지 않는다."""

    place_ref: str = Field(min_length=1, max_length=100, description="선택할 화면 장소 참조")


class SetExcluded(PlanningModel):
    """명시한 장소를 검색 후보에서 제외하거나 제외를 해제한다. 찜에는 영향이 없다."""

    place_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    excluded: StrictBool


class MarkKnown(PlanningModel):
    """이미 아는 장소로 기록한다. 제외·찜 해제·영구 비선호로 처리하지 않는다."""

    place_refs: tuple[str, ...] = Field(min_length=1, max_length=20)


class ProposeSearch(SearchChanges):
    """조건 완화·확대 등 대안을 제안한다. 사용자 확인 전 검색 상태는 바꾸지 않는다."""


class ResolveProposal(PlanningModel):
    """화면에 표시된 현재 검색 제안에 대한 사용자의 명확한 동의 또는 취소를 처리한다."""

    accept: StrictBool


ARGUMENTS = {
    "search_places": SearchPlaces,
    "next_places": NextPlaces,
    "get_place_details": PlaceDetails,
    "select_place": SelectPlace,
    "set_place_excluded": SetExcluded,
    "mark_places_known": MarkKnown,
    "propose_search_change": ProposeSearch,
    "resolve_search_proposal": ResolveProposal,
}


class Proposal(PlanningModel):
    id: UUID
    revision: int
    expires_at: datetime
    candidate: FilterState
    unavailable: tuple[str, ...] = ()
    apply_to: Literal["results", "filters_only"] = "results"


class NamedReference(PlanningModel):
    ref: str
    key: PlaceRef
    name: str


class FacilityState(PlanningModel):
    filters: FilterState
    revision: int = Field(default=0, ge=0)
    snapshot_id: UUID | None = None
    result: FilterResponse | None = None
    selected: PlaceRef | None = None
    presented: tuple[PlaceRef, ...] = Field(default=(), max_length=1200)
    excluded: tuple[NamedReference, ...] = Field(default=(), max_length=120)
    known: tuple[NamedReference, ...] = Field(default=(), max_length=120)
    proposal: Proposal | None = None

    @model_validator(mode="after")
    def snapshot_pair(self):
        if (self.snapshot_id is None) != (self.result is None):
            raise ValueError("snapshot and results must be paired")
        return self


class CommandResult(PlanningModel):
    status: Literal[
        "applied", "unchanged", "empty", "needs_confirmation", "unsupported", "conflict", "failed"
    ]
    state: FacilityState
    changes: dict = Field(default_factory=dict)
    data: dict = Field(default_factory=dict)
    code: str = ""
