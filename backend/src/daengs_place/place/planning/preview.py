"""실행 값과 shadow source evidence를 섞지 않는 순수 plan preview."""

from typing import Literal, Self

from pydantic import Field, model_validator

from daengs_place.place.contracts import PlaceResult
from daengs_place.place.planning.capabilities import capability_spec
from daengs_place.place.planning.contract import (
    CapabilityId,
    GateMode,
    PlaceKind,
    PlaceSearchPlan,
    PlanningModel,
    SearchGate,
    UnknownPolicy,
)
from daengs_place.place.planning.guard import guard_search_plan
from daengs_place.place.source_facts.bundle import CandidateFactBundle
from daengs_place.place.source_facts.states import (
    DetailAcquisitionState,
    FactState,
    ProjectionState,
)

ExecutionOutcome = Literal["match", "mismatch", "unknown"]
SourceEvidenceStatus = Literal[
    "known",
    "unknown",
    "missing",
    "conflicted",
    "failed",
    "unsupported",
]
SOURCE_EVIDENCE_STATUSES: tuple[SourceEvidenceStatus, ...] = (
    "known",
    "unknown",
    "missing",
    "conflicted",
    "failed",
    "unsupported",
)


class PreviewCandidate(PlanningModel):
    place: PlaceResult
    bundles: tuple[CandidateFactBundle, ...] = ()

    @model_validator(mode="after")
    def bundles_match_candidate_sources(self) -> Self:
        allowed = {
            (self.place.match.source.source, self.place.match.source.ref),
            *(
                (provenance.source.source, provenance.source.ref)
                for provenance in self.place.field_sources.values()
            ),
        }
        keys = [(bundle.key.source, bundle.key.source_ref) for bundle in self.bundles]
        if len(set(keys)) != len(keys):
            raise ValueError("preview candidate bundle keys must be unique")
        if any(key not in allowed for key in keys):
            raise ValueError("preview bundles must match candidate or field source records")
        return self


class SourceEvidenceCoverage(PlanningModel):
    known: int = Field(0, ge=0)
    unknown: int = Field(0, ge=0)
    missing: int = Field(0, ge=0)
    conflicted: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    unsupported: int = Field(0, ge=0)
    acquisition_states: dict[DetailAcquisitionState, int] = Field(default_factory=dict)


class GatePreview(PlanningModel):
    capability_id: CapabilityId
    mode: GateMode
    input_candidates: int = Field(ge=0)
    known_match: int = Field(ge=0)
    known_mismatch: int = Field(ge=0)
    unknown: int = Field(ge=0)
    remaining: int = Field(ge=0)
    source_evidence: SourceEvidenceCoverage

    @model_validator(mode="after")
    def counts_are_complete(self) -> Self:
        if self.known_match + self.known_mismatch + self.unknown != self.input_candidates:
            raise ValueError("execution outcome counts must cover every input candidate")
        evidence_total = sum(
            getattr(self.source_evidence, status) for status in SOURCE_EVIDENCE_STATUSES
        )
        if evidence_total != self.input_candidates:
            raise ValueError("source evidence counts must cover every input candidate")
        if self.remaining > self.input_candidates:
            raise ValueError("remaining candidates cannot exceed gate input")
        return self


class PlaceSearchPlanPreview(PlanningModel):
    initial_candidates: int = Field(ge=0)
    candidate_limit_per_kind: int = Field(ge=1)
    truncated_kinds: tuple[PlaceKind, ...] = ()
    gates: tuple[GatePreview, ...]

    @model_validator(mode="after")
    def gate_sequence_is_consistent(self) -> Self:
        expected = self.initial_candidates
        for gate in self.gates:
            if gate.input_candidates != expected:
                raise ValueError("each gate input must equal the previous gate remainder")
            expected = gate.remaining
        return self


def _execution_outcome(gate: SearchGate, place: PlaceResult) -> ExecutionOutcome:
    if gate.capability_id is CapabilityId.PURPOSE_KIND:
        if not isinstance(gate.value, tuple):
            return "unknown"
        try:
            kind = PlaceKind(place.match.kind)
        except ValueError:
            return "unknown"
        return "match" if kind in gate.value else "mismatch"

    if gate.capability_id is CapabilityId.OPERATIONS_PARKING:
        parking = place.facts.parking
        if parking is None or not isinstance(gate.value, bool):
            return "unknown"
        return "match" if parking is gate.value else "mismatch"

    return "unknown"


