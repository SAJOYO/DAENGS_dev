"""Read admitted card evidence using the same projection as actual writing.

No acquisition, jobs, model call, publication or tool registration. Only llm_input
is writing material; request/evidence retain internal binding and audit data.
"""

from dataclasses import dataclass, field, replace
from typing import Literal

from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import policy
from daengs_walk.diary.board.action_context import pin_movement
from daengs_walk.diary.board.narration import VERSION as NARRATION_VERSION
from daengs_walk.diary.board.scene_input import action_anchor
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.slots.space import writing_facts


@dataclass(frozen=True)
class WritingContext:
    stage: Literal["space", "action"]
    request: dict
    evidence: dict = field(default_factory=dict)

    @property
    def llm_input(self):
        """Project a fresh writing-only dictionary; never send this whole object."""
        return normalize(self.stage, self.request).payload


def _resolve(base, card_id):
    if (
        base.input.source.revision() != base.board.input_revision
        or base.slots.input_revision != base.board.input_revision
        or base.slots.plan_revision != base.board.plan_revision
        or base.slots.plan_revision != base.plan.revision()
    ):
        raise ValueError("writing context requires the prepared source and slot version")
    scenes = [s for s in base.board.scenes if s.id == card_id]
    stamps = [s for s in base.slots.stamps if s.scene_id == card_id]
    if len(scenes) != 1 or len(stamps) != 1:
        raise ValueError("card is outside the prepared writing context")
    return scenes[0], stamps[0]


def _common_context(base):
    names = dict(base.input.pet_names)
    return {
        "narration_version": NARRATION_VERSION,
        "record_kind": "guardian_walk_diary",
        "companions": [
            {"id": pet_id, "name": names.get(pet_id)} for pet_id in base.input.source.pet_ids
        ],
    }


def get_action_context(base, card_id):
    """Read one behavior pin and its simultaneous movement; return None without a pin."""
    scene, stamp = _resolve(base, card_id)
    anchor = action_anchor(scene)
    if anchor is None:
        return None
    movement = pin_movement(
        [{"id": e.id, "facts": e.facts} for e in stamp.evidence if e.role == "scene_movement"]
    )
    pet_id = scene.core.record.content.pet_id
    if pet_id is not None and pet_id not in base.input.source.pet_ids:
        raise ValueError("action actor is outside this walk")
    actor = {"id": pet_id, "name": dict(base.input.pet_names).get(pet_id)}
    value = WritingContext(
        "action",
        {
            "card_id": scene.id,
            "event_at": scene.anchor.event_at.isoformat(),
            "walk_context": _common_context(base),
            "action": {**anchor, "actor": actor},
            **({"movement": movement} if movement else {}),
        },
    )
    return replace(
        value,
        evidence={
            e.id: e.model_dump(mode="json") for e in stamp.evidence if e.role == "scene_movement"
        },
    )


def get_space_context(base, card_id):
    """Read admitted space/environment facts for one card, without acquisition or writing."""
    scene, stamp = _resolve(base, card_id)
    # Motion/actor/notes are not spatial observations. They stay in their source records.
    materials, evidence = [], {}
    for e in stamp.materials():
        if e.part not in {"space", "environment"}:
            continue
        facts = {k: v for k, v in writing_facts(e).items() if k != "retrieved_at"}
        if e.facts.get("source") == "land_cover" and isinstance(facts.get("material"), dict):
            facts["material"] = {
                k: policy.LAND_WORDS.get(v, v) for k, v in facts["material"].items()
            }
        identity = "material:" + digest([e.role, facts])
        materials.append({"id": identity, "role": e.role, "facts": facts})
        evidence[identity] = e.model_dump(mode="json")
    materials.sort(key=lambda material: material["id"])
    known = {e.role for e in stamp.materials()}
    backgrounds = [
        b
        for b in (
            *base.input.source.backgrounds,
            *(base.scene_backgrounds.backgrounds if base.scene_backgrounds else ()),
        )
        if b.target == scene.core_ref
    ]

    def state(providers, available):
        matching = [b for b in backgrounds if b.provider in providers]
        return "known" if available else (matching[-1].status if matching else "unavailable")

    def ground(material):
        relation = material["facts"].get("relation")
        return isinstance(relation, dict) and relation.get("kind") == "land_cover_at_query_point"

    value = WritingContext(
        "space",
        {
            "card_id": scene.id,
            "walk_context": _common_context(base),
            "anchor": scene.anchor.model_dump(mode="json"),
            "sources": {
                "sgis": state({"sgis"}, "scene_address_reference" in known),
                "egis": state(
                    {"public-normalized-land_cover"},
                    any(e.facts.get("source") == "land_cover" for e in stamp.materials()),
                ),
                "environment": "known"
                if any(e.part == "environment" for e in stamp.materials())
                else "unavailable",
            },
            "materials": materials,
            "scene_structure": {
                "current_ground": [m["id"] for m in materials if ground(m)],
                "administrative_location": [
                    m["id"] for m in materials if m["role"] == "scene_address_reference"
                ],
                "local_details": [
                    m["id"]
                    for m in materials
                    if m["role"] != "scene_address_reference" and not ground(m)
                ],
            },
        },
    )
    return replace(value, evidence=evidence)
