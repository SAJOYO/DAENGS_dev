"""특보구역명 ↔ 행정구역 매핑 (RT-003).

RT-001 ②-a 가 🟡 로 남겨 둔 자리다. `kma_warning.parse_pwn` 은 `t6` 자연어에서 **내 구역
이름을 찾는** 방식이라, 강남구를 `'서울동남권'` 으로 옮기지 못하면 구 단위 특보를 통째로
놓친다. 시도 단축명(`'서울'`)만 넘기던 것이 지금까지의 최선이었다.

**표는 코드 옆에 산다** (`warning_areas.csv`) — `thresholds.yaml`·`cache.yaml` 과 같은 자리다.
RT-001 ⑨ 와 `kma_warning` 의 docstring 은 `data/reference/` 를 예고했지만 거기 두지 않았다:
`REFERENCE_DIR` 은 `DAENGS_DATA_DIR` 에서 파생되는데 그 값은 **배포마다 다른 곳을 가리킨다**
(GCP 는 체크아웃 밖 더미 경로를 쓴다 — `docs/deploy/roadmap.md` §5). 커밋된 표를 그 경로에
두면 서버에서만 조용히 안 읽혀 특보가 다시 시도 단위로 돌아간다. `data/reference/` 는
**받아 오는 캐시**(측정소 목록)의 자리이고, 이 표는 코드와 함께 버전이 매겨지는 상수다.

표를 다시 굽는 법은 `tools/fetch_warning_areas.py` 에 있다 (기상청 날씨누리가 출처).
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

TABLE_FILE = Path(__file__).with_name("warning_areas.csv")

# 발표관서가 관할하는 시도 (단축명). **이름 충돌을 가르는 유일한 수단이다** — `중구` 는
# 서울·인천·대구·부산·울산에 다 있고, `고성군` 은 강원과 경남에 있다. 표에는 시도 열이
# 없으므로(원본에 없다) 관서로 좁힌 뒤 이름으로 마저 가른다.
OFFICE_SIDO: dict[str, frozenset[str]] = {
    "수도권기상청": frozenset({"서울", "인천", "경기"}),
    "강원지방기상청": frozenset({"강원"}),
    "청주기상지청": frozenset({"충북"}),
    "대전지방기상청": frozenset({"대전", "세종", "충남"}),
    "전주기상지청": frozenset({"전북"}),
    "광주지방기상청": frozenset({"광주", "전남"}),
    "대구지방기상청": frozenset({"대구", "경북"}),
    "부산지방기상청": frozenset({"부산", "울산", "경남"}),
    "제주지방기상청": frozenset({"제주"}),
}

# 행정단위 하나. `중구` 처럼 두 글자짜리가 있으므로 앞부분에 `{2,}` 를 걸면 안 된다.
_UNIT = re.compile(r"^[가-힣]+(?:시|군|구|읍|면|동|리)\d*$")
_PAREN = re.compile(r"\(.*?\)|\(.*$")


def _units(covers: str) -> frozenset[str]:
    """관할구역 문장에서 **실제로 관할되는** 행정단위만 뽑는다.

    두 가지를 일부러 버린다:

    - **괄호는 통째로 버린다.** `'중구(인천영종 제외)'` 의 괄호 안은 제외 규칙이지 관할이
      아니다. 제외를 해석하려면 하위 구역 지식이 필요한데 우리에게 없다 — 지어내지 않는다.
    - **한 토큰 안에서는 마지막 단위만 취한다.** `'강릉시 연곡면'` 의 강릉시는 연곡면을
      한정하는 말이지 그 자체가 관할이 아니다. 앞을 같이 담으면 강릉시가 `강릉산지` 에도
      걸려, 시내 한복판에서 산지 특보를 자기 것으로 읽는다.

    `'강릉시 산지 제외 지역'`(=강릉평지)처럼 서술문이면 `강릉시` 만 남는다. 고도를 모르니
    평지·산지를 가릴 수 없고, 그 경우 두 이름이 다 후보로 남는 것이 정직한 결과다.
    """
    out: set[str] = set()
    for token in covers.split("."):
        words = [w for w in _PAREN.sub("", token).split() if _UNIT.fullmatch(w)]
        if words:
            out.add(words[-1])
    return frozenset(out)


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[tuple[str, str, int], ...]]:
    """행정단위 → (발표관서, 특보구역명, 관할 단위 수).

    표가 없으면 **빈 index 다.** 예외를 던지지 않는다 — 표가 없다는 것은 요청이 틀린 것이
    아니라 배포가 덜 된 것이고, ⑤ 저하 정책이 특보 축을 `unknown` 으로 만들어 준다.
    """
    if not TABLE_FILE.exists():
        return {}
    index: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    with TABLE_FILE.open(encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            units = _units(row["covers"])
            for unit in units:
                index[unit].append((row["office"], row["warning_area"], len(units)))
    return {unit: tuple(rows) for unit, rows in index.items()}


def _stem(name: str) -> str:
    """`'구례군'` → `'구례'`. 특보구역명이 `'구례산간'` 처럼 이 어간으로 시작한다."""
    return name[:-1] if len(name) > 2 and name[-1] in "시군구" else name


def lookup(*, sido: str | None, sigungu: str | None, dong: str | None) -> tuple[str, ...]:
    """내 위치를 가리킬 수 있는 특보구역명들. 좁은 것부터. 모르면 빈 튜플.

    **여럿을 돌려주는 것이 맞다.** 하나를 고르라고 강요하면 고도(산지/평지)나 도서 제외처럼
    우리가 모르는 축에서 반드시 틀린다. `parse_pwn` 은 후보를 OR 로 찾으므로, 같은 시군구
    안의 형제 이름(`강릉`·`강릉평지`)이 함께 있는 것은 손해가 아니라 커버리지다.

    반대로 **다른 도시의 같은 이름은 반드시 걸러야 한다** — 서울 중구에게 `인천남부` 를
    주면 인천 특보를 자기 것으로 읽는다. 시도로 관서를 좁히고, 그래도 남으면 구역명이
    내 시군구·시도 어간으로 시작하는 것만 남긴다.
    """
    index = _index()
    # 읍면동이 먼저다 — 파주·용인·여주·양평·강원 산지처럼 시군구 아래로 쪼개진 곳이 있다.
    for key in (dong, sigungu):
        if not key:
            continue
        found = index.get(key)
        if not found:
            continue
        if sido:
            found = tuple(r for r in found if sido in OFFICE_SIDO.get(r[0], frozenset()))
        if len(found) > 1:
            for prefix in (_stem(sigungu or ""), sido or ""):
                if not prefix:
                    continue
                narrowed = tuple(r for r in found if r[1].startswith(prefix))
                if narrowed:
                    found = narrowed
                    break
        if found:
            # 관할이 좁은 것(=단위 수가 적은 것)을 앞에. 같으면 짧은 이름, 그다음 가나다.
            return tuple(area for _, area, _ in sorted(found, key=lambda r: (r[2], len(r[1]), r[1])))
    return ()


__all__ = ["OFFICE_SIDO", "TABLE_FILE", "lookup"]