def _evidence_source_and_bundle(
    gate: SearchGate,
    candidate: PreviewCandidate,
) -> tuple[str, CandidateFactBundle | None]:
    spec = capability_spec(gate.capability_id)
    provenance_sources = {
        provenance.source
        for path in spec.execution_paths
        if (provenance := candidate.place.field_sources.get(path)) is not None
    }
    if len(provenance_sources) > 1:
        raise ValueError("one capability cannot use multiple field evidence sources")
    source_ref = next(iter(provenance_sources), candidate.place.match.source)
    bundle = next(
        (
            value
            for value in candidate.bundles
            if (value.key.source, value.key.source_ref)
            == (source_ref.source, source_ref.ref)
        ),
        None,
    )
    return source_ref.source, bundle


def _source_evidence(
    gate: SearchGate,
    candidate: PreviewCandidate,
) -> tuple[SourceEvidenceStatus, CandidateFactBundle | None]:
    spec = capability_spec(gate.capability_id)
    source, bundle = _evidence_source_and_bundle(gate, candidate)
    if source not in spec.projection_sources:
        return "unsupported", bundle
    if bundle is None or bundle.availability == "missing":
        return "missing", bundle
    if bundle.projection_state is ProjectionState.FAILED:
        return "failed", bundle

    sections = {path.split(".", maxsplit=1)[0] for path in spec.projection_paths}
    if sections & {conflict.section for conflict in bundle.conflicts}:
        return "conflicted", bundle

    evidence = [
        variant.projection.evidence.get(path)
        for variant in bundle.variants
        for path in spec.projection_paths
    ]
    if evidence and all(item is not None and item.state is FactState.KNOWN for item in evidence):
        return "known", bundle
    return "unknown", bundle


def _source_coverage(
    gate: SearchGate,
    candidates: list[PreviewCandidate],
) -> SourceEvidenceCoverage:
    counts = {status: 0 for status in SOURCE_EVIDENCE_STATUSES}
    acquisition: dict[DetailAcquisitionState, int] = {}
    for candidate in candidates:
        status, bundle = _source_evidence(gate, candidate)
        counts[status] += 1
        if bundle is not None:
            for state in bundle.acquisition_states:
                acquisition[state] = acquisition.get(state, 0) + 1
    return SourceEvidenceCoverage(
        **counts,
        acquisition_states=dict(sorted(acquisition.items(), key=lambda item: item[0].value)),
    )


def _survives(gate: SearchGate, outcome: ExecutionOutcome) -> bool:
    if gate.mode is not GateMode.FILTER:
        return True
    if outcome == "match":
        return True
    return outcome == "unknown" and gate.unknown_policy in {
        UnknownPolicy.KEEP,
        UnknownPolicy.SEPARATE,
    }


def build_plan_preview(
    plan: PlaceSearchPlan,
    candidates: list[PreviewCandidate],
    *,
    candidate_limit_per_kind: int,
    truncated_kinds: tuple[PlaceKind, ...] = (),
) -> PlaceSearchPlanPreview:
    """gate 순서대로 후보 손실을 계산하되 prefer는 후보를 제거하지 않는다."""

    plan = guard_search_plan(plan)
    remaining = list(candidates)
    previews: list[GatePreview] = []
    for gate in plan.gates:
        if gate.mode is GateMode.OFF:
            continue
        outcomes = [_execution_outcome(gate, candidate.place) for candidate in remaining]
        next_remaining = [
            candidate
            for candidate, outcome in zip(remaining, outcomes, strict=True)
            if _survives(gate, outcome)
        ]
        previews.append(
            GatePreview(
                capability_id=gate.capability_id,
                mode=gate.mode,
                input_candidates=len(remaining),
                known_match=outcomes.count("match"),
                known_mismatch=outcomes.count("mismatch"),
                unknown=outcomes.count("unknown"),
                remaining=len(next_remaining),
                source_evidence=_source_coverage(gate, remaining),
            )
        )
        remaining = next_remaining
    return PlaceSearchPlanPreview(
        initial_candidates=len(candidates),
        candidate_limit_per_kind=candidate_limit_per_kind,
        truncated_kinds=truncated_kinds,
        gates=tuple(previews),
    )
