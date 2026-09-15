"""Pure brief preparation and delivery; production execution is connected separately."""

from daengs_walk.diary.relational.brief_contracts import (
    ActionWritingBrief,
    BriefDeliveryState,
    DeliveredMeaning,
    SpaceWritingBrief,
)


def build_space_brief(context, delivery=None):
    return SpaceWritingBrief(context=context, delivery=delivery or BriefDeliveryState())


def space_work_reason(brief: SpaceWritingBrief):
    """Planning consumes exactly the context that the writer and delivery will consume."""
    context = brief.context
    if not context.current_facts:
        return "unavailable"
    if context.earlier is None:
        return "introduce"
    if any(
        r.result in {"different_characteristics", "nearer", "farther"}
        for r in context.relation_slots.all_relations()
    ):
        return "compare"
    earlier_signature = context.signature_for(context.earlier.position.scene_id)
    if earlier_signature != context.signature:
        # New current evidence can be described, but its arrival is not a spatial event.
        return "current_context"
    if brief.delivery.active_signature != context.signature:
        return "recover_introduction"
    return "maintain"


def advance_brief_delivery(brief: SpaceWritingBrief, selection: DeliveredMeaning | None):
    """Call only for an accepted selection; failure leaves no fabricated delivery."""
    state, context = brief.delivery, brief.context
    active = state.active_signature if state.active_signature == context.signature else None
    recent = state.recent
    if selection is not None:
        if selection.context != context:
            raise ValueError("accepted selection belongs to a different request")
        cited = set(selection.evidence_ids)
        for relation in context.relation_slots.all_relations():
            if relation.id in selection.relation_ids:
                cited.update(relation.current_evidence_ids)
        if cited & {f.id for f in context.current_facts}:
            active = context.signature
        recent = (*recent, selection)[-2:]
    return BriefDeliveryState(active_signature=active, recent=recent)


def _anchor_view(anchor):
    return {
        **anchor.model_dump(mode="json", exclude={"position"}),
        "position": anchor.position.writer_view(),
    }


def _memory_view(selection):
    context = selection.context
    relations = [
        r for r in context.relation_slots.all_relations() if r.id in selection.relation_ids
    ]
    ids = set(selection.evidence_ids)
    for relation in relations:
        ids.update(relation.earlier_evidence_ids + relation.current_evidence_ids)
    facts = [f for f in context.facts if f.id in ids]
    scenes = {f.scene_id for f in facts}
    return {
        "selected_in_scene": context.current.position.scene_id,
        "semantic_status": selection.semantic_status,
        "anchors": [
            _anchor_view(a)
            for a in (context.earlier, context.current)
            if a and a.position.scene_id in scenes
        ],
        "selected_facts": [f.model_dump(mode="json") for f in facts],
        "selected_relations": [r.model_dump(mode="json") for r in relations],
        "selected_route": context.route.model_dump(mode="json")
        if context.route and context.route.id in ids
        else None,
    }


def brief_writer_view(brief: SpaceWritingBrief | ActionWritingBrief):
    """Explicit allowlist: source bindings, registration counters, narrator lists stay internal."""
    common = {
        "version": brief.version,
        "part": brief.part,
        "style": {"language": "ko", "tense": "past", "genre": "walk_diary"},
        "citation_ids": list(brief.citation_ids),
    }
    if isinstance(brief, ActionWritingBrief):
        return {
            **common,
            "position": brief.position.writer_view(),
            "required_event": brief.required_event.model_dump(
                mode="json", exclude={"source_record"}
            ),
            "required_evidence_ids": [brief.required_event.id],
            "context_options": [c.model_dump(mode="json") for c in brief.context_options],
        }
    context = brief.context
    return {
        **common,
        "current": _anchor_view(context.current),
        "earlier": _anchor_view(context.earlier) if context.earlier else None,
        "available_facts": [f.model_dump(mode="json") for f in context.facts],
        "relation_slots": context.relation_slots.model_dump(mode="json"),
        "relation_ids": list(brief.relation_ids),
        "connection": context.connection.model_dump(mode="json") if context.connection else None,
        "route": context.route.model_dump(mode="json") if context.route else None,
        "delivery_memory": [_memory_view(m) for m in brief.delivery.recent],
    }
