"""Reuse whole, source-bound route claims without importing previous dog events."""

from dataclasses import replace
from datetime import timedelta

from daengs_walk.diary.relational.interval_writer_view import interval_writer_view
from daengs_walk.diary.relational.relation_flow_analysis import route_flow
from daengs_walk.diary.selection.board import observed_anchor
from daengs_walk.route.geometry import distance
from daengs_walk.value_contracts import digest

from .interval_sources import continuous_points


def route_candidates(sources, context):
    now = context.current.position.recorded_at
    for claim in sources.claims:
        # Turning remains current-action context. No pace/dog inference here.
        if claim["meaning"] not in {"retrace", "straight_run"}:
            continue
        proof = claim.get("proof", {})
        if proof.get("status") not in {"candidate", "confirmed", "verified"}:
            continue
        start = sources.started_at + timedelta(seconds=claim["start_s"])
        end = sources.started_at + timedelta(seconds=claim["end_s"])
        if not sources.started_at <= start < end <= now:
            continue  # An event's future supporting observations are not available yet.
        support = proof.get("support", {})
        window = [(p, b) for p, b in continuous_points(sources) if start <= p.at <= end]
        if (
            len(window) < 2
            or len({b for _, b in window}) != 1
            or window[0][0].at != start
            or window[-1][0].at != end
            or window[0][0].client_seq != support.get("from_seq")
            or window[-1][0].client_seq != support.get("to_seq")
            or window[0][0].chain_index != support.get("chain_index")
        ):
            continue
        event = (
            (sources.started_at + timedelta(seconds=claim["event_s"]))
            if "event_s" in claim
            else None
        )
        row = {
            "id": "segment:" + digest([sources.movement_revision, claim]),
            "kind": claim["meaning"],
            "support_started_at": start.isoformat(),
            "support_ended_at": end.isoformat(),
            "representative_event_at": event.isoformat() if event else None,
            "event_time_basis": "detector_estimate" if event else None,
            "source_status": proof["status"],
        }
        if claim["meaning"] == "retrace":
            row["prior_path"] = {
                k: proof["metrics"][k]
                for k in ("past_from_seq", "past_to_seq", "matched_reverse_m")
            }
        flow = route_flow(row, interval_writer_view(row))
        if flow:
            yield replace(flow, source_ids=(claim["id"],), source_version=sources.movement_revision)
    returned = return_candidate(sources, now)
    if returned:
        yield returned


def return_candidate(sources, now):
    points = tuple(continuous_points(sources))
    if now != sources.ended_at or len(points) < 3:
        return None
    first, last = points[0][0], points[-1][0]
    if (
        first.at != sources.started_at
        or last.at != sources.ended_at
        or len({block for _, block in points}) != 1
    ):
        return None
    tolerance = max(1, first.accuracy_m + last.accuracy_m)
    origin = (first.lat, first.lng)
    gap = distance(origin, (last.lat, last.lng))
    if gap > tolerance or max(distance(origin, (p.lat, p.lng)) for p, _ in points) <= 2 * tolerance:
        return None  # Staying near the start is not leaving and returning.
    row = {
        "id": "segment:"
        + digest(
            [
                sources.route_revision,
                "return",
                first.model_dump(mode="json"),
                last.model_dump(mode="json"),
            ]
        ),
        "kind": "end_near_start",
        "distance_m": gap,
        "tolerance_m": tolerance,
        "start_anchor": observed_anchor(first).model_dump(mode="json"),
        "end_anchor": observed_anchor(last).model_dump(mode="json"),
    }
    return replace(
        route_flow(row, interval_writer_view(row), connected_return=True),
        source_ids=tuple(
            "fix:" + digest([sources.route_revision, p.model_dump(mode="json")])
            for p in (first, last)
        ),
        source_version=sources.route_revision,
    )
