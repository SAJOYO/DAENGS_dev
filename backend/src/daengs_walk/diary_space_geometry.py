"""Small point queries for saved WMS features; no segmentation or map service.

Distances use the same short-route local tangent approximation as route patterns. Geometry
containment is tested in the source CRS, including holes and polygon boundaries.
"""

import math
from itertools import pairwise


def xy(point, origin):
    return (
        6371000
        * math.radians(point["lng"] - origin["lng"])
        * math.cos(math.radians(origin["lat"])),
        6371000 * math.radians(point["lat"] - origin["lat"]),
    )


def distance(point, origin):
    return math.hypot(*xy(point, origin))


def feature_contains(geometry, crs, point):
    """Return containment for a valid Polygon/MultiPolygon, raise for unknown CRS."""
    if crs in {"EPSG:3857", "urn:ogc:def:crs:EPSG::3857"}:
        target = (
            6378137 * math.radians(point["lng"]),
            6378137 * math.log(math.tan(math.pi / 4 + math.radians(point["lat"]) / 2)),
        )
    elif crs in {"EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84", "OGC:CRS84"}:
        target = point["lng"], point["lat"]
    else:
        raise ValueError("unsupported_land_crs")
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError("unsupported_land_geometry")
    if not polygons:
        raise ValueError("empty_land_geometry")

    def ring_state(ring):
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError("invalid_land_ring")
        inside = False
        px, py = target
        for a, b in pairwise(ring):
            if any(
                len(v) != 2 or any(isinstance(n, bool) or not math.isfinite(n) for n in v)
                for v in (a, b)
            ):
                raise ValueError("invalid_land_coordinate")
            ax, ay = a
            bx, by = b
            dx, dy = bx - ax, by - ay
            cross = (px - ax) * dy - (py - ay) * dx
            if (
                abs(cross) <= 1e-9 * max(1, abs(dx), abs(dy))
                and min(ax, bx) <= px <= max(ax, bx)
                and min(ay, by) <= py <= max(ay, by)
            ):
                return 0  # A boundary is covered, including a hole's boundary.
            if (ay > py) != (by > py) and px < ax + (py - ay) * dx / dy:
                inside = not inside
        return 1 if inside else -1

    covered = False
    for polygon in polygons:
        if not polygon:
            raise ValueError("invalid_land_polygon")
        states = [ring_state(r) for r in polygon]
        covered |= states[0] == 0 or (states[0] == 1 and not any(s == 1 for s in states[1:]))
    return covered
