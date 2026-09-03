"""Place 내부 발견 요청과 결과 계약.

공통 오케스트레이터의 capability 타입이나 HTTP DTO를 복제하지 않는다. 호출자는 검증된
위치·반려견 값만 넘기고, 실행량은 이 모듈의 서버 상한 안에서 정한 정책으로 제한한다.
"""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from daengs_place.place.information_needs import InformationNeedId
from daengs_place.place.intent.contract import ProposalDisposition
from daengs_place.place.intent.lenses import SearchLensOutcome
from daengs_place.place.intent.suggestions import SuggestionResolution
from daengs_place.place.planning.contract import (
    PlaceSearchConditions,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.planning.intents import PlannerIssue, PlannerStatus
from daengs_place.place.presentation.contract import PlacePresentation
from daengs_place.place.search import PlaceSearchResponse

MAX_DISCOVERY_LENSES = 3
MAX_CANDIDATES_PER_LENS = 10
MAX_DISCOVERY_CANDIDATES = 20
MAX_DISCOVERY_SERIALIZED_BYTES = 256 * 1024


class PlaceDiscoveryResultPolicy(PlanningModel):
    """클라이언트가 넘을 수 없는 Place discovery 실행·직렬화 상한."""

    max_executable_lenses: int = Field(3, ge=1, le=MAX_DISCOVERY_LENSES)
    max_candidates_per_lens: int = Field(5, ge=1, le=MAX_CANDIDATES_PER_LENS)
    max_total_candidates: int = Field(15, ge=1, le=MAX_DISCOVERY_CANDIDATES)
    max_serialized_bytes: int = Field(
        128 * 1024,
        ge=16 * 1024,
        le=MAX_DISCOVERY_SERIALIZED_BYTES,
    )

    @model_validator(mode="after")
    def every_executable_lens_can_receive_a_candidate(self) -> Self:
        if self.max_total_candidates < self.max_executable_lenses:
            raise ValueError("total candidate budget must cover every executable lens")
        return self


class PlaceDiscoveryRequest(PlanningModel):
    """Place 경계가 받는 최소 입력. identity와 provider 원출력은 받지 않는다."""

    query: str = Field(min_length=1, max_length=1_000)
    spatial: PlaceSpatialConstraint
    conditions: PlaceSearchConditions | None = None
    result_policy: PlaceDiscoveryResultPolicy = Field(default_factory=PlaceDiscoveryResultPolicy)

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class PlaceDiscoveryPlanningData(PlanningModel):
    """provider 내부 자료를 제거한 Place 소유 planning projection."""

    contract_version: Literal["place-discovery-planning-v1"] = "place-discovery-planning-v1"
    status: PlannerStatus
    source_disposition: ProposalDisposition | None
    resolution: SuggestionResolution | None = None
    lenses: SearchLensOutcome = Field(
        default_factory=lambda: SearchLensOutcome(target_lenses=(), signal_lenses=())
    )
    issues: tuple[PlannerIssue, ...] = ()
    rejected_candidate_count: int = Field(ge=0)

    @model_validator(mode="after")
    def status_matches_public_shape(self) -> Self:
        if self.status is PlannerStatus.READY:
            if (
                self.resolution is None
                or self.source_disposition is None
                or not self.lenses.target_lenses
            ):
                raise ValueError(
                    "ready discovery planning data requires a resolution, disposition, and target"
                )
        elif self.resolution is not None:
            raise ValueError("non-ready discovery planning data cannot carry a resolution")

        if self.source_disposition is None:
            valid_invalid_output = (
                self.status is PlannerStatus.NEEDS_CLARIFICATION
                and not self.lenses.target_lenses
                and not self.lenses.signal_lenses
                and self.rejected_candidate_count == 0
                and len(self.issues) == 1
                and self.issues[0].code == "intent_proposer_invalid_output"
            )
            if not valid_invalid_output:
                raise ValueError(
                    "missing source disposition requires an empty invalid-output planning result"
                )

        issue_keys = [
            (issue.observation_ids, issue.code, issue.detail, issue.blocking)
            for issue in self.issues
        ]
        if len(issue_keys) != len(set(issue_keys)):
            raise ValueError("public planning issues must be unique")
        return self


class PlaceDiscoveryNotice(PlanningModel):
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    message: str = Field(min_length=1, max_length=500)
    lens_id: str | None = Field(None, min_length=1, max_length=160)


class PlaceDiscoveryLensResult(PlanningModel):
    """한 executable lens의 제한된 검색 결과와 같은 순서의 표시 모델."""

    lens_id: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=80)
    support_note: str = Field(min_length=1, max_length=300)
    information_needs: tuple[InformationNeedId, ...] = ()
    candidate_limit: int = Field(ge=1, le=MAX_CANDIDATES_PER_LENS)
    search: PlaceSearchResponse
    presentations: tuple[PlacePresentation, ...] = ()

    @model_validator(mode="after")
    def presentations_match_search_hits(self) -> Self:
        hit_keys = [hit.place.key for group in self.search.groups for hit in group.results]
        presentation_keys = [item.place_key for item in self.presentations]
        if presentation_keys != hit_keys:
            raise ValueError("presentations must match search hits in group order")
        if len(presentation_keys) > self.candidate_limit:
            raise ValueError("lens results exceed their candidate limit")
        return self


class PlaceDiscoveryData(PlanningModel):
    """내부 후속 projection이 소비할 수 있는 완결된 Place 발견 데이터."""

    contract_version: Literal["place-discovery-v1"] = "place-discovery-v1"
    planning: PlaceDiscoveryPlanningData
    result_policy: PlaceDiscoveryResultPolicy
    lens_results: tuple[PlaceDiscoveryLensResult, ...] = ()
    notices: tuple[PlaceDiscoveryNotice, ...] = ()

    @model_validator(mode="after")
    def results_follow_planning_and_policy(self) -> Self:
        expected = [
            item.lens_id
            for item in self.planning.lenses.executable_targets[
                : self.result_policy.max_executable_lenses
            ]
        ]
        actual = [item.lens_id for item in self.lens_results]
        if actual != expected:
            raise ValueError("lens results must cover the selected executable lenses in order")
        candidate_count = sum(len(item.presentations) for item in self.lens_results)
        if candidate_count > self.result_policy.max_total_candidates:
            raise ValueError("discovery results exceed the total candidate budget")
        notice_keys = [(item.code, item.lens_id) for item in self.notices]
        if len(notice_keys) != len(set(notice_keys)):
            raise ValueError("discovery notices must be unique per lens")
        return self

    @property
    def serialized_size_bytes(self) -> int:
        return len(self.model_dump_json().encode("utf-8"))
