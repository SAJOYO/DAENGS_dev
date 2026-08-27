"""육각 셀 격자 — 앱·서버·적재가 공유하는 하나의 공간 ID 체계. 순수함수.

`app/ingest/anchors.py` 가 앵커를 솎을 때 쓰던 수학을 여기로 올린다. 같은 개념의 해석은
한 곳이어야 한다(결정 #52). Android `LocalHexCellIndexer` 도 같은 투영·축좌표를 쓰므로
셀 id 는 세 곳에서 같은 값이다 — 그 주장은 `docs/contracts/hex-grid-golden.json` 을
양쪽 테스트가 함께 읽어 지킨다.

**육각인 이유**: 사각 격자는 이웃이 변(G)과 대각(1.41G) 두 거리라 방향마다 간격이 다르다.
육각은 이웃 6개가 등거리다.

## 셀이 격자 실험이 아니라 저장층인 이유

결정 #57 이 `walk_fix`(연속 궤적)를 finish 직후 purge 한다. 그래서 **좌표가 사라진 뒤에도
공간 질문에 답하려면** 좌표가 아닌 무언가가 남아야 한다. 셀 방문 기록이 그 손실 압축이다 —
"어느 셀에 얼마나" 만 남기면 뒤에 생긴 면도 셀 집합으로 근사되고, 물감도 셀에 쌓인다
(`app/features/territory/region.py` · `app/features/territory/paint.py`).

반지름이 곧 다이얼이다. 작을수록 정밀하고 좌표에 가깝다(28단위 셀 id 는 사실상 좌표다).
어느 반지름에서 근사가 GPS 지터보다 작아지는지는 재야 하는 값이지 고르는 값이 아니다 —
`scripts/spikes/territory_paint/region_fidelity.py`, `docs/research/2026-08-26-region-cell-fidelity.md`.

**무엇을 영구히 남길지는 아직 안 정했다** — 단순화 궤적과 산책별 셀 맵이 후보다
(`docs/explorations/walk/territory-paint.md` §A). 실사용 업로드는 #57·#58 이 막고 있어
지금 고를 필요가 없다.
"""

import math
from collections.abc import Iterable

EARTH_R = 6_378_137.0

# 격자 수학의 버전. 투영·축좌표·반올림 중 무엇이라도 바뀌면 올린다 — 같은 (q, r) 이
# 다른 자리를 뜻하게 되기 때문이다. golden vector(docs/contracts/hex-grid-golden.json)가
# 이 버전의 값을 고정하고, 셀로판(`paint.Cellophane`)이 이 버전을 달고 다닌다.
GRID_VERSION = "hex-v1"

# anchors.py 가 앵커를 솎은 반지름(격자 단위). 위도 37.5° 에서 실제 91m, 셀 간격 158m.
ANCHOR_RADIUS_U = 115.0

Cell = tuple[int, int]


# ---- 단위 ↔ 실제 미터 -----------------------------------------------------------------
#
# **격자는 자가 아니라 색인이다.** 좌표는 Web Mercator 평면이라 위도가 올라갈수록 늘어난다 —
# 위도 37.5° 에서 1 단위는 실제 0.793m 다. 그래서 격자 파라미터는 `radius_u`(단위)이고,
# 거리·넓이를 말할 때는 반드시 아래 함수로 실제 지상값으로 바꾼다.
#
# 이름이 `radius_m` 이던 동안 붓 밴드 `3·8·20` 이 실제로는 2.4·6.3·15.9m 였고, `region.py`
# 의 등장방형(실제 미터) 계산과 다른 자를 쓰고 있었다. 이름이 거짓말을 하면 계산도 따라간다.


def metres_per_unit(lat: float) -> float:
    """이 위도에서 격자 1 단위가 몇 미터인가."""
    return math.cos(math.radians(lat))


def units_per_metre(lat: float) -> float:
    return 1.0 / math.cos(math.radians(lat))


def cell_size_m(radius_u: float, lat: float) -> float:
    """셀 반지름의 실제 지상 길이."""
    return radius_u * metres_per_unit(lat)


def cell_area_m2(radius_u: float, lat: float) -> float:
    """정육각형 넓이 = 1.5·√3·r². 실제 지상 면적이다."""
    return 1.5 * math.sqrt(3) * cell_size_m(radius_u, lat) ** 2


def mercator(lat: float, lng: float) -> tuple[float, float]:
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


def hex_cell(lat: float, lng: float, radius_u: float = ANCHOR_RADIUS_U) -> Cell:
    """좌표 → 셀 축좌표. 결정론 — 같은 좌표는 언제나 같은 셀이다."""
    x, y = mercator(lat, lng)
    return _round_axial(
        (math.sqrt(3) / 3 * x - y / 3) / radius_u,
        (2 / 3 * y) / radius_u,
    )


def hex_center(q: int, r: int, radius_u: float = ANCHOR_RADIUS_U) -> tuple[float, float]:
    """셀 → 투영 평면(격자 단위) 중심. anchors.py 가 '중심 우선' 선별에 쓰는 값이다."""
    return (radius_u * math.sqrt(3) * (q + r / 2), radius_u * 1.5 * r)


def hex_center_latlng(q: int, r: int, radius_u: float = ANCHOR_RADIUS_U) -> tuple[float, float]:
    x, y = hex_center(q, r, radius_u)
    return inverse_mercator(x, y)


def hex_boundary_latlng(
    q: int, r: int, radius_u: float = ANCHOR_RADIUS_U
) -> list[tuple[float, float]]:
    """셀 6꼭짓점. Android PolygonOverlay 가 요구하는 시계방향이다."""
    cx, cy = hex_center(q, r, radius_u)
    ring = []
    for index in range(6):
        angle = math.radians(30.0 - 60.0 * index)
        ring.append(
            inverse_mercator(cx + radius_u * math.cos(angle), cy + radius_u * math.sin(angle))
        )
    return ring


def cell_id(cell: Cell, radius_u: float = ANCHOR_RADIUS_U) -> str:
    """반지름이 id 에 들어간다 — 반지름이 다르면 다른 격자고, 섞이면 안 된다."""
    return f"hex:{round(radius_u)}:{cell[0]}:{cell[1]}"


def hex_sample_points(
    q: int, r: int, radius_u: float, rings: int = 3
) -> Iterable[tuple[float, float]]:
    """셀 내부 표본점(투영 단위). 면적 교차를 적분 대신 표본으로 근사할 때 쓴다."""
    cx, cy = hex_center(q, r, radius_u)
    yield (cx, cy)
    for ring in range(1, rings + 1):
        # 육각 안에 확실히 들어가는 내접원 비율. sqrt(3)/2 가 내접원/외접원.
        rho = radius_u * (math.sqrt(3) / 2) * (ring / (rings + 0.5))
        count = 6 * ring
        for index in range(count):
            angle = 2 * math.pi * index / count
            yield (cx + rho * math.cos(angle), cy + rho * math.sin(angle))
