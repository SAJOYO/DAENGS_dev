"""Single model-facing boundary for current briefs and accepted spatial memory."""

from daengs_walk.diary.relational.brief_contracts import ActionWritingBrief
from daengs_walk.diary.relational.relation_delivery import flow_view
from daengs_walk.diary.relational.writer_meaning import (
    anchor_view,
    connection_view,
    event_context_view,
    event_view,
    fact_view,
    position_view,
    present,
    relation_view,
    route_view,
    walk_view,
)

WRITER_POLICY = "single-writing-brief-v4"


def memory_view(selection, *, legacy_v2=False):
    context = selection.context
    relations = [
        r for r in context.relation_slots.all_relations() if r.id in selection.relation_ids
    ]
    ids = set(selection.evidence_ids)
    for relation in relations:
        ids.update(relation.earlier_evidence_ids + relation.current_evidence_ids)
    facts = [f for f in context.facts if f.id in ids]
    scenes = {f.scene_id for f in facts}
    return present(
        selected_in_scene=context.current.position.scene_id,
        anchors=[
            anchor_view(a)
            for a in (context.earlier, context.current)
            if a and a.position.scene_id in scenes
        ],
        selected_facts=[fact_view(f) for f in facts],
        selected_relations=[relation_view(r, legacy_v2=legacy_v2) for r in relations],
        selected_route=route_view(context.route)
        if context.route and context.route.id in ids
        else None,
        selected_journey_relations=[
            flow_view(f)
            for f in context.interval_relations.flows
            if f.id in ids or f.id in selection.relation_ids
        ]
        if context.interval_relations
        else None,
    )


def writer_view(brief, *, legacy_v2=False):
    common = {
        "version": brief.version,
        "part": brief.part,
        "style": {"language": "ko", "tense": "past", "genre": "walk_diary"},
        "citation_ids": list(brief.citation_ids),
    }
    if isinstance(brief, ActionWritingBrief):
        return {
            **common,
            "walk": walk_view(brief.position),
            "position": position_view(brief.position),
            "required_event": event_view(brief.required_event),
            "required_evidence_ids": [brief.required_event.id],
            **present(context_options=[event_context_view(c) for c in brief.context_options]),
        }
    context = brief.context
    return {
        **common,
        "walk": walk_view(context.current.position),
        "current": anchor_view(context.current),
        "available_facts": [fact_view(f) for f in context.facts],
        "relation_ids": list(brief.relation_ids),
        **present(
            earlier=anchor_view(context.earlier) if context.earlier else None,
            relation_slots={
                name: [
                    relation_view(r, legacy_v2=legacy_v2)
                    for r in getattr(context.relation_slots, name)
                    if r.id in brief.relation_ids
                ]
                for name in ("background", "proximity", "area_context")
                if any(r.id in brief.relation_ids for r in getattr(context.relation_slots, name))
            },
            connection=connection_view(context.connection),
            route=route_view(context.route),
            journey_relations=[flow_view(f) for f in context.interval_relations.flows]
            if context.interval_relations
            else None,
            delivery_memory=[memory_view(m, legacy_v2=legacy_v2) for m in brief.delivery.recent],
        ),
    }


def publication_writer_view(brief, policy):
    """Historical contexts without interval material retain the identical projection."""
    if policy == WRITER_POLICY:
        return writer_view(brief)
    if getattr(brief, "context", None) and any(
        context.interval_relations is not None
        for context in (brief.context, *(m.context for m in brief.delivery.recent))
    ):
        raise ValueError("interval material requires writer policy v4")
    if policy == "single-writing-brief-v3":
        return writer_view(brief)
    if policy == "single-writing-brief-v2":
        return writer_view(brief, legacy_v2=True)
    if policy == "single-writing-brief-v1":
        from .legacy_writer_view import brief_writer_view

        return brief_writer_view(brief)
    raise ValueError("unsupported writer meaning policy")
