"""점령지 적재가 쓰는 Web Mercator 육각 격자 순수함수.

DAENGS_geo ``app.geo.cells``에서 검증한 ``hex-v1`` 수학을 운영 적재 경계로 옮겼다.
140은 미터가 아니라 투영 격자 단위이며, 서울 위도에서 셀 반지름 약 111m·이웃 셀 중심
간격 약 192m다.
"""

import math

EARTH_RADIUS_M = 6_378_137.0
GRID_VERSION = "hex-v1"
TERRITORY_SITE_RADIUS_U = 140.0
ACTIVE_SITE_ID_PREFIX = (
    f"territory-site:{GRID_VERSION}:{round(TERRITORY_SITE_RADIUS_U)}:"
)

Cell = tuple[int, int]


def mercator(lat: float, lng: float) -> tuple[float, float]:
    lat = max(-85.0, min(85.0, lat))
    return (
        EARTH_RADIUS_M * math.radians(lng),
        EARTH_RADIUS_M * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
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


def hex_cell(
    lat: float,
    lng: float,
    radius_u: float = TERRITORY_SITE_RADIUS_U,
) -> Cell:
    """좌표를 안정적인 축좌표 셀로 바꾼다."""
    x, y = mercator(lat, lng)
    return _round_axial(
        (math.sqrt(3) / 3 * x - y / 3) / radius_u,
        (2 / 3 * y) / radius_u,
    )


def hex_center(
    q: int,
    r: int,
    radius_u: float = TERRITORY_SITE_RADIUS_U,
) -> tuple[float, float]:
    """축좌표 셀의 Web Mercator 중심."""
    return (radius_u * math.sqrt(3) * (q + r / 2), radius_u * 1.5 * r)


def site_id(cell: Cell, radius_u: float = TERRITORY_SITE_RADIUS_U) -> str:
    """격자 세대와 반경을 포함하는 외부 점령지 식별자."""
    return f"territory-site:{GRID_VERSION}:{round(radius_u)}:{cell[0]}:{cell[1]}"
