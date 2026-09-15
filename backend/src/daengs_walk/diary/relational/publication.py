"""Frozen comparison cards: read one card without replaying earlier narratives."""

from typing import Literal

from pydantic import model_validator

from daengs_walk.diary.relational.delivery import DeliveryState
from daengs_walk.diary.relational.scene_comparison_contracts import (
    SceneCardHeader,
    SpaceComparisonAnswer,
    SpaceComparisonInput,
)
from daengs_walk.value_contracts import ValueContract, digest

PUBLICATION_VERSION = "relational-diary-skeleton-v7"


class ComparisonPublication(ValueContract):
    version: Literal["scene-comparison-publication-v1"] = "scene-comparison-publication-v1"
    context: SpaceComparisonInput
    context_revision: str
    header: SceneCardHeader
    selection: SpaceComparisonAnswer | None
    semantic_status: Literal["model_reviewed", "unverified", "not_published"]
    delivery_before: DeliveryState
    delivery_after: DeliveryState

    @model_validator(mode="after")
    def frozen_context(self):
        if self.context_revision != digest(self.context):
            raise ValueError("published context revision changed")
        if self.header.scene_id != self.context.current.scene_id:
            raise ValueError("header belongs to another scene")
        if (self.selection is None) != (self.semantic_status == "not_published"):
            raise ValueError("selection publication status disagrees")
        if self.selection:
            self.selection.validate_against(self.context)
            usable = {
                r.id
                for r in self.context.relation_slots.all_relations()
                if r.result not in {"incomparable", "only_one_snapshot_has_evidence"}
            }
            if not set(self.selection.relation_ids) <= usable:
                raise ValueError("unusable relation selected for publication")
        return self


def validate_publication(receipt):
    """Only saved values: no preparation, network, writer or latest data lookup."""
    from daengs_walk.diary.relational.delivery import advance_delivery

    memory = DeliveryState()
    results = {r["task_id"]: r for r in receipt["writing"]["results"]}
    if len(results) != len(receipt["writing"]["results"]):
        raise ValueError("duplicate saved writer result")
    used_tasks, seen_scenes = set(), set()
    previous_scene = None
    for card in receipt["cards"]:
        pub = ComparisonPublication.model_validate(card["comparison"])
        current_scene = pub.context.current
        if previous_scene and (
            current_scene.walk_id != previous_scene.walk_id
            or current_scene.recorded_at < previous_scene.recorded_at
        ):
            raise ValueError("published cards must be in chronological order in one walk")
        previous_scene = current_scene
        if card["scene_id"] in seen_scenes:
            raise ValueError("duplicate published scene")
        seen_scenes.add(card["scene_id"])
        if card["scene_id"] != pub.context.current.scene_id or pub.delivery_before != memory:
            raise ValueError("published scene or memory chain changed")
        part = card["parts"]["space"]
        result = results.get(part["task_id"])
        selected = result.get("answer") if result and result["status"] == "returned" else None
        if (pub.selection.model_dump(mode="json") if pub.selection else None) != selected:
            raise ValueError("published selection differs from accepted writer result")
        if part["text"] != (pub.selection.text if pub.selection else ""):
            raise ValueError("published text differs from selection")
        if part["status"] != (result["status"] if result else "not_requested"):
            raise ValueError("published task status differs")
        expected_status = result["semantic_status"] if selected else "not_published"
        if pub.semantic_status != expected_status:
            raise ValueError("published semantic status differs")
        if (
            selected
            and expected_status == "model_reviewed"
            and result.get("semantic_review", {}).get("status") != "passed"
        ):
            raise ValueError("reviewed publication has no passed review")
        if card["relation_selection"]["space"] != (
            list(pub.selection.relation_ids) if pub.selection else []
        ):
            raise ValueError("published relation selection differs")
        memory = advance_delivery(
            memory,
            {
                "scene_id": card["scene_id"],
                "scene_snapshot": pub.context.current.model_dump(mode="json"),
                "spatial_comparison_slots": pub.context.relation_slots.model_dump(mode="json"),
            },
            {"id": part["task_id"]},
            result,
        )
        if pub.delivery_after != memory:
            raise ValueError("published memory differs from accepted results")
        for stage, stage_part in card["parts"].items():
            entry = results.get(stage_part["task_id"])
            if stage_part["task_id"]:
                if entry is None or stage_part["task_id"] in used_tasks:
                    raise ValueError("missing or reused saved task result")
                used_tasks.add(stage_part["task_id"])
            text = entry["answer"]["text"] if entry and entry["status"] == "returned" else ""
            if stage_part["text"] != text or (
                entry and (entry["scene_id"], entry["stage"]) != (card["scene_id"], stage)
            ):
                raise ValueError("published part differs from accepted task")
        if card["body"] != "\n".join(p["text"] for p in card["parts"].values() if p["text"]):
            raise ValueError("published body changed")
    if used_tasks != results.keys():
        raise ValueError("unexpected saved writer result")
