"""실제 spatial 후보와 shadow bundle을 결합하는 plan preview application service."""

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.place.contracts import PlaceResult
from daengs_place.place.planning.capabilities import capability_spec
from daengs_place.place.planning.contract import (
    MAX_RESULTS_PER_KIND,
    CapabilityId,
    GateMode,
    PlaceSearchPlan,
)
from daengs_place.place.planning.guard import guard_search_plan
from daengs_place.place.planning.preview import (
    PlaceSearchPlanPreview,
    PreviewCandidate,
    build_plan_preview,
)
from daengs_place.place.search import search_place_plan
from daengs_place.place.source_facts.bundle import SourceFactKey
from daengs_place.place.source_facts.reader import (
    MAX_BUNDLE_CANDIDATES,
    load_candidate_fact_bundles,
    source_fact_key,
)


def _source_fact_keys(
    place: PlaceResult,
    plan: PlaceSearchPlan,
) -> tuple[SourceFactKey, ...]:
    refs = [place.match.source]
    for gate in plan.gates:
        if gate.mode is GateMode.OFF:
            continue
        spec = capability_spec(gate.capability_id)
        refs.extend(
            provenance.source
            for path in spec.execution_paths
            if (provenance := place.field_sources.get(path)) is not None
        )
    keys: list[SourceFactKey] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = source_fact_key(ref)
        identity = (ref.source, ref.ref)
        if key is not None and identity not in seen:
            seen.add(identity)
            keys.append(key)
    return tuple(keys)


def _candidate_plan(plan: PlaceSearchPlan, limit_per_kind: int) -> PlaceSearchPlan:
    gates = tuple(
        gate.model_copy(update={"mode": GateMode.OFF})
        if gate.capability_id is not CapabilityId.PURPOSE_KIND
        else gate
        for gate in plan.gates
    )
    return guard_search_plan(
        plan.model_copy(update={"gates": gates, "limit_per_kind": limit_per_kind})
    )


async def preview_search_plan(
    db: AsyncSession,
    plan: PlaceSearchPlan,
) -> PlaceSearchPlanPreview:
    """선호 적용 전 최대 1,000개 spatial 후보에서 실행과 source 근거를 함께 센다."""

    plan = guard_search_plan(plan)
    purpose_gate = next(
        gate for gate in plan.gates if gate.capability_id is CapabilityId.PURPOSE_KIND
    )
    if not isinstance(purpose_gate.value, tuple):
        raise TypeError("validated purpose gate must contain kinds")
    limit_per_kind = min(
        MAX_RESULTS_PER_KIND,
        MAX_BUNDLE_CANDIDATES // len(purpose_gate.value),
    )
    response = await search_place_plan(db, _candidate_plan(plan, limit_per_kind))
    places = [hit.place for group in response.groups for hit in group.results]

    candidate_keys = [_source_fact_keys(place, plan) for place in places]
    unique_keys: dict[tuple[str, str], SourceFactKey] = {}
    for keys in candidate_keys:
        for key in keys:
            unique_keys[(key.source, key.source_ref)] = key
    requested = list(unique_keys.values())
    loaded = []
    for start in range(0, len(requested), MAX_BUNDLE_CANDIDATES):
        loaded.extend(
            await load_candidate_fact_bundles(
                db, requested[start : start + MAX_BUNDLE_CANDIDATES]
            )
        )
    bundles_by_key = {
        (bundle.key.source, bundle.key.source_ref): bundle for bundle in loaded
    }
    candidates = [
        PreviewCandidate(
            place=place,
            bundles=tuple(
                bundles_by_key[(key.source, key.source_ref)] for key in keys
            ),
        )
        for place, keys in zip(places, candidate_keys, strict=True)
    ]
    return build_plan_preview(
        plan,
        candidates,
        candidate_limit_per_kind=limit_per_kind,
        truncated_kinds=tuple(group.kind for group in response.groups if group.truncated),
    )
