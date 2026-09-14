"""Bind supplied route evidence to a diary snapshot before any interpretation."""

from daengs_walk.diary.contracts.input import digest
from daengs_walk.storyboard_input import route_nodes


def verified_route(source, route):
    if route is None:
        return [], (), None
    facts = route.evidence.facts
    if (
        route.version != source.route
        or route.version.status != "ready"
        or str(facts.walk_id) != source.walk_id
        or (facts.started_at, facts.ended_at) != (source.started_at, source.ended_at)
        or facts.calculation_version != source.route.calculation_version
        or facts.evidence_origin != source.evidence_origin
    ):
        raise ValueError("route evidence belongs to another source/version")
    nodes = route_nodes(route.evidence)
    fixes = route.evidence.accepted_points
    # Bind also to the actual canonical material, not only the supplied analysis ID.
    revision = digest(
        {
            "version": route.version.model_dump(mode="json"),
            "nodes": nodes,
            "fixes": [f.model_dump(mode="json") for f in fixes],
        }
    )
    return nodes, fixes, revision
