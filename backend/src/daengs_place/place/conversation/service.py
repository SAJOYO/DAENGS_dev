"""Deterministic workflow: plan → validate → acquire results → prepare commit."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from daengs_place.place.conversation.compiler import fingerprint
from daengs_place.place.conversation.context import edit_exclusions, identity, unique_keys
from daengs_place.place.conversation.contract import (
    ConversationState,
    DialogueTurn,
    ExecutionReceipt,
    ExplorationState,
    PreparedTurn,
    PrepareRequest,
    ResultSnapshot,
    SelectionBasis,
    TurnPlan,
)
from daengs_place.place.conversation.grounding import browse_scope
from daengs_place.place.conversation.policy import Decision, base_revision, decide
from daengs_place.place.conversation.render import selected_facts
from daengs_place.place.filters.contract import (
    Branch,
    FilterState,
    HardFilters,
    Preference,
    ResultPolicy,
)
from daengs_place.place.filters.service import search_filtered_places
from daengs_place.place.search import PlaceSearchGroup, PlaceSearchResponse, _parking_preference_key
from daengs_place.place.tools.changes import apply_changes
from daengs_place.place.tools.contract import FilterChanges

CACHE_SECONDS = 300


def snapshot_hits(snapshot):
    if snapshot is None:
        return []
    by_key = {
        (hit.place.key.source, hit.place.key.ref): hit
        for group in snapshot.result.groups
        for hit in group.matched
    }
    return [by_key[(key.source, key.ref)] for key in snapshot.display_order]


def public_search(snapshot):
    if snapshot is None:
        return None
    result = snapshot.result
    return PlaceSearchResponse(
        dogs=list(result.applied_state.dogs),
        evaluated_at=result.evaluated_at,
        name_query=result.applied_state.name_query,
        groups=[
            PlaceSearchGroup(
                kind=group.kind,
                limit=result.applied_state.result_policy.limit_per_kind,
                truncated=group.matched_truncated,
                results=list(group.matched),
            )
            for group in result.groups
        ],
    ).model_dump(mode="json")


def manual_filters(request, previous=None):
    if request.conditions is not None:
        raise ValueError("use selected dog snapshots in conversation search")
    kinds = tuple(request.kinds)
    hard = previous.filters.hard if previous else HardFilters()
    if previous and set(kinds) != set(previous.filters.candidate_kinds):
        hard = HardFilters(
            all=tuple(a for a in hard.all if a.capability != "purpose.kind"),
            any=tuple(
                Branch(id=b.id, all=atoms)
                for b in hard.any
                if (atoms := tuple(a for a in b.all if a.capability != "purpose.kind"))
            ),
        )
    return FilterState(
        candidate_kinds=kinds,
        spatial={"lat": request.lat, "lng": request.lng, "radius_m": request.radius_m},
        name_query=request.name_query,
        dogs=tuple(request.dogs),
        hard=hard,
        result_policy=ResultPolicy(limit_per_kind=min(request.limit_per_kind or 20, 20)),
        preferences=(
            Preference(
                id="parking-first",
                capability="operations.parking",
                op="eq",
                value=True,
                scope_kinds=kinds,
            ),
        )
        if request.preferences and request.preferences.parking
        else (),
    )


class ConversationService:
    def __init__(self, planner=None, *, searcher=search_filtered_places, now=None):
        self.planner = planner
        self.searcher = searcher
        self.now = now or (lambda: datetime.now(UTC))

    async def prepare(self, db, request: PrepareRequest) -> PreparedTurn:
        old = request.previous
        now = self.now()
        decision = Decision("execute")
        plan = TurnPlan(goal="show")
        if request.mode == "manual":
            candidate = manual_filters(request.manual, old)
        elif request.mode == "restore":
            candidate = request.restore_filters
        elif request.mode == "filters":
            # Direct UI operation: validate IDs, preserve all other fields, no planner or answer.
            candidate = apply_changes(
                old.filters, FilterChanges.model_validate(request.remove_filters.model_dump())
            )
        else:
            assert old is not None
            try:
                decision = await decide(self.planner, request, now)
            except (ValidationError, ValueError, TypeError):
                return self._unchanged(
                    request,
                    "clarify",
                    "invalid_plan",
                    "조건을 적용할 수 없어요. 바꾸려는 조건을 구체적으로 알려주세요.",
                    action="clarify",
                )
            now = self.now()
            if (
                decision.pending is not None
                and decision.action == "execute"
                and now >= decision.pending.expires_at
            ):
                return self._unchanged(
                    request,
                    "clarify",
                    "pending_expired",
                    "이전 제안이 만료되었어요. 원하는 조건을 다시 알려주세요.",
                )
            if decision.action not in {"execute", "explain"}:
                return self._unchanged(
                    request,
                    "clarify",
                    decision.code,
                    decision.question,
                    action=decision.action,
                    pending=decision.pending,
                    intent=decision.intent,
                )
            plan, candidate = decision.plan, decision.candidate
        changed = old is None or fingerprint(old.filters) != fingerprint(candidate)
        intent = decision.intent
        browse = browse_scope(request.query, intent)
        try:
            excluded, newly_excluded, restored = edit_exclusions(request, intent)
        except ValueError:
            return self._unchanged(
                request,
                "clarify",
                "invalid_exploration_target",
                "현재 목록 또는 제외 목록에서 어느 장소인지 확인해 주세요. 제외는 최대 120곳까지 가능해요.",
                action="clarify",
            )
        exclusion_keys = tuple(p.key for p in excluded)
        candidate_fingerprint = fingerprint(candidate)
        presented = ()
        if old and browse != "restart":
            if old.exploration.fingerprint == candidate_fingerprint:
                presented = old.exploration.presented
            elif old.snapshot and old.snapshot.fingerprint == candidate_fingerprint:
                # Compatibility with sessions created before exploration state existed.
                presented = old.snapshot.display_order
        if browse == "next" and len(presented) + 20 * len(candidate.candidate_kinds) > 1200:
            return self._unchanged(
                request,
                "clarify",
                "exploration_budget",
                "한 번의 탐색에서 기록할 수 있는 범위에 도달했어요. 조건을 좁히거나 처음부터 다시 찾아주세요.",
                action="clarify",
            )
        omitted = unique_keys((*exclusion_keys, *(presented if browse == "next" else ())))
        snapshot = old.snapshot if old else None
        same = (
            snapshot is not None
            and snapshot.fingerprint == candidate_fingerprint
            and snapshot.exclusions == exclusion_keys
        )
        if plan.goal == "show" and plan.reference_index is None and browse == "current":
            # An empty next page is not an empty full search. Normal show can revisit seen places.
            same = same and snapshot.omitted == omitted
        fresh = same and 0 <= (now - snapshot.created_at).total_seconds() < CACHE_SECONDS
        execution = "not_run"
        if plan.goal == "explain" and snapshot is None:
            return self._unchanged(
                request, plan.goal, "no_snapshot", "설명할 검색 결과가 아직 없어요."
            )
        needs_results = plan.goal in {"show", "pick_one"}
        if (
            plan.reference_index is not None
            and needs_results
            and (not fresh or plan.refresh or browse != "current")
        ):
            return self._unchanged(
                request,
                plan.goal,
                "reference_needs_confirmation",
                "이전 목록의 장소를 고를지, 새 조건으로 다시 찾을지 알려주세요.",
            )
        if needs_results and (not fresh or plan.refresh or browse != "current"):
            try:
                result = await self.searcher(
                    db, candidate, **({"omitted": omitted} if omitted else {})
                )
            except (SQLAlchemyError, TimeoutError):
                if old is None:
                    raise
                return self._unchanged(
                    request,
                    plan.goal,
                    "search_failed",
                    "",
                    execution="failed",
                    pending=decision.pending,
                )
            if result.applied_state != candidate:
                raise RuntimeError("search state mismatch")
            hits = [hit for group in result.groups for hit in group.matched]
            if any(hit.place.key in omitted for hit in hits):
                raise RuntimeError("search returned an omitted place")
            hits.sort(
                key=lambda hit: (
                    _parking_preference_key(hit.place)
                    if candidate.preferences
                    else (hit.place.distance_m, hit.place.key.source, hit.place.key.ref)
                )
            )
            # Source identity is preserved, even if two sources describe nearby places.
            snapshot = ResultSnapshot(
                id=uuid4(),
                fingerprint=fingerprint(candidate),
                created_at=now,
                result=result,
                display_order=tuple(hit.place.key for hit in hits),
                exclusions=exclusion_keys,
                omitted=omitted,
            )
            execution = "searched"
        elif needs_results or plan.goal == "explain":
            execution = "reused"
        selected = None
        evidence = {}
        facts = ()
        selection_basis = None
        attributes = decision.intent.asked_attributes if decision.intent else ()
        unsupported = (
            decision.intent.unsupported
            if decision.intent
            else (decision.pending.unsupported if decision.pending else ())
        )
        if not attributes and plan.goal == "explain":
            attributes = ("selection_reason",)
        if plan.goal == "pick_one":
            attributes = tuple(dict.fromkeys((*attributes, "distance", *unsupported)))
        used_visible_order = False
        if plan.goal in {"pick_one", "explain"}:
            hits = snapshot_hits(snapshot)
            if (
                request.visible_order
                and snapshot is not None
                and old
                and old.snapshot
                and snapshot.id == old.snapshot.id
            ):
                available = {(h.place.key.source, h.place.key.ref): h for h in hits}
                keys = [(key.source, key.ref) for key in request.visible_order]
                if len(set(keys)) != len(keys) or any(key not in available for key in keys):
                    return self._unchanged(
                        request,
                        plan.goal,
                        "invalid_visible_order",
                        "현재 보고 있는 결과를 확인해 주세요.",
                    )
                hits = [available[key] for key in keys]
                used_visible_order = True
            if plan.reference_index is not None:
                if plan.reference_index > len(hits):
                    return self._unchanged(
                        request, plan.goal, "invalid_reference", "몇 번째 장소인지 다시 알려주세요."
                    )
                hit = hits[plan.reference_index - 1]
            elif plan.goal == "explain" and old and (request.visible_selected or old.selected):
                key = request.visible_selected or old.selected
                hit = next((h for h in hits if h.place.key == key), None)
            else:
                hit = hits[0] if hits else None
            if hit is not None:
                selected = hit.place.key
                evidence = {
                    "place": hit.place.name,
                    "distance": f"검색 중심에서 {hit.place.distance_m}m 거리예요.",
                    "scope": "저장된 검색 후보에 포함된 장소예요.",
                }
                facts = selected_facts(hit.place, attributes)
                if plan.goal == "pick_one":
                    method = (
                        "user_reference"
                        if plan.reference_index
                        else (
                            "visible_order"
                            if used_visible_order
                            else ("parking_then_distance" if candidate.preferences else "distance")
                        )
                    )
                    selection_basis = SelectionBasis(
                        place=selected, snapshot_id=snapshot.id, method=method
                    )
                elif (
                    old
                    and old.selection_basis
                    and old.selection_basis.place == selected
                    and old.selection_basis.snapshot_id == snapshot.id
                ):
                    selection_basis = old.selection_basis
                if hit.place.facts.address:
                    evidence["address"] = hit.place.facts.address
            elif plan.goal == "explain":
                return self._unchanged(
                    request, plan.goal, "no_reference", "어느 장소를 설명할까요?"
                )
        history = old.history if old else ()
        if request.mode == "chat":
            history = (
                *history,
                DialogueTurn(query=request.query, goal=plan.goal, selected=selected),
            )[-6:]
        new_places = ()
        remaining = "unknown"
        if needs_results and snapshot:
            new_places = tuple(
                key
                for key in snapshot.display_order
                if identity(key) not in {identity(p) for p in presented}
            )
            presented = unique_keys((*presented, *snapshot.display_order))
            if len(presented) > 1200:
                # A current search can refresh results as data changes; never silently drop history.
                return self._unchanged(
                    request,
                    "clarify",
                    "exploration_budget",
                    "탐색 기록 한도에 도달했어요. 조건을 좁히거나 처음부터 다시 찾아주세요.",
                    action="clarify",
                )
            remaining = (
                "more" if any(g.matched_truncated for g in snapshot.result.groups) else "exhausted"
            )
        elif old and not changed:
            presented = old.exploration.presented
        state = ConversationState(
            filters=candidate,
            snapshot=snapshot,
            selected=selected,
            history=history,
            revision=base_revision(request) + 1,
            selection_basis=selection_basis,
            exploration=ExplorationState(
                excluded=excluded,
                presented=presented,
                fingerprint=candidate_fingerprint,
            ),
        )
        receipt = ExecutionReceipt(
            goal=plan.goal,
            execution=execution,
            filters_changed=changed,
            result_matches_filters=snapshot is not None
            and snapshot.fingerprint == fingerprint(candidate),
            returned_count=len(snapshot_hits(snapshot)),
            snapshot_id=snapshot.id if snapshot else None,
            selected=selected,
            evidence=evidence,
            action=decision.action,
            asked_attributes=attributes,
            facts=facts,
            unsupported=unsupported,
            selection_basis=selection_basis,
            browse=browse,
            new_places=new_places,
            excluded_places=newly_excluded,
            restored_places=restored,
            remaining=remaining,
        )
        return PreparedTurn(state=state, receipt=receipt)

    @staticmethod
    def _unchanged(
        request,
        goal,
        code,
        question,
        *,
        execution="not_run",
        action="clarify",
        pending=None,
        intent=None,
    ):
        old = request.previous
        snapshot = old.snapshot
        revision = base_revision(request) + 1
        if pending is not None:
            pending = pending.model_copy(update={"revision": revision})
        history = old.history
        if request.mode == "chat":
            history = (
                *history,
                DialogueTurn(query=request.query, goal=goal, selected=old.selected),
            )[-6:]
        state = old.model_copy(
            update={
                "pending_question": question[:200],
                "pending_proposal": pending,
                "revision": revision,
                "history": history,
            }
        )
        return PreparedTurn(
            state=state,
            receipt=ExecutionReceipt(
                goal=goal,
                execution=execution,
                action=action,
                pending_id=pending.id if pending else None,
                result_matches_filters=snapshot is not None
                and snapshot.fingerprint == fingerprint(old.filters),
                returned_count=len(snapshot_hits(snapshot)),
                snapshot_id=snapshot.id if snapshot else None,
                selected=old.selected,
                code=code,
                question=question,
                asked_attributes=intent.asked_attributes if intent else (),
                unsupported=intent.unsupported
                if intent
                else (pending.unsupported if pending else ()),
            ),
        )
