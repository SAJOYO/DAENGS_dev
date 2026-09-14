"""Observed-distance candidates for activity writing; legacy GEO extraction is unchanged."""

import math
import statistics
from bisect import bisect_left
from itertools import pairwise

from pydantic import Field

from daengs_walk.diary.contracts.input import DiaryContract
from daengs_walk.diary.route.geometry import (
    _angle,
    _chains,
    _distance,
    _line_distance,
    _material,
    _simplify,
    _stays,
)

SHAPE_MEANINGS = {
    "curve_left": "왼쪽으로 완만하게 휘어 이동",
    "curve_right": "오른쪽으로 완만하게 휘어 이동",
    "direction_left": "왼쪽으로 진행 방향이 바뀜",
    "direction_right": "오른쪽으로 진행 방향이 바뀜",
    "turn_sharp_left": "왼쪽으로 크게 꺾음",
    "turn_sharp_right": "오른쪽으로 크게 꺾음",
}


class MovementShapePolicy(DiaryContract):
    version: str = "observed-distance-shapes-v1"
    heading_leg_m: float = Field(default=30, gt=0)
    accuracy_multiplier: float = Field(default=4, gt=0)
    change_deg: float = Field(default=8, gt=0)
    minimum_change_deg: float = Field(default=20, gt=0)
    curve_minimum_deg: float = Field(default=30, gt=0)
    curve_max_peak_ratio: float = Field(default=0.6, gt=0, lt=1)
    reverse_deg: float = Field(default=160, gt=135, le=180)
    sharp_deg: float = Field(default=135, gt=90, lt=160)
    retrace_heading_deg: float = Field(default=30, gt=0, lt=90)
    retrace_lookback_m: float = Field(default=500, gt=0)
    retrace_step_m: float = Field(default=15, gt=0)


def _vertices(points, leg, skip=()):
    # Only observed fixes count as support. No interpolated samples add confidence.
    indices = [0]
    for i in range(1, len(points)):
        if i in skip:
            continue
        if _distance(points[indices[-1]].xy, points[i].xy) >= leg:
            indices.append(i)
    if indices[-1] != len(points) - 1:
        indices.append(len(points) - 1)
    return indices


def _candidates(points, indices, leg, policy):
    changes = []
    for j in range(1, len(indices) - 1):
        a, b, c = (points[i].xy for i in indices[j - 1 : j + 2])
        if min(_distance(a, b), _distance(b, c)) < leg * 0.5:
            continue
        angle = _angle(a, b, c)
        if abs(angle) >= policy.change_deg:
            changes.append((j, angle))
    groups = []
    for item in changes:
        # An intervening stable heading or a sign reversal closes the candidate.
        if (
            not groups
            or item[0] != groups[-1][-1][0] + 1
            or item[1] * groups[-1][-1][1] <= 0
            or abs(item[1]) >= policy.sharp_deg
            or abs(groups[-1][-1][1]) >= policy.sharp_deg
        ):
            groups.append([])
        groups[-1].append(item)
    return groups


def _shape_runs(points, chain, run, geometry, policy, stays):
    accuracy = [p.fix.accuracy_m for p in points if p.fix.accuracy_m is not None]
    error = statistics.median(accuracy) if accuracy else geometry.max_accuracy_m
    leg = max(policy.heading_leg_m, policy.accuracy_multiplier * error)
    skip = {i for a, b, pivot, *_ in stays for i in range(a, b + 1) if i != pivot}
    indices = _vertices(points, leg, skip)
    materials, occupied = [], []
    for group in _candidates(points, indices, leg, policy):
        total = sum(angle for _, angle in group)
        if abs(total) < policy.minimum_change_deg:
            continue
        first, last = group[0][0], group[-1][0]
        a, c = indices[first - 1], indices[last + 1]
        peak = max(group, key=lambda v: abs(v[1]))
        b = indices[peak[0]]
        ratio = abs(peak[1] / total)
        side = "left" if total > 0 else "right"
        distributed = len(group) >= 3 and ratio <= policy.curve_max_peak_ratio
        # A sampled arc needs visible lateral displacement beyond location uncertainty.
        deviation = max(_line_distance(p.xy, points[a].xy, points[c].xy) for p in points[a : c + 1])
        curve = distributed and abs(total) >= policy.curve_minimum_deg and deviation > error
        supported_corner = b - a >= 2 and c - b >= 2 and bool(accuracy)
        if curve:
            meaning = "curve_" + side
        elif abs((total + 180) % 360 - 180) >= policy.reverse_deg:
            meaning = "turn_reverse"
        elif supported_corner and not distributed:
            meaning = ("turn_sharp_" if abs(total) > policy.sharp_deg else "turn_") + side
        else:
            meaning = "direction_" + side
        raw = _material(
            points,
            chain,
            run,
            "path",
            a,
            c,
            b,
            {
                "signed_angle_deg": total,
                "absolute_rotation_deg": sum(abs(v) for _, v in group),
                "peak_rotation_ratio": ratio,
                "heading_leg_m": leg,
                "original_fix_count": c - a + 1,
                "stable_before": first > 1,
                "stable_after": last < len(indices) - 2,
            },
        )
        raw["meaning"] = meaning
        raw["is_event"] = meaning.startswith(("turn_", "direction_"))
        materials.append(raw)
        if curve:
            occupied.append((a, c))
    # Straight must have positive support; unclassified windows are not straight by default.
    tolerance = max(error, geometry.simplify_m)
    straight_vertices = _simplify(
        points, [i for i in range(len(points)) if i not in skip], tolerance
    )
    for a, b in pairwise(straight_vertices):
        if any(a < end and start < b for start, end in occupied):
            continue
        distance = _distance(points[a].xy, points[b].xy)
        deviation = max(_line_distance(p.xy, points[a].xy, points[b].xy) for p in points[a : b + 1])
        if distance >= max(geometry.straight_min_m, 2 * tolerance) and deviation <= tolerance:
            raw = _material(points, chain, run, "path", a, b, a, {"max_deviation_m": deviation})
            materials.append({**raw, "meaning": "straight_run", "is_event": False})
    return materials


