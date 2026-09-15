"""Model vocabulary and shape only. Cannot calculate or choose relation cases."""

from copy import deepcopy

from .relation_vocabulary import FLOW_WORDS
from .writer_meaning import present

DELIVERY_POLICY = "relation-delivery-v1"


def flow_view(flow):
    word = FLOW_WORDS[flow.case]
    result = {
        "id": flow.id,
        "relationship": word,
        "interval": {
            "started_at": flow.started_at.isoformat(),
            "ended_at": flow.ended_at.isoformat(),
        },
    }
    if flow.target_id:
        result["target"] = {"name": flow.target_name, "scope": flow.target_scope}
        samples = flow.profile
        indices = sorted(
            {0, len(samples) - 1, min(range(len(samples)), key=lambda i: samples[i].distance_m)}
        )
        result["distance_profile"] = [
            {
                "at": samples[i].at.isoformat(),
                "meters": round(samples[i].distance_m, 3),
                "accuracy_m": samples[i].accuracy_m,
            }
            for i in indices
        ]
        result["sample_count"] = len(flow.source_ids)
    elif flow.interval_view:
        # An already projected interval supplies scope/uncertainty; internal kind
        # is replaced here, never put alongside the narrative vocabulary.
        result.update(
            {k: deepcopy(v) for k, v in flow.interval_view.items() if k not in {"id", "kind"}}
        )
    return result


def deliver_relations(brief, selection, *, memory=()):
    from .writer_view import writer_view

    if brief.context.current.position.scene_id != selection.scene_id:
        raise ValueError("selection belongs to another scene")
    request = writer_view(brief)
    allowed = set(selection.spatial_relation_ids)
    all_old = set(brief.relation_ids)
    if allowed | set(selection.replaced_relation_ids) != all_old:
        raise ValueError("selection must account for every base relation")
    slots = {}
    for name, rows in request.pop("relation_slots", {}).items():
        items = []
        for row in rows:
            if row["id"] not in allowed:
                continue
            # Production writer_view already applies the shared vocabulary.
            items.append(deepcopy(row))
        if items:
            slots[name] = items
    flows = [flow_view(f) for f in selection.flows]
    request.update(present(relation_slots=slots, journey_relations=flows))
    request["relation_ids"] = list(selection.spatial_relation_ids) + [f.id for f in selection.flows]
    request["citation_ids"] = [i for i in request["citation_ids"] if i not in all_old] + request[
        "relation_ids"
    ]
    request.pop("delivery_memory", None)
    if memory:
        request["delivery_memory"] = deepcopy(list(memory))
    return request
