"""Derive supported flow cases. No writer words, prompts, or scene salience rules."""

from dataclasses import asdict
from itertools import accumulate, pairwise
from math import isfinite

from daengs_walk.value_contracts import digest

from .relation_flow_contracts import FlowPolicy, RelationFlow


def distance_flow(track, started_at, ended_at, *, policy=None):
    policy = policy or FlowPolicy()
    # Never inspect future observations to describe an earlier scene.
    history = tuple(s for s in track.samples if s.at <= ended_at)
    if any(
        s.at.tzinfo is None
        or not isfinite(s.distance_m)
        or s.distance_m < 0
        or not isfinite(s.accuracy_m)
        or s.accuracy_m < 0
        for s in history
    ):
        raise ValueError("invalid distance observation")
    if any(a.at >= b.at for a, b in pairwise(history)):
        raise ValueError("distance observations must be chronological")
    # Keep the most recent continuous history; no relation across a GPS gap.
    for i in range(len(history) - 1, 0, -1):
        if (history[i].at - history[i - 1].at).total_seconds() > policy.max_gap_seconds or history[
            i
        ].continuity_id != history[i - 1].continuity_id:
            history = history[i:]
            break
    window = tuple(s for s in history if started_at <= s.at <= ended_at)
    if len(window) < 3 or window[0].at != started_at or window[-1].at != ended_at:
        return None
    if (ended_at - started_at).total_seconds() < policy.minimum_duration_s:
        return None
    tolerance = max(policy.minimum_change_m, 2 * max(s.accuracy_m for s in history))
    # Locate confirmed approach -> recession cycles. Earlier approach may predate
    # the last card, but the closest observation must belong to this interval.
    high = low = 0
    falling = False
    valleys = []
    for i, sample in enumerate(history):
        if not falling:
            if sample.distance_m > history[high].distance_m:
                high = i
            if history[high].distance_m - sample.distance_m > tolerance:
                falling, low = True, i
        else:
            if sample.distance_m < history[low].distance_m:
                low = i
            if sample.distance_m - history[low].distance_m > tolerance:
                if started_at < history[low].at <= ended_at:
                    valleys.append((high, low, i))
                falling, high = False, i
    if valleys:
        a, low, b = valleys[-1]
        profile = (history[a], history[low], history[b])
        case = "distance_valley"
        passage = history[a : b + 1]
        offsets = [s.target_axis_offset_m for s in passage]
        if track.scope != "reference_point" and all(v is not None for v in offsets):
            progresses = all(y >= x for x, y in pairwise(offsets)) or all(
                y <= x for x, y in pairwise(offsets)
            )
            if (
                progresses
                and offsets[0] * offsets[-1] < 0
                and history[low].distance_m + history[low].accuracy_m <= policy.alongside_limit_m
            ):
                case = "passing"
    else:
        profile = window
        values = [s.distance_m for s in window]
        change = values[-1] - values[0]
        rise = max(v - low for v, low in zip(values, accumulate(values, min)))
        fall = max(high - v for v, high in zip(values, accumulate(values, max)))
        if change < -tolerance and rise <= tolerance:
            case = "distance_decrease"
        elif change > tolerance and fall <= tolerance:
            case = "distance_increase"
        elif max(values) - min(values) <= tolerance:
            case = (
                "alongside"
                if track.scope != "reference_point"
                and window[0].path_offset_m is not None
                and window[-1].path_offset_m is not None
                and window[-1].path_offset_m - window[0].path_offset_m > tolerance
                and max(s.distance_m + s.accuracy_m for s in window) <= policy.alongside_limit_m
                else "distance_stable"
            )
        else:
            return None
    relevant = tuple(s for s in history if profile[0].at <= s.at <= profile[-1].at)
    key = digest(
        [
            track.target_id,
            track.source_version,
            asdict(policy),
            case,
            [
                [
                    s.at.isoformat(),
                    s.distance_m,
                    s.accuracy_m,
                    s.source_id,
                    s.continuity_id,
                    s.path_offset_m,
                    s.target_axis_offset_m,
                ]
                for s in relevant
            ],
        ]
    )
    return RelationFlow(
        "flow:" + key,
        case,
        profile[0].at,
        profile[-1].at,
        track.target_id,
        track.name,
        track.scope,
        profile,
        tuple(s.source_id for s in relevant),
        policy.version,
        track.source_version,
    )


def route_flow(row, writer_interval, *, connected_return=False):
    """Reuse source-bound route detectors, without upgrading endpoint proximity alone."""
    from datetime import datetime

    kind = row["kind"]
    cases = {
        "retrace": "route_retrace",
        "turn_reverse": "route_turn",
        "straight_run": "route_straight",
    }
    if kind == "end_near_start":
        if not connected_return:
            return None
        case = "route_return"
        start, end = row["start_anchor"]["event_at"], row["end_anchor"]["event_at"]
    elif kind in cases:
        case = cases[kind]
        start, end = row["support_started_at"], row["support_ended_at"]
    else:
        return None
    return RelationFlow(
        row["id"],
        case,
        datetime.fromisoformat(start),
        datetime.fromisoformat(end),
        None,
        None,
        None,
        (),
        (row["id"],),
        "existing-route-detectors",
        "source-bound-ledger",
        writer_interval,
    )
