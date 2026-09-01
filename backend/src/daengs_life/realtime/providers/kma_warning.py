"""기상청 특보 — 모양 C. **구조화된 것이 거의 없다.**

`getPwnStatus` 는 `{other, t6, t7, tmEf, tmFc, tmSeq}` 이고 `t6` 에 전국 발효 현황이 **자연어
1,610자**로 들어온다 (§6.3). 구조화된 것은 `tmFc`·`tmSeq` 뿐이다.

**그래서 이 provider 는 텍스트 파싱을 진다.** ②-a 가 특보를 `Measurement` 가 아니라 `State` 로
보낸 이유가 여기 있다 — 값으로 넣으면 `value=None, grade='경보'` 라는 기형이 생기고 그 `None` 을
모든 룰이 특수 처리한다.

⚠️ **특보구역명 ↔ 내 행정구역 매핑이 필요하다.** 강남구를 "서울동남권"으로 옮겨야 `t6` 에서
나를 찾는다. `data/reference/` 캐시가 하나 더 생기는 자리이고, 그 표가 없으면 이 provider 는
아무것도 못 낸다 — 그래서 매핑을 **인자로 받는다**. 없으면 빈 목록이고 ⑤가 저하시킨다.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from ..config import KST
from ..observation import Source, State, StateKind
from ..transport import datagokr
from ..transport.base import Budget

PATH = "/1360000/WthrWrnInfoService"

# 기상청 특보 11종. 고정 목록이라 우리가 만든 것이 아니다.
KINDS: dict[str, StateKind] = {
    "폭염": StateKind.HEAT, "한파": StateKind.COLD, "호우": StateKind.HEAVY_RAIN,
    "대설": StateKind.HEAVY_SNOW, "강풍": StateKind.WIND, "건조": StateKind.DRY,
    "태풍": StateKind.TYPHOON, "황사": StateKind.YELLOW_DUST, "풍랑": StateKind.HIGH_SEAS,
    "폭풍해일": StateKind.STORM_SURGE, "지진해일": StateKind.TSUNAMI,
}
CATEGORIES = ("경보", "주의보")

# `o 폭염경보 : 경기도(김포, 오산), 서울(서울동남권, 서울동북권), 광주` 한 줄.
# `t7`(예비특보)은 이 모양이 아니라 `(1) 풍랑 예비특보` 로 시작하므로 여기 안 걸린다 — 의도다.
# 예비특보는 발효가 아니라 예고라 `covers()` 의 구간 의미가 다르고, 섞으면 "지금 폭염경보"가 된다.
_LINE = re.compile(r"^o\s*(?P<label>\S+?)\s*:\s*(?P<areas>.+)$")


def _split_kind(label: str) -> tuple[StateKind, str] | None:
    """`'폭염경보'` → `(HEAT, '경보')`. 모르는 라벨은 `None` — 지어내지 않는다."""
    for category in CATEGORIES:
        if label.endswith(category):
            kind = KINDS.get(label[: -len(category)])
            return (kind, category) if kind else None
    return None


def _mentions(areas_text: str, mine: Iterable[str]) -> bool:
    """내 특보구역이 그 줄에 있는가.

    괄호 안팎을 따로 파싱하지 않는다 — `'서울(서울동남권, 서울동북권)'` 에서 우리가 알고 싶은
    것은 "내 구역 이름이 들어 있나" 하나이고, 구조를 복원해도 쓸 데가 없다. 다만 **부분일치를
    피하려고 경계를 본다** — `'서울동남권'` 을 찾는데 `'서울동남권해상'` 에 걸리면 안 된다.
    """
    return any(re.search(rf"(?<![가-힣]){re.escape(name)}(?![가-힣])", areas_text)
               for name in mine if name)


def parse_pwn(body: dict, areas: Iterable[str]) -> list[State]:
    """`t6`(발효 중인 특보)에서 내 구역에 걸린 것만.

    `areas` 는 내 위치를 가리키는 이름들이다 — 특보구역명(`'서울동남권'`)과 광역명(`'서울'`)을
    함께 넘기면 둘 중 어느 표기로 실려도 잡힌다.
    """
    item = _first(body)
    if item is None:
        return []
    issued = _stamp(item.get("tmFc"))
    effective = _stamp(item.get("tmEf")) or issued
    if issued is None or effective is None:
        return []

    mine = list(areas)
    out: list[State] = []
    for raw_line in str(item.get("t6") or "").splitlines():
        matched = _LINE.match(raw_line.strip())
        if not matched:
            continue
        split = _split_kind(matched["label"])
        if split is None or not _mentions(matched["areas"], mine):
            continue
        kind, category = split
        out.append(State(
            kind=kind, category=category, area=mine[0],
            valid_from=effective,
            # **해제 시각이 없다.** 특보는 해제될 때 다음 통보로 알려지므로 `None` 이 정상이고,
            # 임의의 만료를 씌우면 아직 발효 중인 폭염경보가 조용히 사라진다 (State 의 covers).
            valid_to=None,
            issued_at=issued, source=Source.WARNING, detail=raw_line.strip()[:200]))
    return out


def _first(body: dict) -> dict | None:
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if isinstance(items, list):
        return items[0] if items else None
    return items if isinstance(items, dict) else None


def _stamp(raw: object) -> datetime | None:
    """`'202608251000'`."""
    try:
        return datetime.strptime(str(raw).strip(), "%Y%m%d%H%M").replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_pwn(*, stn: int = 108, budget: Budget | None = None) -> dict:
    return datagokr.get(f"{PATH}/getPwnStatus", {"stnId": stn, "numOfRows": 5}, budget=budget)


def fetch(areas: Iterable[str], *, stn: int = 108, budget: Budget | None = None) -> list[State]:
    """`stn=108` 은 전국 본청이다 — `t6` 이 전국 현황이라 지점을 좁힐 이유가 없다."""
    return parse_pwn(datagokr.get(f"{PATH}/getPwnStatus",
                                  {"stnId": stn, "numOfRows": 5}, budget=budget), areas)
