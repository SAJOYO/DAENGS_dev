"""Actual GPS + retained target identity -> interval candidates -> selection.

No fixture targets, extra provider requests, or narrative instructions.
"""

from collections import defaultdict

from daengs_walk.diary.relational.relation_flow_analysis import distance_flow
from daengs_walk.diary.relational.relation_flow_contracts import DistanceSample, DistanceTrack
from daengs_walk.diary.relational.relation_injection import select_relations
from daengs_walk.route.geometry import distance
from daengs_walk.value_contracts import Point, digest

from .interval_sources import continuous_points


def target_tracks(sources, frame, previous):
    targets = defaultdict(list)
    for record in (previous, frame):
        if record:
            for fact in record["scene_snapshot"]["facts"]:
                if fact["family"] == "surrounding_object" and fact.get("subject_key"):
                    targets[fact["subject_key"]].append(fact)
    fixes = tuple(continuous_points(sources))
    for identity, facts in sorted(targets.items()):
        versions = {
            digest([f["value"].get("registered_point"), f.get("reference_date")]) for f in facts
        }
        if len(versions) != 1 or not facts[-1]["value"].get("registered_point"):
            continue  # A changed catalog coordinate is not movement by the walker.
        value = facts[-1]["value"]
        target = Point.model_validate(value["registered_point"])
        version = digest(
            [
                identity,
                target.model_dump(mode="json"),
                facts[-1].get("reference_date"),
                sorted({r for f in facts for r in f["source_refs"]}),
            ]
        )
        yield DistanceTrack(
            identity,
            value["name"],
            "reference_point",
            tuple(
                DistanceSample(
                    p.at,
                    distance((p.lat, p.lng), (target.lat, target.lng)),
                    p.accuracy_m,
                    "fix:" + digest([sources.route_revision, p.model_dump(mode="json")]),
                    block,
                )
                for p, block in fixes
            ),
            version,
        )


def attach_interval_materials(context, sources, frame, previous):
    """Called during preparation and once for source consistency before writing."""
    from .interval_route_materials import route_candidates

    timeline = context.current.position.timeline
    if (sources.source_revision, sources.started_at, sources.ended_at) != (
        timeline.source_revision,
        timeline.started_at,
        timeline.ended_at,
    ):
        raise ValueError("interval sources belong to another walk")
    candidates = []
    if context.earlier:
        start, end = context.earlier.position.recorded_at, context.current.position.recorded_at
        for track in target_tracks(sources, frame, previous):
            window = [s for s in track.samples if start <= s.at <= end]
            if len(window) < 3:
                continue
            # Use only observed support. No interpolation to a pin's timestamp.
            flow = distance_flow(track, window[0].at, window[-1].at, policy=sources.flow_policy)
            if flow:
                candidates.append(flow)
        candidates.extend(route_candidates(sources, context))
    selection = select_relations(context, candidates)
    # Retain a versioned empty selection as evidence this production path ran.
    return type(context).model_validate(
        {
            **context.model_dump(),
            "interval_relations": selection,
        }
    )
