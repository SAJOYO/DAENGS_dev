"""산책 공간 산출물이 공유하는 결정론적 hex-v1 셀 격자."""

import math

EARTH_R = 6_378_137.0
GRID_VERSION = "hex-v1"

Cell = tuple[int, int]


def metres_per_unit(lat: float) -> float:
    """Web Mercator 격자 1단위를 해당 위도의 실제 지상 미터로 바꾼다."""
    return math.cos(math.radians(lat))


def units_per_metre(lat: float) -> float:
    return 1.0 / metres_per_unit(lat)


def cell_size_m(radius_u: float, lat: float) -> float:
    return radius_u * metres_per_unit(lat)


def cell_area_m2(radius_u: float, lat: float) -> float:
    return 1.5 * math.sqrt(3) * cell_size_m(radius_u, lat) ** 2


def mercator(lat: float, lng: float) -> tuple[float, float]:
    """위경도를 hex-v1이 사용하는 Web Mercator 평면으로 투영한다."""
    lat = max(-85.0, min(85.0, lat))
    return (
        EARTH_R * math.radians(lng),
        EARTH_R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
    )


def inverse_mercator(x: float, y: float) -> tuple[float, float]:
    return (
        math.degrees(2 * math.atan(math.exp(y / EARTH_R)) - math.pi / 2),
        math.degrees(x / EARTH_R),
    )


def _round_axial(q: float, r: float) -> Cell:
    x, z = q, r
    y = -x - z
    rx, ry, rz = round(x), round(y), round(z)
    dx, dy, dz = abs(rx - x), abs(ry - y), abs(rz - z)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return rx, rz


def hex_cell(lat: float, lng: float, radius_u: float) -> Cell:
    """좌표를 결정론적인 hex-v1 축좌표로 바꾼다."""
    _require_radius(radius_u)
    x, y = mercator(lat, lng)
    return _round_axial(
        (math.sqrt(3) / 3 * x - y / 3) / radius_u,
        (2 / 3 * y) / radius_u,
    )


def hex_center(q: int, r: int, radius_u: float) -> tuple[float, float]:
    _require_radius(radius_u)
    return (radius_u * math.sqrt(3) * (q + r / 2), radius_u * 1.5 * r)


def hex_center_latlng(q: int, r: int, radius_u: float) -> tuple[float, float]:
    return inverse_mercator(*hex_center(q, r, radius_u))


def hex_boundary_latlng(q: int, r: int, radius_u: float) -> list[tuple[float, float]]:
    """셀의 여섯 꼭짓점을 시계방향 위경도로 돌려준다."""
    cx, cy = hex_center(q, r, radius_u)
    return [
        inverse_mercator(
            cx + radius_u * math.cos(math.radians(30.0 - 60.0 * index)),
            cy + radius_u * math.sin(math.radians(30.0 - 60.0 * index)),
        )
        for index in range(6)
    ]


def _require_radius(radius_u: float) -> None:
    if not math.isfinite(radius_u) or radius_u <= 0:
        raise ValueError("radius_u must be a finite positive number")
