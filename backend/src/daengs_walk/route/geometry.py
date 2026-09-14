"""Geographic distance and per-continuity-block route coverage calculations."""


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


def distance(a, b):
    import math

    la, lb = math.radians(a[0]), math.radians(b[0])
    h = (
        math.sin((lb - la) / 2) ** 2
        + math.cos(la) * math.cos(lb) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(h)))
