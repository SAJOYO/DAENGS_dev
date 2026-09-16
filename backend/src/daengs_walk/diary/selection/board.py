"""Keep records/observations, then cover real route gaps; boundaries are extra cards."""

from datetime import datetime

from daengs_walk.diary.board.models import (
    BaseBoardPolicy,
    BoardStamp,
    BoundaryCore,
    CheckpointCore,
    ObservationCore,
    PreparedBaseBoard,
    RecordCore,
    VerifiedBoardRoute,
    core_anchor,
)
from daengs_walk.diary.contracts.input import (
    Anchor,
    DiaryInput,
    MaterialRef,
    UserRecord,
    digest,
    material_ref,
)
from daengs_walk.diary.route.binding import verified_route
from daengs_walk.diary.selection.stamps import prepare_stamps
from daengs_walk.route.geometry import distance, uncovered


def observed_anchor(fix):
    return Anchor(
        event_at=fix.at,
        time_basis="route_observation",
        point={"lat": fix.lat, "lng": fix.lng},
        location_at=fix.at,
        accuracy_m=fix.accuracy_m,
        position_state="resolved",
        method="observed",
        source_fixes=[{"client_seq": fix.client_seq, "chain_index": fix.chain_index, "at": fix.at}],
    )


def _stamp(core, identity):
    ref = MaterialRef(identity=identity, version=digest(core))
    return BoardStamp(id="stamp:" + digest(identity), core_ref=ref, core=core)


def _boundary(source, fixes, which):
    at = source.started_at if which == "start" else source.ended_at
    matches = [fix for fix in fixes if fix.at == at]
    # Conflicting observations at exactly the same time cannot locate a boundary uniquely.
    fix = matches[0] if len(matches) == 1 else None
    anchor = (
        observed_anchor(fix)
        if fix
        else Anchor(
            event_at=at,
            time_basis="session_fallback",
            point=None,
            location_at=None,
            position_state="unlocated",
            method="none",
        )
    )
    core = BoundaryCore(boundary=which, anchor=anchor)
    return _stamp(core, "boundary:" + digest([source.client_session_id, which]))


def _cover_anchor(anchor, blocks, tolerance):
    """Map only coverage bookkeeping. Never replace the original anchor with this node.

    Time chooses a continuous visit before spatial proximity is checked. The same
    coordinate on a later lap, a stale last-known fix or a GPS gap is not that visit.
    """
    if anchor.point is None or anchor.position_state == "provisional":
        return None
    at = anchor.location_at if anchor.method == "last_known" else anchor.event_at
    for nodes in blocks.values():
        if not nodes[0]["at"] <= at <= nodes[-1]["at"]:
            continue
        node = min(nodes, key=lambda n: (abs((n["at"] - at).total_seconds()), n["route_m"]))
        location = node["location"]
        if (
            distance((anchor.point.lat, anchor.point.lng), (location["lat"], location["lng"]))
            <= tolerance
        ):
            return node
    return None


def _fill(source, nodes, fixes, existing, policy, deficit):
    if not deficit or not nodes:
        return []
    by_fix = {(f.chain_index, f.client_seq, f.at): f for f in fixes}
    blocks = {}
    for node in nodes:
        node["at"] = datetime.fromisoformat(node["observation"]["at"])
        blocks.setdefault(node["block"], []).append(node)
    covered = [
        node
        for stamp in existing
        if (node := _cover_anchor(core_anchor(stamp.core), blocks, policy.record_route_tolerance_m))
        is not None
    ]
    candidates = [n for n in nodes if source.started_at < n["at"] < source.ended_at]
    selected = []
    while len(selected) < deficit:
        chosen = None
        for gap in uncovered(nodes, covered, policy.separation_m):
            available = [
                n
                for n in candidates
                if n["block"] == gap["block"]
                and gap["start_m"] <= n["route_m"] <= gap["end_m"]
                and all(
                    n["block"] != c["block"]
                    or abs(n["route_m"] - c["route_m"]) >= policy.separation_m
                    for c in covered
                )
            ]
            if available:
                middle = (gap["start_m"] + gap["end_m"]) / 2
                chosen = min(
                    available,
                    key=lambda n: (
                        abs(n["route_m"] - middle),
                        n["at"],
                        n["observation"]["client_seq"],
                    ),
                )
                break
        if chosen is None:
            break  # Short/sparse routes can remain below the intermediate target.
        observed = chosen["observation"]
        fix = by_fix[(observed["chain_index"], observed["client_seq"], chosen["at"])]
        core = CheckpointCore(
            route=source.route,
            block=chosen["block"],
            route_m=chosen["route_m"],
            anchor=observed_anchor(fix),
        )
        identity = "checkpoint:" + digest(
            [source.client_session_id, fix.chain_index, fix.client_seq, fix.at.isoformat()]
        )
        selected.append(_stamp(core, identity))
        covered.append(chosen)
    return selected


def prepare_base_board(
    source: DiaryInput,
    policy: BaseBoardPolicy,
    *,
    route: VerifiedBoardRoute | None = None,
) -> PreparedBaseBoard:
    source = DiaryInput.model_validate(source.model_dump(mode="json"))
    policy = BaseBoardPolicy.model_validate(policy.model_dump(mode="json"))
    intermediate = prepare_stamps(source, policy.intermediate)
    nodes, fixes, route_revision = verified_route(source, route)
    materials = {material_ref(m).identity: m for m in (*source.records, *source.observations)}
    stamps = []
    for stamp in intermediate.plan.scenes:
        material = materials[stamp.core.identity]
        core = (
            RecordCore(record=material)
            if isinstance(material, UserRecord)
            else ObservationCore(observation=material)
        )
        # Preserve the current record/observation IDs and version binding exactly.
        stamps.append(
            BoardStamp(id=stamp.id, core_ref=stamp.core, core=core, background=stamp.background)
        )
    start, end = _boundary(source, fixes, "start"), _boundary(source, fixes, "end")
    additions = _fill(
        source,
        nodes,
        fixes,
        [start, *stamps, end],
        policy,
        intermediate.counts["remaining_deficit"],
    )
    middle = sorted(
        [*stamps, *additions], key=lambda s: (core_anchor(s.core).event_at, s.core_ref.identity)
    )
    limits = list(intermediate.limits)
    if route is None:
        limits.append("canonical_route_not_supplied")
    remaining = max(0, policy.intermediate.target_scene_count - len(middle))
    if remaining:
        limits.append("intermediate_target_not_reached")
    return PreparedBaseBoard(
        input_revision=source.revision(),
        route_revision=route_revision,
        policy=policy,
        intermediate=intermediate,
        stamps=(start, *middle, end),
        counts={
            **intermediate.counts,
            "checkpoints": len(additions),
            "boundaries": 2,
            "remaining_deficit": remaining,
            "intermediate_total": len(middle),
            "total": len(middle) + 2,
        },
        limits=tuple(limits),
    )
