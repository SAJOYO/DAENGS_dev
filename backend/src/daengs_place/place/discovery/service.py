"""검증된 Place lens를 검색하고 source-aware presentation까지 조립한다."""

from collections.abc import Awaitable, Callable
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.place.discovery.contract import (
    PlaceDiscoveryData,
    PlaceDiscoveryLensResult,
    PlaceDiscoveryNotice,
    PlaceDiscoveryPlanningData,
    PlaceDiscoveryRequest,
    PlaceDiscoveryResultPolicy,
)
from daengs_place.place.information_needs import InformationNeedId
from daengs_place.place.intent.lenses import (
    LensAvailability,
    SearchLensOutcome,
    SearchSignalLens,
    TargetSearchLens,
)
from daengs_place.place.intent.service import (
    PlaceIntentSuggestionService,
    PlaceIntentSuggestionTrace,
)
from daengs_place.place.planning.contract import (
    CapabilityId,
    GateMode,
    PlaceSearchPlan,
)
from daengs_place.place.planning.execution import purpose_kinds
from daengs_place.place.planning.intents import PlannerIssue
from daengs_place.place.presentation.assembler import assemble_place_presentation
from daengs_place.place.search import (
    PlaceSearchResponse,
    search_place_plan,
)
from daengs_place.place.source_facts.bundle import CandidateFactBundle, SourceFactKey
from daengs_place.place.source_facts.reader import (
    MAX_BUNDLE_CANDIDATES,
    load_candidate_fact_bundles,
    source_fact_key,
)

SearchPlanExecutor = Callable[
    [AsyncSession, PlaceSearchPlan],
    Awaitable[PlaceSearchResponse],
]
SourceFactLoader = Callable[
    [AsyncSession, list[SourceFactKey]],
    Awaitable[list[CandidateFactBundle]],
]

_OPTION_NEEDS = {
    item.value: item
    for item in (
        InformationNeedId.COST_TRAVEL_DISTANCE,
        InformationNeedId.COST_PET_FEE,
        InformationNeedId.COST_ADMISSION,
        InformationNeedId.COST_PRODUCT_PRICE,
    )
}


def _public_issues(trace: PlaceIntentSuggestionTrace) -> tuple[PlannerIssue, ...]:
    candidates = trace.outcome.rejected
    issue_groups = (
        trace.outcome.issues,
        *(candidate.result.not_applied for candidate in candidates),
        *(candidate.result.unsupported for candidate in candidates),
        *(candidate.result.clarifications for candidate in candidates),
    )
    seen: set[tuple[tuple[str, ...], str, str, bool]] = set()
    public = []
    for issue in (item for group in issue_groups for item in group):
        key = (issue.observation_ids, issue.code, issue.detail, issue.blocking)
        if key in seen:
            continue
        seen.add(key)
        public.append(issue)
    return tuple(public)


def project_discovery_planning(
    trace: PlaceIntentSuggestionTrace,
) -> PlaceDiscoveryPlanningData:
    """내부 trace에서 raw·grounded·normalized provider 자료를 제외한다."""

    outcome = trace.outcome
    return PlaceDiscoveryPlanningData(
        status=outcome.status,
        source_disposition=outcome.source_disposition,
        resolution=outcome.resolution,
        lenses=trace.lenses or SearchLensOutcome(target_lenses=(), signal_lenses=()),
        issues=_public_issues(trace),
        rejected_candidate_count=len(outcome.rejected),
    )


def _signal_belongs_to_target(signal: SearchSignalLens, target: TargetSearchLens) -> bool:
    marker = ":facet:"
    if marker not in signal.lens_id or not signal.lens_id.startswith("signal:"):
        return False
    hypothesis_set_key = signal.lens_id.removeprefix("signal:").split(marker, maxsplit=1)[0]
    return target.lens_id.startswith(f"lens:{hypothesis_set_key}:")


