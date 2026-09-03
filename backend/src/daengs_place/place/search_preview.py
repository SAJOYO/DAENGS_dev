"""실제 spatial 후보와 shadow bundle을 결합하는 plan preview application service."""

from sqlalchemy.ext.asyncio import AsyncSession

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
from daengs_place.place.source_facts.reader import (
    MAX_BUNDLE_CANDIDATES,
    load_candidate_fact_bundles,
    source_fact_key,
)


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

    bundle_indexes = []
    keys = []
    for index, place in enumerate(places):
        key = source_fact_key(place.match.source)
        if key is not None:
            bundle_indexes.append(index)
            keys.append(key)
    loaded = await load_candidate_fact_bundles(db, keys)
    bundles_by_index = dict(zip(bundle_indexes, loaded, strict=True))
    candidates = [
        PreviewCandidate(place=place, bundle=bundles_by_index.get(index))
        for index, place in enumerate(places)
    ]
    return build_plan_preview(
        plan,
        candidates,
        candidate_limit_per_kind=limit_per_kind,
        truncated_kinds=tuple(group.kind for group in response.groups if group.truncated),
    )
