"""Prepare saved feature rings once, preserving the normalizer's containment rule.

Real road features have thousands of holes. Bounding boxes skip irrelevant rings;
the ray crossing and boundary tolerance remain the same, even for self-touching
source rings. No geometry repair, simplification or new topology interpretation.
"""

import math
from itertools import pairwise

from .diary_space_geometry import feature_contains


class PreparedFeature:
    def __init__(self, geometry, crs, query):
        # Validate every ring/coordinate/CRS once with the existing implementation.
        feature_contains(geometry, crs, query)
        self.crs = crs
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
        self.polygons = []
        for polygon in polygons:
            rings = []
            for ring in polygon:
                xs, ys = zip(*ring)
                edges = tuple(
                    (a[0], a[1], b[0], b[1], b[0] - a[0], b[1] - a[1]) for a, b in pairwise(ring)
                )
                rings.append(((min(xs), min(ys), max(xs), max(ys)), edges))
            self.polygons.append(rings)

    def covers(self, point):
        if self.crs in {"EPSG:3857", "urn:ogc:def:crs:EPSG::3857"}:
            px = 6378137 * math.radians(point["lng"])
            py = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(point["lat"]) / 2))
        else:
            px, py = point["lng"], point["lat"]

        def state(ring):
            (xmin, ymin, xmax, ymax), edges = ring
            if not (xmin <= px <= xmax and ymin <= py <= ymax):
                return -1
            inside = False
            for ax, ay, bx, by, dx, dy in edges:
                cross = (px - ax) * dy - (py - ay) * dx
                if (
                    abs(cross) <= 1e-9 * max(1, abs(dx), abs(dy))
                    and min(ax, bx) <= px <= max(ax, bx)
                    and min(ay, by) <= py <= max(ay, by)
                ):
                    return 0
                if (ay > py) != (by > py) and px < ax + (py - ay) * dx / dy:
                    inside = not inside
            return 1 if inside else -1

        for polygon in self.polygons:
            outer = state(polygon[0])
            if outer == 0 or (outer == 1 and not any(state(ring) == 1 for ring in polygon[1:])):
                return True
        return False
