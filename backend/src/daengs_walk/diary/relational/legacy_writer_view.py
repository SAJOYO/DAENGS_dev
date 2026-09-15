"""Frozen v1 request projection, used only to validate historical publications."""

from daengs_walk.diary.relational.brief_contracts import ActionWritingBrief, SpaceWritingBrief


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
