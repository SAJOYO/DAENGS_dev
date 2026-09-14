"""Priority draft: explicit records, historical movement, session movement, route coverage."""

from __future__ import annotations

import statistics
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from daengs_walk.route.geometry import distance, uncovered
from daengs_walk.route.pace import PacePolicy
from daengs_walk.route.pace import movement_candidates as calculate_candidates
from daengs_walk.route.pace import session_speed_baseline as calculate_baseline


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


# The supported priority storyboard owns these values independently of diary.
STORYBOARD_PACE = PacePolicy(slow_ratio=0.5, fast_ratio=1.75, minimum_seconds=20)


def movement_candidates(nodes, baseline, reason):
    return calculate_candidates(nodes, baseline, reason, policy=STORYBOARD_PACE)


def session_speed_baseline(nodes):
    return calculate_baseline(nodes, minimum_speed=0.5, minimum_samples=5)


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
    baseline = session_speed_baseline(nodes)

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
        chosen["observation"] = candidate.get("observation")
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
        if closest is None or entry.get("location") is None or not entry.get("route_known", True):
            deferred.append(
                {"entry_ids": [entry["id"]], "status": "no_valid_route", "reason": "action"}
            )
            continue
        add(
            dict(
                closest,
                observation=None,  # An entry location/time is not the nearest GPS sample's identity.
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
