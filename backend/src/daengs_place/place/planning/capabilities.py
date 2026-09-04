"""AI와 UI가 볼 수 있는 정적 검색 손잡이. 원천 필드 catalog가 아니다."""

from typing import Literal

from pydantic import Field

from daengs_place.place.planning.contract import (
    CapabilityId,
    GateMode,
    GateOperator,
    GateOrigin,
    PlanningModel,
    UnknownPolicy,
)
from daengs_place.place.source_catalog import MOIS_SOURCES

CapabilityValueType = Literal["place_kind_set", "boolean"]
ExecutionStage = Literal["candidate", "ranking"]
ProjectionAuthority = Literal["source_projection"]
ExecutionAuthority = Literal[
    "canonical_classification",
    "effective_fact_with_provenance",
]


class OriginModeAllowance(PlanningModel):
    origin: GateOrigin
    modes: tuple[GateMode, ...] = Field(min_length=1)


class SearchCapabilitySpec(PlanningModel):
    capability_id: CapabilityId
    description: str = Field(min_length=1)
    value_type: CapabilityValueType
    operators: tuple[GateOperator, ...] = Field(min_length=1)
    origin_modes: tuple[OriginModeAllowance, ...] = Field(min_length=1)
    unknown_policies: tuple[UnknownPolicy, ...] = Field(min_length=1)
    default_unknown_policy: UnknownPolicy
    execution_stage: ExecutionStage
    executor_id: str = Field(min_length=1)
    projection_paths: tuple[str, ...] = ()
    execution_paths: tuple[str, ...] = Field(min_length=1)
    projection_sources: tuple[str, ...] = Field(min_length=1)
    execution_sources: tuple[str, ...] = Field(min_length=1)
    projection_authority: ProjectionAuthority = "source_projection"
    execution_authority: ExecutionAuthority
    allowed_boolean_values: tuple[bool, ...] = ()

    def modes_for(self, origin: GateOrigin) -> tuple[GateMode, ...]:
        return next(
            (allowance.modes for allowance in self.origin_modes if allowance.origin is origin),
            (),
        )


CAPABILITIES: tuple[SearchCapabilitySpec, ...] = (
    SearchCapabilitySpec(
        capability_id=CapabilityId.PURPOSE_KIND,
        description="canonical 장소 종류로 독립 후보군을 생성한다",
        value_type="place_kind_set",
        operators=(GateOperator.IN,),
        origin_modes=(
            OriginModeAllowance(
                origin=GateOrigin.USER_EXPLICIT,
                modes=(GateMode.OFF, GateMode.FILTER),
            ),
            OriginModeAllowance(
                origin=GateOrigin.INFERRED,
                modes=(GateMode.OFF, GateMode.FILTER),
            ),
            OriginModeAllowance(
                origin=GateOrigin.SYSTEM,
                modes=(GateMode.OFF, GateMode.FILTER),
            ),
        ),
        unknown_policies=(UnknownPolicy.EXCLUDE,),
        default_unknown_policy=UnknownPolicy.EXCLUDE,
        execution_stage="candidate",
        executor_id="purpose_kind_selector",
        projection_paths=("purpose.primary",),
        execution_paths=("match.kind",),
        projection_sources=("kcisa", "kto"),
        execution_sources=(
            "kcisa",
            "kto",
            *(source.source for source in MOIS_SOURCES.values()),
        ),
        execution_authority="canonical_classification",
    ),
    SearchCapabilitySpec(
        capability_id=CapabilityId.OPERATIONS_PARKING,
        description="주차 가능 사실을 같은 거리 band 안의 선호 순위에 반영한다",
        value_type="boolean",
        operators=(GateOperator.EQ,),
        origin_modes=(
            OriginModeAllowance(
                origin=GateOrigin.USER_PREFERENCE,
                modes=(GateMode.OFF, GateMode.PREFER),
            ),
            OriginModeAllowance(
                origin=GateOrigin.CONTEXT,
                modes=(GateMode.OFF, GateMode.PREFER),
            ),
            OriginModeAllowance(
                origin=GateOrigin.INFERRED,
                modes=(GateMode.OFF, GateMode.PREFER),
            ),
        ),
        unknown_policies=(UnknownPolicy.KEEP,),
        default_unknown_policy=UnknownPolicy.KEEP,
        execution_stage="ranking",
        executor_id="parking_preference_ranker",
        projection_paths=("operations.parking",),
        execution_paths=("facts.parking",),
        projection_sources=("kcisa",),
        execution_sources=("kcisa", "kto"),
        execution_authority="effective_fact_with_provenance",
        allowed_boolean_values=(True,),
    ),
)

_BY_ID = {spec.capability_id: spec for spec in CAPABILITIES}
if len(_BY_ID) != len(CAPABILITIES):
    raise RuntimeError("search capability ids must be unique")


def capability_spec(capability_id: CapabilityId) -> SearchCapabilitySpec:
    return _BY_ID[capability_id]
