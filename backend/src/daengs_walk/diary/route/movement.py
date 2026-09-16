"""Compose verified path shapes and relative pace before any scene or slot clipping."""

from dataclasses import dataclass
from datetime import datetime
from itertools import groupby, pairwise

from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.route.movement_geometry import SHAPE_MEANINGS, movement_shapes
from daengs_walk.diary.route.patterns import RoutePatternBindingPolicy
from daengs_walk.route.nodes import route_nodes
from daengs_walk.route.pace import session_speed_baseline


@dataclass(frozen=True)
class MovementCatalog:
    source_revision: str
    started_at: datetime
    nodes: tuple
    claims: tuple
    baseline: dict
    policies: dict


def pace_claims(nodes, baseline, policy, source_revision):
    """Classify continuous original episodes, never clipped card fragments."""
    groups, active, previous = [], [], None
    for n in nodes:
        speed = n.get("speed")
        kind = (
            "relative_slow"
            if speed is not None and baseline and speed < baseline * policy.slow_ratio
            else "relative_fast"
            if speed is not None and baseline and speed > baseline * policy.fast_ratio
            else None
        )
        key = (n["block"], kind)
        contiguous = active and active[-1]["elapsed_s"] == n.get("start_s")
        if not kind or key != previous or not contiguous:
            if active:
                groups.append(active)
            active = []
        if kind:
            active.append(n)
        previous = key
    if active:
        groups.append(active)
    claims = []
    for group in groups:
        start, end = group[0]["start_s"], group[-1]["elapsed_s"]
        if end - start < policy.minimum_seconds:
            continue
        mean = sum(n["speed"] * n["duration_s"] for n in group) / (end - start)
        value = {
            "kind": "pace",
            "meaning": "relative_slow" if mean < baseline else "relative_fast",
            "block": group[0]["block"],
            "start_s": start,
            "end_s": end,
            "mean_mps": mean,
            "baseline_mps": baseline,
        }
        claims.append(
            {
                "id": "movement-claim:"
                + digest([source_revision, policy.model_dump(mode="json"), value]),
                **value,
            }
        )
    return claims


def prepare_movement(source, route, policy, pattern_policy=None):
    from daengs_walk.diary.route.binding import verified_route

    if route is None:
        return None
    # The existing preparation checks source route identity and canonical quality.
    _, _, route_revision = verified_route(source, route)
    binding = pattern_policy or RoutePatternBindingPolicy()
    shapes, shape_audit, shape_policy = movement_shapes(route.evidence, binding.geometry)
    nodes = route_nodes(route.evidence)
    origin = route.evidence.facts.started_at
    baseline = session_speed_baseline(nodes, minimum_speed=0.5, minimum_samples=5)
    measured = [n for n in nodes if "speed" in n]
    included = [n for n in measured if n["speed"] >= 0.5]
    audit = {
        "policy": policy.baseline,
        "available": baseline is not None,
        "baseline_mps": baseline,
        "included_seconds": sum(n["duration_s"] for n in included),
        "excluded_seconds": sum(n["duration_s"] for n in measured if n["speed"] < 0.5),
        "included_segments": len(included),
        "excluded_segments": [n["observation"]["client_seq"] for n in measured if n["speed"] < 0.5],
    }
    policies = {
        "movement": policy.model_dump(mode="json"),
        "geometry": binding.geometry.model_dump(mode="json"),
        "shape_analysis": shape_policy.model_dump(mode="json"),
        "geometry_dictionary": digest(SHAPE_MEANINGS),
    }
    revision = digest([route_revision, policies])
    audit["shape_quality"] = shape_audit
    claims = pace_claims(nodes, baseline, policy, revision)
    blocks = {block: list(group) for block, group in groupby(nodes, key=lambda n: n["block"])}
    for item in shapes:
        start = (datetime.fromisoformat(item["support"]["started_at"]) - origin).total_seconds()
        end = (datetime.fromisoformat(item["support"]["ended_at"]) - origin).total_seconds()
        matches = [
            block
            for block, points in blocks.items()
            if points[0]["elapsed_s"] <= start <= end <= points[-1]["elapsed_s"]
        ]
        if len(matches) != 1:
            continue
        claims.append(
            {
                "id": "movement-shape:" + digest([revision, item]),
                "kind": "path",
                "meaning": item["meaning"],
                "start_s": start,
                "end_s": end,
                "block": matches[0],
                **(
                    {
                        "event_s": (
                            datetime.fromisoformat(item["anchor"]["at"]) - origin
                        ).total_seconds()
                    }
                    if item["is_event"]
                    else {}
                ),
                # Shape proof remains internal, including retrace correspondence and pivot.
                "proof": item,
            }
        )
    return MovementCatalog(revision, origin, tuple(nodes), tuple(claims), audit, policies)


def phases_for(claims, start, end):
    """A phase contains only claims valid across its whole interval."""
    selected = [c for c in claims if c["start_s"] < end and c["end_s"] > start]
    cuts = sorted(
        {
            start,
            end,
            *(max(start, min(end, c[k])) for c in selected for k in ("start_s", "end_s")),
            *(c["event_s"] for c in selected if "event_s" in c and start < c["event_s"] < end),
        }
    )
    phases = []
    for left, right in pairwise(cuts):
        active = [
            c
            for c in selected
            if c["start_s"] <= left
            and right <= c["end_s"]
            and ("event_s" not in c or left <= c["event_s"] < right or c["event_s"] == right == end)
        ]
        if not active:
            continue
        # More than one verified shape can describe a phase (e.g. retrace + straight).
        phases.append(
            {
                "start_s": left,
                "end_s": right,
                "claims": [c["id"] for c in sorted(active, key=lambda c: c["id"])],
            }
        )
    return phases
