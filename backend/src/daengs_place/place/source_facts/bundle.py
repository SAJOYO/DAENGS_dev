"""원천 레코드별 projection을 후보 단위로 묶되 충돌을 합의값으로 덮지 않는다."""

import hashlib
import json
from collections import defaultdict
from typing import Any, Literal, Self

from pydantic import Field, computed_field, model_validator

from daengs_place.place.source_facts.contract import InternalModel, SourceFactProjection
from daengs_place.place.source_facts.states import DetailAcquisitionState, ProjectionState

SourceFactSource = Literal["kcisa", "kto"]
FactSection = Literal[
    "purpose",
    "pet_access",
    "restrictions",
    "amenities",
    "pet_fee",
    "operations",
]
BundleAvailability = Literal["missing", "present"]
FACT_SECTIONS: tuple[FactSection, ...] = (
    "purpose",
    "pet_access",
    "restrictions",
    "amenities",
    "pet_fee",
    "operations",
)


class SourceFactKey(InternalModel):
    """검색 후보의 원천 identity. 물리 Place 통합 ID가 아니다."""

    source: SourceFactSource
    source_ref: str = Field(min_length=1)


class SourceFactVariant(InternalModel):
    """shadow 원문 하나의 projection과 획득 provenance."""

    source_ref: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    occurrence_count: int = Field(ge=1)
    snapshot: str = Field(min_length=1)
    detail_state: DetailAcquisitionState
    projection: SourceFactProjection


class FactValueGroup(InternalModel):
    """같은 section 값으로 투영된 원천 record 묶음."""

    fingerprint: str = Field(min_length=16)
    record_refs: tuple[str, ...] = Field(min_length=1)


class FactConflict(InternalModel):
    """후보 안에서 canonical section 값이 둘 이상임을 알린다."""

    section: FactSection
    groups: tuple[FactValueGroup, ...] = Field(min_length=2)


class CandidateFactBundle(InternalModel):
    """검색 후보 하나에 연결된 모든 source fact variant.

    `variants[0]`을 대표값으로 고르지 않는다. 소비자는 `conflicts`와 각 projection을 함께
    읽어야 하며, 이 PR은 resolver 정책을 발명하지 않는다.
    """

    key: SourceFactKey
    variants: tuple[SourceFactVariant, ...] = ()

    @model_validator(mode="after")
    def variants_match_candidate(self) -> Self:
        record_refs = [variant.record_ref for variant in self.variants]
        if len(set(record_refs)) != len(record_refs):
            raise ValueError("bundle record_refs must be unique")
        if any(variant.projection.source != self.key.source for variant in self.variants):
            raise ValueError("bundle projection sources must match the candidate key")
        if any(variant.source_ref != self.key.source_ref for variant in self.variants):
            raise ValueError("bundle variant source_refs must match the candidate key")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def conflicts(self) -> tuple[FactConflict, ...]:
        """variant 값에서 항상 다시 계산해 수동으로 모순된 충돌 상태를 만들 수 없게 한다."""

        return _conflicts(self.variants)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def availability(self) -> BundleAvailability:
        return "present" if self.variants else "missing"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def projection_state(self) -> ProjectionState | None:
        """parser 성공도만 요약한다. detail 획득 상태나 conflict와 합치지 않는다."""

        if not self.variants:
            return None
        states = {variant.projection.state for variant in self.variants}
        if states == {ProjectionState.COMPLETE}:
            return ProjectionState.COMPLETE
        if states == {ProjectionState.FAILED}:
            return ProjectionState.FAILED
        return ProjectionState.PARTIAL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def acquisition_states(self) -> tuple[DetailAcquisitionState, ...]:
        return tuple(
            sorted(
                {variant.detail_state for variant in self.variants},
                key=lambda state: state.value,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def physical_occurrences(self) -> int:
        return sum(variant.occurrence_count for variant in self.variants)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _conflicts(variants: tuple[SourceFactVariant, ...]) -> tuple[FactConflict, ...]:
    successful = tuple(
        variant for variant in variants if variant.projection.state is not ProjectionState.FAILED
    )
    result = []
    for section in FACT_SECTIONS:
        groups: dict[str, list[str]] = defaultdict(list)
        for variant in successful:
            fingerprint = _fingerprint(getattr(variant.projection, section))
            groups[fingerprint].append(variant.record_ref)
        if len(groups) > 1:
            result.append(
                FactConflict(
                    section=section,
                    groups=tuple(
                        FactValueGroup(
                            fingerprint=fingerprint,
                            record_refs=tuple(sorted(record_refs)),
                        )
                        for fingerprint, record_refs in sorted(groups.items())
                    ),
                )
            )
    return tuple(result)


def build_candidate_fact_bundle(
    key: SourceFactKey,
    variants: list[SourceFactVariant],
) -> CandidateFactBundle:
    """projection variant를 정렬하고 section 충돌만 표시한다. 대표값은 고르지 않는다."""

    ordered = tuple(sorted(variants, key=lambda item: item.record_ref))
    return CandidateFactBundle(
        key=key,
        variants=ordered,
    )
