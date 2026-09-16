"""Select applicable information. This module has no model-facing vocabulary."""

from .relation_flow_contracts import RelationSelection


def occurrence_at(flow):
    from datetime import datetime

    event = (flow.interval_view or {}).get("representative_event_at")
    return datetime.fromisoformat(event) if event else flow.ended_at


def select_relations(context, candidates):
    now = context.current.position.recorded_at
    previous = context.earlier.position.recorded_at if context.earlier else now
    selected = tuple(f for f in candidates if previous < occurrence_at(f) <= now)
    if len({f.id for f in selected}) != len(selected):
        raise ValueError("duplicate selected relation")
    target_ids = {
        f.target_id
        for f in selected
        if f.target_id and f.started_at <= previous and f.ended_at >= now
    }
    replaced, retained = [], []
    for relation in context.relation_slots.all_relations():
        identities = {
            context.object_identities.get(i)
            for i in relation.current_evidence_ids + relation.earlier_evidence_ids
        }
        if (
            relation.family == "surrounding_object"
            and identities - {None} <= target_ids
            and None not in identities
        ):
            replaced.append(relation.id)
        else:
            retained.append(relation.id)
    return RelationSelection(
        context.current.position.scene_id,
        selected,
        tuple(retained),
        tuple(replaced),
    )