def _projection(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length**2)) if length else 0
    return _distance(p, (a[0] + t * dx, a[1] + t * dy)), t, length


def _retraces(points, indices, chain, run, geometry, policy):
    """Recent past segment matching with reverse heading and decreasing path position."""
    offsets = [0.0]
    for a, b in pairwise(indices):
        offsets.append(offsets[-1] + _distance(points[a].xy, points[b].xy))
    groups, active = [], []
    accuracy = [p.fix.accuracy_m for p in points if p.fix.accuracy_m is not None]
    if not accuracy:
        return []
    lateral_limit = min(geometry.retrace_tolerance_m, max(1.0, statistics.median(accuracy) / 2))
    for j in range(1, len(indices)):
        a, b = (points[i].xy for i in indices[j - 1 : j + 1])
        options = []
        for k in range(max(1, bisect_left(offsets, offsets[j] - policy.retrace_lookback_m)), j - 1):
            if offsets[j] - offsets[k] > policy.retrace_lookback_m:
                continue
            p, q = (points[i].xy for i in indices[k - 1 : k + 1])
            da, ta, length = _projection(a, p, q)
            db, tb, _ = _projection(b, p, q)
            if not length or ta <= tb:
                continue
            # Along-segment sampling offsets are allowed; a persistent parallel offset is not.
            lateral = max(
                abs((x[0] - p[0]) * (q[1] - p[1]) - (x[1] - p[1]) * (q[0] - p[0])) / length
                for x in (a, b)
            )
            if lateral > lateral_limit:
                continue
            dot = ((b[0] - a[0]) * (q[0] - p[0]) + (b[1] - a[1]) * (q[1] - p[1])) / (
                max(_distance(a, b), 1e-9) * length
            )
            if dot > -math.cos(math.radians(policy.retrace_heading_deg)):
                continue
            # Keep corridor bounded: poor accuracy does not widen it to a parallel street.
            if max(da, db) <= geometry.retrace_tolerance_m:
                options.append(
                    (da + db, k, offsets[k - 1] + ta * length, offsets[k - 1] + tb * length)
                )
        options.sort()
        # Distinct equally plausible historical visits are ambiguous.
        if (
            len(options) > 1
            and abs(options[0][0] - options[1][0]) < 1
            and abs(options[0][1] - options[1][1]) > 1
        ):
            options = []
        chosen = options[0] if options else None
        if active and (
            not chosen
            or abs(chosen[2] - active[-1][2]) > geometry.retrace_tolerance_m
            or chosen[3] >= active[-1][2]
        ):
            groups.append(active)
            active = []
        if chosen:
            active.append((j, chosen[2], chosen[3], chosen[1]))
    if active:
        groups.append(active)
    result = []
    for group in groups:
        a, b = indices[group[0][0] - 1], indices[group[-1][0]]
        length = group[0][1] - group[-1][2]
        if length < geometry.retrace_min_m:
            continue
        raw = _material(
            points,
            chain,
            run,
            "path",
            a,
            b,
            a,
            {
                "matched_reverse_m": length,
                "past_from_seq": points[indices[group[0][3]]].fix.client_seq,
                "past_to_seq": points[indices[group[-1][3] - 1]].fix.client_seq,
                "past_positions_m": [x[2] for x in group],
            },
        )
        result.append({**raw, "meaning": "retrace", "is_event": False})
    return result


def movement_shapes(trail, geometry, policy=None):
    policy = policy or MovementShapePolicy()
    chains, audit = _chains(trail, geometry)
    result = []
    for chain, run, points in chains:
        accuracy = [p.fix.accuracy_m for p in points if p.fix.accuracy_m is not None]
        error = statistics.median(accuracy) if accuracy else geometry.max_accuracy_m
        # A slow but steadily advancing walk is not stationary jitter. Require bounded
        # net progress as well as the legacy size/duration gate before collapsing fixes.
        stays = [
            s
            for s in _stays(points, geometry)
            if _distance(points[s[0]].xy, points[s[1]].xy)
            <= min(geometry.stay_diameter_m / 2, 2 * error)
        ]
        for a, b, pivot, diameter, speed in stays:
            raw = _material(
                points,
                chain,
                run,
                "path",
                a,
                b,
                pivot,
                {"diameter_m": diameter, "progress_mps": speed},
            )
            result.append({**raw, "meaning": "local_stay", "is_event": False})
        shapes = _shape_runs(points, chain, run, geometry, policy, stays)
        # Dense stationary jitter must never create a directional narrative.
        result.extend(
            s
            for s in shapes
            if not any(
                points[a].fix.client_seq <= s["support"]["from_seq"]
                and s["support"]["to_seq"] <= points[b].fix.client_seq
                for a, b, *_ in stays
            )
        )
        match_vertices = _vertices(points, policy.retrace_step_m)
        result.extend(_retraces(points, match_vertices, chain, run, geometry, policy))
    return result, audit, policy
