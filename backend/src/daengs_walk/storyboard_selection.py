"""Priority draft: explicit records, historical movement, session movement, route coverage."""

from __future__ import annotations

import statistics
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    minimum: int = Field(4, ge=1, le=8)
    separation_m: float = Field(100, ge=20, le=500)
    max_unread_m: float = Field(300, ge=50, le=2000)


class ReferenceWalk(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    walk_id: str = Field(min_length=1, max_length=128)
    pet_id: str = Field(min_length=1, max_length=128)
    started_at: datetime
    median_speed_mps: float = Field(gt=0, le=10)


def movement_candidates(nodes, baseline, reason):
    if not baseline:
        return []
    groups, group, previous = [], [], None
    for n in nodes:
        speed = n.get("speed")
        direction = (
            "slow"
            if speed is not None and speed < baseline * 0.5
            else "fast"
            if speed is not None and speed > baseline * 1.75
            else None
        )
        key = (n["block"], direction)
        if not direction or key != previous:
            if group:
                groups.append(group)
            group = []
        if direction:
            group.append(n)
        previous = key
    if group:
        groups.append(group)
    candidates = []
    for group in groups:
        duration = sum(n["duration_s"] for n in group)
        if duration < 20:
            continue
        middle = (group[0]["start_s"] + group[-1]["elapsed_s"]) / 2
        candidate = dict(min(group, key=lambda n: abs(n["elapsed_s"] - middle)))
        speed = sum(n["speed"] * n["duration_s"] for n in group) / duration
        candidate.update(
            reason=reason,
            score=abs(speed - baseline) * duration,
            movement={
                "start_s": group[0]["start_s"],
                "end_s": group[-1]["elapsed_s"],
                "baseline_mps": baseline,
                "mean_mps": speed,
            },
        )
        candidates.append(candidate)
    return sorted(candidates, key=lambda c: (-c["score"], c["elapsed_s"]))


def uncovered(nodes, selected, radius):
    gaps = []
    for block in sorted({n["block"] for n in nodes}):
        subset = [n for n in nodes if n["block"] == block]
        start, end = subset[0]["route_m"], subset[-1]["route_m"]
        cursor = start
        for chosen in sorted(
            (c for c in selected if c["block"] == block), key=lambda c: c["route_m"]
        ):
            left, right = (
                max(start, chosen["route_m"] - radius),
                min(end, chosen["route_m"] + radius),
            )
            if left > cursor:
                gaps.append({"block": block, "start_m": cursor, "end_m": left})
            cursor = max(cursor, right)
        if cursor < end:
            gaps.append({"block": block, "start_m": cursor, "end_m": end})
    return sorted(gaps, key=lambda g: (-(g["end_m"] - g["start_m"]), g["block"], g["start_m"]))


def select_nodes(nodes, entries, policy, references, *, session_id, pet_id, started_at):
    selected, deferred, steps = [], [], []
    start = started_at
    history = {
        r.walk_id: r
        for r in references
        if r.walk_id != session_id
        and r.pet_id == pet_id
        and r.started_at.tzinfo is not None
        and r.started_at < start
    }
    historical = (
        statistics.median(r.median_speed_mps for r in history.values())
        if len(history) >= 3
        else None
    )
    speeds = [n["speed"] for n in nodes if n.get("speed", 0) >= 0.5]
    baseline = statistics.median(speeds) if len(speeds) >= 5 else None

    def add(candidate, force=False):
        near = next(
            (
                c
                for c in selected
                if c["block"] == candidate["block"]
                and abs(c["route_m"] - candidate["route_m"]) < policy.separation_m
                and distance(
                    (c["location"]["lat"], c["location"]["lng"]),
                    (candidate["location"]["lat"], candidate["location"]["lng"]),
                )
                < policy.separation_m
            ),
            None,
        )
        if near:
            if candidate["reason"] not in near["reasons"]:
                near["reasons"].append(candidate["reason"])
            near["entry_ids"].extend(candidate.get("entry_ids", []))
            if candidate.get("movement"):
                near["movement_evidence"].append(
                    candidate["movement"] | {"reason": candidate["reason"]}
                )
            return False
        if len(selected) >= 8 or (not force and len(selected) >= policy.minimum):
            deferred.append(
                {
                    "reason": candidate["reason"],
                    "entry_ids": candidate.get("entry_ids", []),
                    "status": "budget" if len(selected) >= 8 else "minimum_met",
                }
            )
            return False
        chosen = {k: candidate[k] for k in ("route_m", "block", "elapsed_s", "location")}
        chosen.update(
            id=f"anchor-{len(selected) + 1}",
            reasons=[candidate["reason"]],
            entry_ids=list(candidate.get("entry_ids", [])),
            movement_evidence=[candidate["movement"] | {"reason": candidate["reason"]}]
            if candidate.get("movement")
            else [],
        )
        selected.append(chosen)
        steps.append(
            {
                "reason": candidate["reason"],
                "anchor_id": chosen["id"],
                "route_m": round(chosen["route_m"], 1),
                "gap_before": candidate.get("gap_before"),
            }
        )
        return True

    for entry in entries:
        if not entry["accepted"] or entry["kind"] != "behavior":
            continue
        closest = min(nodes, key=lambda n: abs(n["elapsed_s"] - entry["elapsed_s"]), default=None)
        if closest is None or not entry.get("route_known", True):
            deferred.append(
                {"entry_ids": [entry["id"]], "status": "no_valid_route", "reason": "action"}
            )
            continue
        add(
            dict(
                closest,
                location=entry["location"],
                reason="action",
                elapsed_s=entry["elapsed_s"],
                entry_ids=[entry["id"]],
            ),
            force=True,
        )
    for ref, reason in ((historical, "profile_change"), (baseline, "session_speed")):
        for candidate in movement_candidates(nodes, ref, reason):
            add(candidate)
    while nodes and len(selected) < 8:
        gaps = uncovered(nodes, selected, policy.separation_m / 2)
        too_long = bool(gaps and gaps[0]["end_m"] - gaps[0]["start_m"] > policy.max_unread_m)
        if len(selected) >= policy.minimum and not too_long:
            break
        available = [
            n
            for n in nodes
            if all(
                n["block"] != c["block"] or abs(n["route_m"] - c["route_m"]) >= policy.separation_m
                for c in selected
            )
        ]
        if not available:
            break
        candidate, gap = None, None
        for gap in gaps:
            if (
                len(selected) >= policy.minimum
                and gap["end_m"] - gap["start_m"] <= policy.max_unread_m
            ):
                continue
            choices = [
                n
                for n in available
                if n["block"] == gap["block"] and gap["start_m"] <= n["route_m"] <= gap["end_m"]
            ]
            if choices:
                midpoint = (gap["start_m"] + gap["end_m"]) / 2
                candidate = min(choices, key=lambda n: abs(n["route_m"] - midpoint))
                break
        if candidate is None:
            if len(selected) >= policy.minimum:
                break
            candidate = available[0]
            gap = None
        if not add(dict(candidate, reason="distance_fill", gap_before=gap), force=True):
            break
    gaps = uncovered(nodes, selected, policy.separation_m / 2)
    longest = max((g["end_m"] - g["start_m"] for g in gaps), default=0)
    return {
        "version": "priority-coverage-draft-v1",
        "settings": policy.model_dump(),
        "anchors": selected,
        "steps": steps,
        "deferred": deferred,
        "minimum_met": len(selected) >= policy.minimum,
        "shortfall_reason": None
        if len(selected) >= policy.minimum
        else "insufficient_distinct_valid_route",
        "longest_unread_m": round(longest, 1),
        "coverage_met": longest <= policy.max_unread_m,
        "max_anchors": 8,
        "reference_status": "available" if historical else "insufficient_history",
        "reference_walk_ids": sorted(history) if historical else [],
        "movement_summary": {
            "walk_id": session_id,
            "pet_id": pet_id,
            "started_at": start.isoformat(),
            "median_speed_mps": baseline,
        },
    }


def distance(a, b):
    import math

    la, lb = math.radians(a[0]), math.radians(b[0])
    h = (
        math.sin((lb - la) / 2) ** 2
        + math.cos(la) * math.cos(lb) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(h)))
