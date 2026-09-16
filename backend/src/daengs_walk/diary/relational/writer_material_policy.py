"""Writing eligibility only; source relations and their identities remain intact."""

WRITER_POLICY = "single-writing-brief-v7"


def action_context_options(brief):
    """Current terrain and coincident movement only; road introductions belong to space."""
    return tuple(
        c
        for c in brief.context_options
        if c.kind in {"current_gait", "current_shape"}
        or (c.kind == "space" and c.evidence.meaning.kind == "land_cover")
    )


def hidden_flow_ids(context):
    selection = context.interval_relations
    return {f.id for f in selection.flows if f.case == "route_retrace"} if selection else set()


def writer_reference_ids(brief, policy=WRITER_POLICY):
    context = getattr(brief, "context", None)
    hidden = (
        hidden_flow_ids(context)
        if context and policy in {"single-writing-brief-v6", WRITER_POLICY}
        else set()
    )
    if context is None and policy == WRITER_POLICY:
        hidden.update(
            c.evidence.id for c in brief.context_options if c not in action_context_options(brief)
        )
    return (
        tuple(i for i in brief.citation_ids if i not in hidden),
        tuple(i for i in getattr(brief, "relation_ids", ()) if i not in hidden),
    )


def omit_retrace(view, hidden):
    """Filter known material fields, including previously adopted spatial memory."""
    for key in ("citation_ids", "relation_ids"):
        if key in view:
            view[key] = [i for i in view[key] if i not in hidden]
    for container, key in [
        (view, "journey_relations"),
        *[(m, "selected_journey_relations") for m in view.get("delivery_memory", [])],
    ]:
        if key in container:
            rows = [
                r
                for r in container[key]
                if r["id"] not in hidden and r.get("relationship") != "retracing"
            ]
            if rows:
                container[key] = rows
            else:
                container.pop(key)
    return view
