"""Derive independent model and UI views from the same committed state."""

from daengs_place.place.conversation.compiler import fingerprint


def places(state):
    if state.result is None:
        return []
    items = [h.place for g in state.result.groups for h in g.matched]
    items.sort(
        key=lambda p: (
            0 if state.result.applied_state.preferences and p.facts.parking is True else 1,
            p.distance_m,
            p.key.source,
            p.key.ref,
        )
    )
    return items


def references(state):
    return {f"p{state.snapshot_id.hex[:16]}-{i}": p for i, p in enumerate(places(state), 1)}


def filter_view(filters):
    def condition(atom):
        return {"attribute": atom.capability, "value": atom.value, "op": atom.op}

    return {
        "kinds": list(filters.candidate_kinds),
        "radius_m": filters.spatial.radius_m,
        "name_query": filters.name_query,
        "required": [condition(a) for a in filters.hard.all],
        "any_of": [[condition(a) for a in branch.all] for branch in filters.hard.any],
        "preferred": [{**condition(a), "kinds": list(a.scope_kinds)} for a in filters.preferences],
    }


def context(state):
    refs = references(state)
    return {
        "filters": filter_view(state.filters),
        "scope": "현재 검색 중심 기준 반경",
        "visible_places": [
            {"ref": ref, "name": p.name, "kind": p.match.kind, "distance_m": p.distance_m}
            for ref, p in refs.items()
        ],
        "selected_ref": next((r for r, p in refs.items() if p.key == state.selected), None),
        "results_match_filters": bool(
            state.result and fingerprint(state.result.applied_state) == fingerprint(state.filters)
        ),
        "excluded_places": [{"ref": p.ref, "name": p.name} for p in state.excluded],
        "known_places": [{"ref": p.ref, "name": p.name} for p in state.known],
        "pending_proposal": {
            "filters": filter_view(state.proposal.candidate),
            "unavailable": list(state.proposal.unavailable),
            "apply_to": state.proposal.apply_to,
        }
        if state.proposal
        else None,
    }


def ui_view(state):
    return {
        **context(state),
        "revision": state.revision,
        "snapshot_id": str(state.snapshot_id) if state.snapshot_id else None,
        "cards": [{"ref": r, **p.model_dump(mode="json")} for r, p in references(state).items()],
    }


def state_changes(before, after):
    left, right = filter_view(before.filters), filter_view(after.filters)
    changes = {k: {"before": left[k], "after": right[k]} for k in left if left[k] != right[k]}
    if before.selected != after.selected:
        changes["selection"] = {
            "before": context(before)["selected_ref"],
            "after": context(after)["selected_ref"],
        }
    if before.excluded != after.excluded:
        changes["excluded"] = {"before": len(before.excluded), "after": len(after.excluded)}
    if before.known != after.known:
        changes["known"] = {"before": len(before.known), "after": len(after.known)}
    return changes


def tool_result(result):
    return {
        "status": result.status,
        "code": result.code,
        "changes": result.changes,
        "current": context(result.state),
        **result.data,
    }
