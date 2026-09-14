"""Relative speed calculation; callers must supply their own policy values.

This candidate ranking preserves the older canonical observation algorithm. Diary
movement claims have distinct continuity/identity semantics and remain in diary.
"""

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class PacePolicy:
    slow_ratio: float
    fast_ratio: float
    minimum_seconds: float


def movement_candidates(nodes, baseline, reason, *, policy):
    if not baseline:
        return []
    groups, group, previous = [], [], None
    for n in nodes:
        speed = n.get("speed")
        direction = (
            "slow"
            if speed is not None and speed < baseline * policy.slow_ratio
            else "fast"
            if speed is not None and speed > baseline * policy.fast_ratio
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
        if duration < policy.minimum_seconds:
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


def session_speed_baseline(nodes, *, minimum_speed, minimum_samples):
    speeds = [n["speed"] for n in nodes if n.get("speed", 0) >= minimum_speed]
    return statistics.median(speeds) if len(speeds) >= minimum_samples else None
