"""Pure brief preparation and delivery; production execution is connected separately."""

from daengs_walk.diary.relational.brief_contracts import (
    BriefDeliveryState,
    DeliveredMeaning,
    SpaceWritingBrief,
)


def build_space_brief(context, delivery=None):
    return SpaceWritingBrief(context=context, delivery=delivery or BriefDeliveryState())


def space_work_reason(brief: SpaceWritingBrief):
    """Planning consumes exactly the context that the writer and delivery will consume."""
    context = brief.context
    if context.flow_ids:
        return "interval_context"
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


def brief_writer_view(brief):
    from .writer_view import writer_view

    return writer_view(brief)
