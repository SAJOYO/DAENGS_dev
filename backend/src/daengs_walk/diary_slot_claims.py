"""Resolve claim identity before ranking. Retrieval recency is not source authority."""

from collections import defaultdict

from daengs_walk.diary_input import digest
from daengs_walk.diary_slots import SlotDecision

# Different meanings do not compete on distance/radius. One item per nonempty queue
# per turn, then repeat; no category quota and no empty reserved positions.
SPATIAL_ORDER = (
    "scene_registered_point_distance",
    "scene_geometry_distance",
    "scene_area_context",
)


def spatial_order(items):
    queues = [
        sorted((c for c in items if c.role == role), key=lambda c: (c.rank, c.id))
        for role in SPATIAL_ORDER
    ]
    return [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]


def normalize(value):
    """JSON numeric spelling (40 vs 40.0) is not a distinct assertion."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def resolve_claims(candidates, decisions):
    groups = defaultdict(list)
    for item in candidates:
        groups[(item.entity_key, item.claim_scope)].append(item)
    resolved = []
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda c: c.id)
        if len({c.claim_key for c in group}) > 1:
            # None of these adapters supplies a comparable authoritative revision.
            # Do not choose a value by proximity, fetch time or input order.
            details = {
                "competing_claims": [
                    {"evidence_id": c.id, "source_id": c.source_id, "facts": c.facts} for c in group
                ]
            }
            for item in group:
                decisions.append(
                    SlotDecision(
                        source_id=item.source_id,
                        evidence_id=item.id,
                        part=item.part,
                        eligibility="unknown",
                        admission="conflict",
                        reason="conflicting_claims",
                        details=details,
                    )
                )
            continue
        first = group[0]
        sources = {(s.source_id, s.source_version): s for c in group for s in c.sources}
        sources = tuple(sources[k] for k in sorted(sources))
        merged = first.model_copy(
            update={
                "id": "slot:"
                + digest(
                    {
                        "claim": first.claim_key,
                        "sources": [s.model_dump(mode="json") for s in sources],
                    }
                ),
                "sources": sources,
            }
        )
        resolved.append(merged)
        for item in group[1:]:
            decisions.append(
                SlotDecision(
                    source_id=item.source_id,
                    evidence_id=item.id,
                    part=item.part,
                    eligibility="pass",
                    admission="duplicate",
                    reason="same_claim",
                    details={"merged_into": merged.id},
                )
            )
    return resolved
