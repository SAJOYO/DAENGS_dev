"""Bounded publication memory. Cited facts are not proof of semantic coverage."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from daengs_walk.diary.relational.scene_comparison_contracts import SpaceComparisonAnswer
from daengs_walk.value_contracts import ValueContract, digest


class DeliveredSelection(ValueContract):
    scene_id: str
    task_id: str
    context_signature: str
    focus: str
    evidence_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    semantic_status: Literal["model_reviewed", "unverified"]


class DeliveryState(ValueContract):
    active_introduction: DeliveredSelection | None = None
    recent_deliveries: tuple[DeliveredSelection, ...] = Field(default=(), max_length=2)
    all_context_facts_delivered: Literal[False] = False


def context_signature(frame):
    meanings = []
    for fact in frame["scene_snapshot"]["facts"]:
        value = deepcopy(fact["value"])
        for key in ("reference_dates", "reference_month", "classification_policy", "source_hash"):
            value.pop(key, None)
        meanings.append(
            [
                fact["family"],
                fact["subject_key"],
                value,
                fact["scope"]["kind"],
                fact["scope"].get("coverage_key")
                if fact["scope"]["kind"] == "query_area"
                else None,
            ]
        )
    return digest(sorted(meanings, key=digest)) if meanings else None


def advance_delivery(state, frame, task, result):
    state = DeliveryState.model_validate(state)
    signature = context_signature(frame)
    active = state.active_introduction
    if active and active.context_signature != signature:
        active = None
    recent = state.recent_deliveries
    if result and result["status"] == "returned":
        answer = SpaceComparisonAnswer.model_validate(result["answer"])
        selection = DeliveredSelection(
            scene_id=frame["scene_id"],
            task_id=task["id"],
            context_signature=signature,
            focus=answer.focus,
            evidence_ids=answer.evidence_ids,
            relation_ids=answer.relation_ids,
            semantic_status=result["semantic_status"],
        )
        recent = (*recent, selection)[-2:]
        # A published retrospective or route-only sentence does not introduce
        # the current background. Keep it in history without suppressing recovery.
        current_ids = {f["id"] for f in frame["scene_snapshot"]["facts"]}
        cited = set(answer.evidence_ids)
        if result["semantic_status"] == "model_reviewed":
            reviewed = result["semantic_review"]["assessment"]["used_evidence_ids"]
            aliases = result.get("citation_map", {})
            cited = {aliases.get(key, key) for key in reviewed}
        else:
            # Selected relations are explicit claims about their current endpoint.
            for slot in frame.get("spatial_comparison_slots", {}).values():
                for relation in slot["items"]:
                    if relation["id"] in answer.relation_ids:
                        cited.update(relation["current_evidence_ids"])
        if current_ids & cited:
            active = selection
    return DeliveryState(active_introduction=active, recent_deliveries=recent)
