"""Deterministic workflow: plan → validate → acquire results → prepare commit."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from daengs_place.place.conversation.contract import (
    ConversationState,
    DialogueTurn,
    ExecutionReceipt,
    PreparedTurn,
    PrepareRequest,
    ResultSnapshot,
    TurnPlan,
)
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

CACHE_SECONDS = 300


def fingerprint(state: FilterState) -> str:
    data = state.model_dump(mode="json")

    def canonical(value):
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items() if k != "id"}
        if isinstance(value, list):
            return sorted(
                (canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True)
            )
        return value

    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True).encode()).hexdigest()


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
        plan = TurnPlan(goal="show")
        if request.mode == "manual":
            candidate = manual_filters(request.manual, old)
        elif request.mode == "restore":
            candidate = request.restore_filters
        else:
            assert old is not None
            try:
                plan = await self.planner.plan(request)
                candidate = apply_changes(old.filters, plan.changes)
            except (ValidationError, ValueError):
                return self._unchanged(
                    old,
                    "clarify",
                    "invalid_plan",
                    "조건을 적용할 수 없어요. 바꾸려는 조건을 구체적으로 알려주세요.",
                )
        changed = old is None or fingerprint(old.filters) != fingerprint(candidate)
        snapshot = old.snapshot if old else None
        now = self.now()
        same = snapshot is not None and snapshot.fingerprint == fingerprint(candidate)
        fresh = same and 0 <= (now - snapshot.created_at).total_seconds() < CACHE_SECONDS
        execution = "not_run"
        if plan.goal == "clarify":
            return self._unchanged(old, plan.goal, "clarification_required", plan.question)
        if plan.goal == "explain" and snapshot is None:
            return self._unchanged(old, plan.goal, "no_snapshot", "설명할 검색 결과가 아직 없어요.")
        needs_results = plan.goal in {"show", "pick_one"}
        if plan.reference_index is not None and needs_results and (not fresh or plan.refresh):
            return self._unchanged(
                old,
                plan.goal,
                "reference_needs_confirmation",
                "이전 목록의 장소를 고를지, 새 조건으로 다시 찾을지 알려주세요.",
            )
        if needs_results and (not fresh or plan.refresh):
            try:
                result = await self.searcher(db, candidate)
            except (SQLAlchemyError, TimeoutError):
                if old is None:
                    raise
                return self._unchanged(old, plan.goal, "search_failed", "", execution="failed")
            if result.applied_state != candidate:
                raise RuntimeError("search state mismatch")
            hits = [hit for group in result.groups for hit in group.matched]
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
            )
            execution = "searched"
        elif needs_results or plan.goal == "explain":
            execution = "reused"
        selected = None
        evidence = {}
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
                        old,
                        plan.goal,
                        "invalid_visible_order",
                        "현재 보고 있는 결과를 확인해 주세요.",
                    )
                hits = [available[key] for key in keys]
            if plan.reference_index is not None:
                if plan.reference_index > len(hits):
                    return self._unchanged(
                        old, plan.goal, "invalid_reference", "몇 번째 장소인지 다시 알려주세요."
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
                if hit.place.facts.parking is True:
                    evidence["parking"] = "원천 정보에 주차 가능으로 확인돼요."
                if hit.place.facts.address:
                    evidence["address"] = hit.place.facts.address
            elif plan.goal == "explain":
                return self._unchanged(old, plan.goal, "no_reference", "어느 장소를 설명할까요?")
        history = old.history if old else ()
        if request.mode == "chat":
            history = (
                *history,
                DialogueTurn(query=request.query, goal=plan.goal, selected=selected),
            )[-6:]
        state = ConversationState(
            filters=candidate, snapshot=snapshot, selected=selected, history=history
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
        )
        return PreparedTurn(state=state, receipt=receipt)

    @staticmethod
    def _unchanged(old, goal, code, question, *, execution="not_run"):
        snapshot = old.snapshot
        state = old.model_copy(update={"pending_question": question})
        return PreparedTurn(
            state=state,
            receipt=ExecutionReceipt(
                goal=goal,
                execution=execution,
                result_matches_filters=snapshot is not None
                and snapshot.fingerprint == fingerprint(old.filters),
                returned_count=len(snapshot_hits(snapshot)),
                snapshot_id=snapshot.id if snapshot else None,
                selected=old.selected,
                code=code,
                question=question,
            ),
        )
