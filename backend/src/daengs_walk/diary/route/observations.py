"""Project verified canonical motion into records-first diary materials, without action meaning."""

from dataclasses import dataclass
from datetime import timedelta

from daengs_walk.diary.contracts.input import Anchor, MovementObservation, RouteVersion, digest
from daengs_walk.evidence import WalkEvidenceBundle
from daengs_walk.facts import MIN_STOP_S
from daengs_walk.route.nodes import route_nodes
from daengs_walk.route.pace import PacePolicy, movement_candidates, session_speed_baseline

OBSERVATION_POLICY_VERSION = "diary-canonical-motion-v1"
MAX_OBSERVATIONS = 200
# This versioned observation pool owns its criteria, independently of storyboard.
OBSERVATION_PACE = PacePolicy(slow_ratio=0.5, fast_ratio=1.75, minimum_seconds=20)


@dataclass(frozen=True)
class ObservationPool:
    observations: tuple[MovementObservation, ...]
    total_candidates: int
    omitted_at_capacity: int
    baseline_mps: float | None
    policy_version: str = OBSERVATION_POLICY_VERSION


def _stop_runs(segments):
    """Recover source support for the existing stop events; never use their mean coordinate."""
    runs, run = [], []
    for segment in segments:
        if run and (
            segment.moving
            or segment.chain_index != run[-1].chain_index
            or segment.a.client_seq != run[-1].b.client_seq
        ):
            if sum(s.dt for s in run) >= MIN_STOP_S:
                runs.append(run)
            run = []
        if not segment.moving:
            run.append(segment)
    if run and sum(s.dt for s in run) >= MIN_STOP_S:
        runs.append(run)
    return runs


def build_observation_pool(evidence: WalkEvidenceBundle, route: RouteVersion) -> ObservationPool:
    if route.status != "ready" or route.calculation_version != evidence.facts.calculation_version:
        raise ValueError("observations require the matching finalized calculation")
    candidates = []

    def add(kind, started_at, ended_at, points, anchor_fix, measurement):
        anchor = Anchor(
            event_at=anchor_fix.at,
            time_basis="route_observation",
            point={"lat": anchor_fix.lat, "lng": anchor_fix.lng},
            location_at=anchor_fix.at,
            accuracy_m=anchor_fix.accuracy_m,
            position_state="resolved",
            method="observed",
            source_fixes=(
                {
                    "client_seq": anchor_fix.client_seq,
                    "chain_index": anchor_fix.chain_index,
                    "at": anchor_fix.at,
                },
            ),
        )
        identity = {
            "analysis_id": route.analysis_id,
            "kind": kind,
            "first_seq": points[0].client_seq,
            "last_seq": points[-1].client_seq,
        }
        version = digest(
            {
                "policy": OBSERVATION_POLICY_VERSION,
                "route": route.model_dump(mode="json"),
                "identity": identity,
                "support": [p.model_dump(mode="json") for p in points],
                "measurement": measurement,
                "anchor": anchor.model_dump(mode="json"),
            }
        )
        candidates.append(
            MovementObservation(
                id="motion:" + digest(identity),
                version=version,
                analysis_id=route.analysis_id,
                kind=kind,
                started_at=started_at,
                ended_at=ended_at,
                anchor=anchor,
            )
        )

    stops = _stop_runs(evidence.segments)
    if len(stops) != len(evidence.events):
        raise ValueError("stored stop events differ from their canonical support")
    for event, run in zip(evidence.events, stops, strict=True):
        points = [run[0].a, *(segment.b for segment in run)]
        if (event.started_at, event.ended_at, event.fix_count) != (
            points[0].at,
            points[-1].at,
            len(points),
        ):
            raise ValueError("stop event does not match its source interval")
        middle = event.started_at + (event.ended_at - event.started_at) / 2
        fix = min(points, key=lambda p: (abs((p.at - middle).total_seconds()), p.client_seq))
        add(
            "observed_dwell",
            event.started_at,
            event.ended_at,
            points,
            fix,
            {"event": event.model_dump(mode="json")},
        )

    nodes = route_nodes(evidence)
    baseline = session_speed_baseline(nodes, minimum_speed=0.5, minimum_samples=5)
    fixes = {p.client_seq: p for p in evidence.accepted_points}
    blocks = {}
    for node in nodes:
        blocks.setdefault(node["block"], []).append(node)
    for candidate in movement_candidates(nodes, baseline, "session_speed", policy=OBSERVATION_PACE):
        movement = candidate["movement"]
        support = [
            n
            for n in blocks[candidate["block"]]
            if movement["start_s"] <= n["elapsed_s"] <= movement["end_s"]
        ]
        points = [fixes[n["observation"]["client_seq"]] for n in support]
        fix = fixes[candidate["observation"]["client_seq"]]
        kind = "observed_slow" if movement["mean_mps"] < baseline else "observed_fast"
        add(
            kind,
            evidence.facts.started_at + timedelta(seconds=movement["start_s"]),
            evidence.facts.started_at + timedelta(seconds=movement["end_s"]),
            points,
            fix,
            movement,
        )

    # Match the consumer's admission priority before bounding the input pool.
    candidates.sort(
        key=lambda o: (
            o.kind != "observed_dwell",
            -(o.ended_at - o.started_at).total_seconds(),
            o.anchor.event_at,
            o.id,
        )
    )
    admitted = candidates[:MAX_OBSERVATIONS]
    return ObservationPool(
        observations=tuple(sorted(admitted, key=lambda o: (o.anchor.event_at, o.id))),
        total_candidates=len(candidates),
        omitted_at_capacity=max(0, len(candidates) - MAX_OBSERVATIONS),
        baseline_mps=baseline,
    )
