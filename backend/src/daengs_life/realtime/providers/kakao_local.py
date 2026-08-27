"""카카오 Local — **표기 전용 provider** (RT-001 ①).

일곱 중 유일하게 판정에 안 쓰인다. 좌표 변환도 최근접 측정소도 `geo.py` 가 자체 계산하므로
(§6.5), 여기 남은 역할은 `realtime-apis.md` §0 이 요구한 정직 표기 — **"○○동 (측정소: △△)
기준"** 의 앞부분뿐이다.

**그래서 실패해도 관통된다.** 키가 없거나 403 이면 좌표를 그대로 라벨로 쓴다. `⑤`가 저하를
만들 필요조차 없다 — 판정 입력이 아니기 때문이다.
"""
from __future__ import annotations

from typing import Any

from ..geo import LatLon
from ..transport import kakao
from ..transport.base import Budget, TransportError

REGION_PATH = "/v2/local/geo/coord2regioncode.json"
TRANSCOORD_PATH = "/v2/local/geo/transcoord.json"


def parse_region(payload: Any) -> str | None:
    """행정동 이름. 없으면 `None`.

    `region_type` 이 둘이다 — `'B'` 는 법정동, `'H'` 는 행정동. **행정동(H)을 고른다.**
    사람이 "우리 동네"라고 부르는 단위이고, 지자체 지원·특보구역 매핑도 행정 단위를 쓴다.
    `H` 가 없으면 `B` 로 떨어진다 — 이름이 없는 것보다 법정동이라도 있는 편이 낫다.
    """
    documents = (payload or {}).get("documents") or []
    for want in ("H", "B"):
        for doc in documents:
            if doc.get("region_type") == want and doc.get("region_3depth_name"):
                return str(doc["region_3depth_name"])
    return None


def parse_transcoord(payload: Any) -> tuple[float, float] | None:
    """TM 좌표 `(x, y)`.

    **대체 경로다.** 에어코리아 `getNearbyMsrstnList` 가 TM 을 요구하는데, 우리는 측정소 목록을
    캐시해 하버사인으로 고르므로 이 체인을 안 탄다 (§6.5). 남겨 두는 이유는 목록 캐시가
    비었을 때의 폴백이고, 그때는 호출 두 번을 더 쓰는 대신 답이 나온다.
    """
    documents = (payload or {}).get("documents") or []
    if not documents:
        return None
    doc = documents[0]
    try:
        return float(doc["x"]), float(doc["y"])
    except (KeyError, TypeError, ValueError):
        return None


# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_region(point: LatLon, *, budget: Budget | None = None) -> Any:
    return kakao.get(REGION_PATH, {"x": point.lon, "y": point.lat}, budget=budget)


def label_for(point: LatLon, *, budget: Budget | None = None) -> str:
    """사람에게 보여줄 위치 이름. **절대 실패하지 않는다.**

    ⑥ 응답의 `location.label` 이 이것이다. 카카오가 죽었다고 판정을 못 내보내면 "표기 때문에
    답을 못 준다"가 되는데, 그건 ①이 카카오를 UX 역할로 좁힌 이유와 정면으로 어긋난다.
    """
    try:
        name = parse_region(kakao.get(REGION_PATH, {"x": point.lon, "y": point.lat}, budget=budget))
    except TransportError:
        name = None
    return name or f"{point.lat:.4f}, {point.lon:.4f}"


def fetch_transcoord(point: LatLon, *, budget: Budget | None = None) -> tuple[float, float] | None:
    return parse_transcoord(kakao.get(TRANSCOORD_PATH,
                                      {"x": point.lon, "y": point.lat, "output_coord": "TM"},
                                      budget=budget))
