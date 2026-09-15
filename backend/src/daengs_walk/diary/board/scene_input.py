"""Project an existing stamp into scene context and an optional recorded action."""

from daengs_walk.diary.board.models import RecordCore
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.slots.space import writing_facts

ACTION_MEANINGS = {"sniffing": "냄새 맡기", "excretion": "배변", "barking": "짖기"}
PART_FIELDS = {"space": "where", "motion": "route_pattern", "environment": "environment"}


def action_anchor(scene):
    if not isinstance(scene.core, RecordCore) or scene.core.record.content.kind != "behavior":
        return None
    if scene.core.record.deleted:
        return None
    content = scene.core.record.content
    return {
        "id": "action:" + digest(scene.core_ref),
        "kind": content.code,
        "material": {"무엇을": ACTION_MEANINGS[content.code]},
    }


def preserve_original(scene):
    return isinstance(scene.core, RecordCore) and scene.core.record.content.kind in {
        "note",
        "photo",
    }


def scene_input(scene, stamp):
    context = {field: [] for field in PART_FIELDS.values()}
    preserve = preserve_original(scene)
    for item in stamp.materials():
        if item.facts.get("format") == "diary-movement-material-v1":
            continue  # Only the pin-bound action request may consume the new movement format.
        if preserve and item.part == "motion":
            continue  # User-authored moments receive only spatial/environment supplements.
        context[PART_FIELDS[item.part]].append(
            {"id": item.id, "role": item.role, "facts": writing_facts(item)}
        )
    action = action_anchor(scene)
    if not any(context.values()) and action is None:
        return None
    return {
        "scene_id": scene.id,
        "mode": "preserve_original" if preserve else "scene",
        "scene": context,
        "action": action,
    }


def scene_materials(item):
    return [e for group in item["scene"].values() for e in group]