def information_needs_for_lens(
    lens: TargetSearchLens,
    signal_lenses: tuple[SearchSignalLens, ...],
) -> tuple[InformationNeedId, ...]:
    """lens 의미를 표시 우선순위로 옮기되 검색 조건으로 승격하지 않는다."""

    needs = list(lens.information_need_ids)
    plan = lens.candidate.result.plan
    if plan is None:
        raise ValueError("information needs require a ready target plan")
    if any(
        gate.capability_id is CapabilityId.OPERATIONS_PARKING and gate.mode is not GateMode.OFF
        for gate in plan.gates
    ):
        needs.append(InformationNeedId.OPERATIONS_PARKING)
    if plan.conditions is not None and (
        plan.conditions.dog_size is not None or plan.conditions.dog_weight_kg is not None
    ):
        needs.append(InformationNeedId.PET_SIZE)
    needs.extend(
        _OPTION_NEEDS[item.selected_option_id]
        for item in signal_lenses
        if item.availability is LensAvailability.RESOLVED
        and item.selected_option_id in _OPTION_NEEDS
        and _signal_belongs_to_target(item, lens)
    )
    return tuple(dict.fromkeys(needs))


def _candidate_limits(
    policy: PlaceDiscoveryResultPolicy,
    lens_count: int,
) -> tuple[int, ...]:
    if lens_count == 0:
        return ()
    shared = min(policy.max_candidates_per_lens, policy.max_total_candidates // lens_count)
    remainder = min(policy.max_total_candidates - shared * lens_count, lens_count)
    return tuple(shared + (index < remainder) for index in range(lens_count))


def _execution_plan(plan: PlaceSearchPlan, candidate_limit: int) -> PlaceSearchPlan:
    kind_count = len(purpose_kinds(plan))
    per_kind_limit = min(plan.limit_per_kind, ceil(candidate_limit / kind_count))
    return plan.model_copy(update={"limit_per_kind": per_kind_limit})


def _limit_search(
    search: PlaceSearchResponse,
    candidate_limit: int,
) -> tuple[PlaceSearchResponse, bool]:
    remaining = candidate_limit
    policy_truncated = False
    groups = []
    for group in search.groups:
        selected = group.results[:remaining]
        if len(selected) < len(group.results):
            policy_truncated = True
        remaining -= len(selected)
        groups.append(
            group.model_copy(
                update={
                    "results": selected,
                    "truncated": group.truncated or len(selected) < len(group.results),
                }
            )
        )
    return search.model_copy(update={"groups": groups}), policy_truncated


def _source_keys(searches: list[PlaceSearchResponse]) -> list[SourceFactKey]:
    unique: dict[tuple[str, str], SourceFactKey] = {}
    for search in searches:
        for group in search.groups:
            for hit in group.results:
                key = source_fact_key(hit.place.key)
                if key is not None:
                    unique.setdefault((key.source, key.source_ref), key)
    return list(unique.values())


async def _load_bundles(
    db: AsyncSession,
    keys: list[SourceFactKey],
    loader: SourceFactLoader,
) -> dict[tuple[str, str], CandidateFactBundle]:
    bundles: dict[tuple[str, str], CandidateFactBundle] = {}
    for offset in range(0, len(keys), MAX_BUNDLE_CANDIDATES):
        chunk = keys[offset : offset + MAX_BUNDLE_CANDIDATES]
        loaded = await loader(db, chunk)
        if [item.key for item in loaded] != chunk:
            raise RuntimeError("source fact loader must preserve every requested key in order")
        for item in loaded:
            bundles[(item.key.source, item.key.source_ref)] = item
    return bundles


def _drop_last_candidate(
    lens_result: PlaceDiscoveryLensResult,
) -> PlaceDiscoveryLensResult:
    groups = list(lens_result.search.groups)
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group.results:
            groups[index] = group.model_copy(
                update={"results": group.results[:-1], "truncated": True}
            )
            break
    return lens_result.model_copy(
        update={
            "search": lens_result.search.model_copy(update={"groups": groups}),
            "presentations": lens_result.presentations[:-1],
        }
    )


def _fit_byte_budget(
    planning: PlaceDiscoveryPlanningData,
    policy: PlaceDiscoveryResultPolicy,
    lens_results: list[PlaceDiscoveryLensResult],
    notices: list[PlaceDiscoveryNotice],
) -> PlaceDiscoveryData:
    data = PlaceDiscoveryData(
        planning=planning,
        result_policy=policy,
        lens_results=tuple(lens_results),
        notices=tuple(notices),
    )
    if data.serialized_size_bytes <= policy.max_serialized_bytes:
        return data

    notices.append(
        PlaceDiscoveryNotice(
            code="discovery.serialized_byte_budget_applied",
            message="응답 크기 상한에 맞춰 뒤쪽 후보를 줄였습니다.",
        )
    )
    while True:
        data = PlaceDiscoveryData(
            planning=planning,
            result_policy=policy,
            lens_results=tuple(lens_results),
            notices=tuple(notices),
        )
        if data.serialized_size_bytes <= policy.max_serialized_bytes:
            return data
        for index in range(len(lens_results) - 1, -1, -1):
            if lens_results[index].presentations:
                lens_results[index] = _drop_last_candidate(lens_results[index])
                break
        else:
            raise ValueError("discovery metadata exceeds the serialized byte budget")


class PlaceDiscoveryService:
    """Place 내부 planning과 실행을 잇는다. HTTP·전역 route·provider SDK는 소유하지 않는다."""

    def __init__(
        self,
        intent_service: PlaceIntentSuggestionService,
        *,
        searcher: SearchPlanExecutor = search_place_plan,
        source_fact_loader: SourceFactLoader = load_candidate_fact_bundles,
    ):
        self._intent_service = intent_service
        self._searcher = searcher
        self._source_fact_loader = source_fact_loader

    async def plan(self, request: PlaceDiscoveryRequest) -> PlaceDiscoveryPlanningData:
        trace = await self._intent_service.inspect(
            request.query,
            spatial=request.spatial,
            limit_per_kind=request.result_policy.max_candidates_per_lens,
            conditions=request.conditions,
        )
        return project_discovery_planning(trace)

    async def discover(
        self,
        db: AsyncSession,
        request: PlaceDiscoveryRequest,
    ) -> PlaceDiscoveryData:
        planning = await self.plan(request)
        return await self.execute(db, planning, request.result_policy)

    async def execute(
        self,
        db: AsyncSession,
        planning: PlaceDiscoveryPlanningData,
        result_policy: PlaceDiscoveryResultPolicy,
    ) -> PlaceDiscoveryData:
        """초기 planning과 사용자 refinement 결과를 같은 예산으로 실행한다."""

        executable = planning.lenses.executable_targets
        targets = executable[: result_policy.max_executable_lenses]
        notices = []
        if len(targets) < len(executable):
            notices.append(
                PlaceDiscoveryNotice(
                    code="discovery.lens_budget_applied",
                    message=(
                        f"탐색 방향 {len(executable)}개 중 정책 순서의 "
                        f"{len(targets)}개를 실행했습니다."
                    ),
                )
            )

        candidate_limits = _candidate_limits(result_policy, len(targets))
        searches = []
        for target, candidate_limit in zip(targets, candidate_limits, strict=True):
            plan = target.candidate.result.plan
            if plan is None:
                raise RuntimeError("executable target lens did not carry a search plan")
            execution_plan = _execution_plan(plan, candidate_limit)
            search, response_trimmed = _limit_search(
                await self._searcher(db, execution_plan),
                candidate_limit,
            )
            if execution_plan.limit_per_kind < plan.limit_per_kind or response_trimmed:
                notices.append(
                    PlaceDiscoveryNotice(
                        code="discovery.candidate_budget_applied",
                        lens_id=target.lens_id,
                        message=f"이 탐색 방향의 표시 후보를 {candidate_limit}개로 제한했습니다.",
                    )
                )
            searches.append(search)

        bundles = await _load_bundles(
            db,
            _source_keys(searches),
            self._source_fact_loader,
        )
        lens_results = []
        for target, candidate_limit, search in zip(
            targets,
            candidate_limits,
            searches,
            strict=True,
        ):
            needs = information_needs_for_lens(
                target,
                planning.lenses.signal_lenses,
            )
            presentations = []
            for group in search.groups:
                for hit in group.results:
                    key = source_fact_key(hit.place.key)
                    bundle = bundles.get((key.source, key.source_ref)) if key is not None else None
                    presentations.append(
                        assemble_place_presentation(
                            hit,
                            group,
                            lens_id=target.lens_id,
                            lens_label=target.display_label,
                            lens_support_note=target.support_note,
                            information_needs=needs,
                            source_facts=bundle,
                        )
                    )
            lens_results.append(
                PlaceDiscoveryLensResult(
                    lens_id=target.lens_id,
                    display_label=target.display_label,
                    support_note=target.support_note,
                    information_needs=needs,
                    candidate_limit=candidate_limit,
                    search=search,
                    presentations=tuple(presentations),
                )
            )
        return _fit_byte_budget(planning, result_policy, lens_results, notices)


__all__ = [
    "PlaceDiscoveryService",
    "information_needs_for_lens",
    "project_discovery_planning",
]
