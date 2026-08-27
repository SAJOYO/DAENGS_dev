"""상태 편집 툴. 순수 함수 (state, args) → state. UI도 LLM도 이걸 부른다.

**툴은 정책별로 묶여 있다** (state.py 참조):
  TARGET_TOOLS  — 어디를 갈까. 결과 집합을 바꾼다
  JOURNEY_TOOLS — 어떻게 갈까. 결과를 빼지 않는다
  VIEW_TOOLS    — 표시·세션 (정렬, undo, reset)

새 툴을 만들면 셋 중 하나에 넣어라. 어디에도 안 들어가면 정책이 불분명한 것이다.
'min_rating' 같은 툴은 의도적으로 없다 (condition-schema.md).

**툴은 history 를 건드리지 않는다.** 되돌림 지점은 턴 경계에서 한 번 찍힌다
(`checkpoint` ← app/discovery/refine/engine.py). undo 의 단위는 툴이 아니라 사용자의 한 마디다.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from pydantic import ConfigDict, Field, ValidationError, validate_call

from app.discovery.semantics import TimeIntent, TimeKind, Urgency
from app.discovery.state import MAX_HISTORY, EditableState, Sort
from app.providers.base import Mode

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
RadiusM = Annotated[int, Field(ge=100, le=20000)]
PositiveMinutes = Annotated[int, Field(ge=1, le=1440)]
PositiveId = Annotated[int, Field(ge=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
ShortTag = Annotated[str, Field(min_length=1, max_length=64)]


class ToolInputError(ValueError):
    """사용자·LLM이 보낸 툴 이름 또는 인자가 계약에 맞지 않는다."""


def _edit(state: EditableState) -> EditableState:
    """편집용 사본. **history 는 건드리지 않는다.**

    되돌림 지점은 툴이 아니라 턴 경계에서 찍는다 (`checkpoint`, app/discovery/refine/engine.py).
    툴마다 찍으면 "걸어서 15분 안에" 한 마디(set_mode + set_max_total_min)가 스택 두 칸을 먹고,
    undo 한 번이 사용자가 말한 적 없는 중간 상태를 만든다.
    """
    return state.model_copy(deep=True)


def checkpoint(before: EditableState, after: EditableState) -> EditableState:
    """`after` 에 `before` 로 돌아갈 지점 하나를 남긴다. 턴당 한 번만 불린다."""
    s = after.model_copy(deep=True)
    s.history = (before.history + [before.snapshot()])[-MAX_HISTORY:]
    return s


# ---------------------------------------------------------------- TARGET (필터)
def set_origin(state: EditableState, lat: Latitude, lng: Longitude) -> EditableState:
    s = _edit(state); s.lat, s.lng = lat, lng; return s


def set_radius(state: EditableState, m: RadiusM) -> EditableState:
    s = _edit(state); s.target.radius_m = max(100, min(20000, int(m))); return s


def widen(state: EditableState, factor: Annotated[float, Field(gt=0, le=10)] = 2.0) -> EditableState:
    return set_radius(state, int(state.target.radius_m * factor))


def narrow(state: EditableState, factor: Annotated[float, Field(gt=0, le=10)] = 0.5) -> EditableState:
    return set_radius(state, int(state.target.radius_m * factor))


# `set_time(open_now, night, emergency)` 하나였다. 셋은 같은 종류가 아니다 —
# 영업 여부는 검색 조건, 야간·응급은 병원의 표방, 긴급도는 이번 상황의 사실이다.
# 한 툴에 묶어두니 정책 라벨이 하나뿐이라 '응급'이 target 변경으로만 보고됐다.
def set_open_now(state: EditableState, on: bool = True) -> EditableState:
    s = _edit(state); s.target.open_now = on; return s


def set_night_service(state: EditableState, on: bool = True) -> EditableState:
    """야간진료를 **표방하는** 곳만. 지금이 밤이냐는 `set_time_intent` 다."""
    s = _edit(state); s.target.night_service = on; return s


def set_emergency_service(state: EditableState, on: bool = True) -> EditableState:
    """응급을 **표방하는** 곳만. 이번 상황이 급하냐는 `set_urgency` 다."""
    s = _edit(state); s.target.emergency_service = on; return s


def note_symptoms(state: EditableState,
                  terms: Annotated[list[ShortText], Field(max_length=20)]) -> EditableState:
    """증상 표현을 **말 그대로** 남긴다. 과목 번역이 아니다.

    이 말을 읽던 커뮤니티 검색은 #63, 과목 축은 #64 로 없어졌다 — 지금은 기록만 한다.
    """
    s = _edit(state); s.target.symptoms = sorted(set(s.target.symptoms) | set(terms)); return s


def clear_symptoms(state: EditableState,
                   terms: Annotated[list[ShortText], Field(max_length=20)] | None = None) -> EditableState:
    s = _edit(state)
    s.target.symptoms = [] if terms is None else [t for t in s.target.symptoms if t not in terms]
    return s


def require(state: EditableState, tags: Annotated[list[ShortTag], Field(max_length=20)]) -> EditableState:
    s = _edit(state); s.target.require_tags = sorted(set(s.target.require_tags) | set(tags)); return s


def unrequire(state: EditableState, tags: Annotated[list[ShortTag], Field(max_length=20)]) -> EditableState:
    s = _edit(state); s.target.require_tags = [t for t in s.target.require_tags if t not in tags]; return s


def exclude(state: EditableState,
            ids: Annotated[list[PositiveId], Field(max_length=100)]) -> EditableState:
    s = _edit(state); s.target.exclude_ids = sorted(set(s.target.exclude_ids) | set(ids)); return s


def pin(state: EditableState,
        ids: Annotated[list[PositiveId], Field(max_length=100)]) -> EditableState:
    s = _edit(state); s.target.pin_ids = sorted(set(s.target.pin_ids) | set(ids)); return s


# ------------------------------------------------------- CONTEXT (상황 — 사실)
# 여기 값들은 target 과 journey **둘 다**를 먹인다. 그래서 어느 한쪽 상자에 못 들어간다.
def set_urgency(state: EditableState, level: Urgency) -> EditableState:
    """이번 상황의 긴급도. **사용자가 말한 것만** 여기 들어온다.

    긴급도는 조건을 좁히지 않는다 — 수단 우선순위·정렬·안내 문구를 바꿀 뿐이다.
    '응급 병원을 찾겠다'는 요구는 `set_emergency_service` 로 따로 간다.
    """
    s = _edit(state); s.urgency = level
    return s


def set_time_intent(state: EditableState, kind: TimeKind | None = None,
                    at: datetime | None = None) -> EditableState:
    """시각과 그 **뜻**. kind 없이 부르면 해제 (= 지금)."""
    s = _edit(state)
    if (kind is None) != (at is None):
        raise ValueError("kind and at must be supplied together")
    s.time_intent = TimeIntent(kind=kind, at=at) if (kind and at) else None
    return s


# ------------------------------------------------- JOURNEY / 수단 (scope: any)
def set_mode(state: EditableState, mode: Mode | None) -> EditableState:
    """어느 leg 를 앞에 놓을지의 선호. Transport 는 늘 셋 다 반환한다."""
    s = _edit(state); s.journey.preferred_mode = mode
    if mode in ("walk", "car", "transit") and s.sort == "distance":
        s.sort = "duration"
    return s


def set_max_total_min(state: EditableState, minutes: PositiveMinutes | None,
                      hard: bool | None = None) -> EditableState:
    """**전체 이동시간** 상한. 수단 무관 — 차든 도보든 같은 잣대다.

    개가 걸어도 되는 시간은 `set_walk_max_min` 이다. 둘을 한 값으로 쓰면
    '차로 10분'과 '노견 도보 10분'이 같은 뜻이 된다.
    hard=True 일 때만 결과에서 제외 (정책 경계를 넘는 유일한 지점).
    """
    s = _edit(state); s.journey.max_total_min = minutes
    if hard is not None: s.journey.hard_limit = hard
    return s


# ------------------------------------------------ JOURNEY / 도보 (scope: walk)
# 이 그룹은 preferred_mode 를 건드리지 않는다. '계단 없는 길로'가 도보를 함의한다면
# 그건 자연어 층이 set_mode(walk) 를 **따로 내는** 것이지, 툴이 몰래 세울 일이 아니다.
def set_walk_max_min(state: EditableState, minutes: PositiveMinutes | None) -> EditableState:
    """**개가 걸어도 되는 시간** 상한. 전체 이동시간(`set_max_total_min`)과 다르다."""
    s = _edit(state); s.journey.walk.max_walk_min = minutes
    return s


# ------------------------------------------------------------------ VIEW (표시)
def set_sort(state: EditableState, sort: Sort) -> EditableState:
    s = _edit(state); s.sort = sort
    return s


def undo(state: EditableState) -> EditableState:
    if not state.history:
        return state
    prev = EditableState(**state.history[-1])
    prev.history = state.history[:-1]
    return prev


def reset(state: EditableState) -> EditableState:
    """**필터 초기화.** 상황(context)은 남긴다 — 사실은 초기화의 대상이 아니다.

    지금이 밤이고 급한 건 필터를 푼다고 달라지지 않는다. 지우려면 각각 따로 부른다.
    """
    return EditableState(lat=state.lat, lng=state.lng, history=list(state.history),
                         time_intent=state.time_intent, urgency=state.urgency)


TARGET_TOOLS: dict[str, Callable[..., EditableState]] = {
    "set_origin": set_origin, "set_radius": set_radius, "widen": widen, "narrow": narrow,
    "set_open_now": set_open_now, "set_night_service": set_night_service,
    "set_emergency_service": set_emergency_service,
    "note_symptoms": note_symptoms, "clear_symptoms": clear_symptoms,
    "require": require, "unrequire": unrequire,
    "exclude": exclude, "pin": pin,
}
# 상황은 정책이 아니라 **두 정책의 입력**이다. 그래서 네 번째 그룹이 된다.
CONTEXT_TOOLS: dict[str, Callable[..., EditableState]] = {
    "set_urgency": set_urgency, "set_time_intent": set_time_intent,
}
# 수단 선택은 scope="any", 수단별 설정은 그 수단 scope. 계층이 다르다.
MODE_TOOLS: dict[str, Callable[..., EditableState]] = {
    "set_mode": set_mode, "set_max_total_min": set_max_total_min,
}
WALK_TOOLS: dict[str, Callable[..., EditableState]] = {"set_walk_max_min": set_walk_max_min}
JOURNEY_TOOLS: dict[str, Callable[..., EditableState]] = {**MODE_TOOLS, **WALK_TOOLS}
VIEW_TOOLS: dict[str, Callable[..., EditableState]] = {
    "set_sort": set_sort, "undo": undo, "reset": reset,
}
# 스택을 **소비**하는 툴. 이 툴이 낀 턴은 되돌림 지점을 남기지 않는다 —
# 남기면 undo 가 방금 찍힌 자기 지점을 도로 팝해서 아무 일도 안 일어난다.
STACK_TOOLS: frozenset[str] = frozenset({"undo"})
TOOLS: dict[str, Callable[..., EditableState]] = {
    **CONTEXT_TOOLS, **TARGET_TOOLS, **JOURNEY_TOOLS, **VIEW_TOOLS,
}


def _legacy_set_time(state: EditableState, open_now: bool | None = None,
                     night: bool | None = None,
                     emergency: bool | None = None) -> EditableState:
    """v1 클라이언트의 복합 툴. 새 툴 목록에는 노출하지 않고 입력만 이행한다."""
    s = _edit(state)
    if open_now is not None:
        s.target.open_now = open_now
    if night is not None:
        s.target.night_service = night
    if emergency is not None:
        s.target.emergency_service = emergency
    return s


LEGACY_TOOLS: dict[str, Callable[..., EditableState]] = {"set_time": _legacy_set_time}
_VALIDATED_TOOLS = {
    name: validate_call(fn, config=ConfigDict(extra="forbid"))
    for name, fn in {**TOOLS, **LEGACY_TOOLS}.items()
}


def policy_of(tool: str) -> str:
    if tool in CONTEXT_TOOLS: return "context"
    if tool in TARGET_TOOLS: return "target"
    if tool in JOURNEY_TOOLS: return "journey"
    if tool in VIEW_TOOLS: return "view"
    return "unknown"


def scope_of(tool: str) -> str:
    """어느 수단에서만 의미가 있나. 정책(policy)과 **직교하는 축**이다.

    'any' = 수단 무관 (반경·정렬·전체 이동시간), 'walk' = 도보로 갈 때만.
    """
    return "walk" if tool in WALK_TOOLS else "any"


# LLM에 주는 툴 설명. policy(결과를 바꾸나)와 scope(어느 수단에서만 의미 있나)를 같이 준다.
TOOL_SPECS: list[dict[str, Any]] = [
    {"policy": "target", "scope": "any", "name": "set_origin", "desc": "검색 기준 좌표 변경 (집 근처 등)", "args": {"lat": "float", "lng": "float"}},
    {"policy": "target", "scope": "any", "name": "set_radius", "desc": "검색 반경(m)", "args": {"m": "int"}},
    {"policy": "target", "scope": "any", "name": "widen", "desc": "반경 넓히기(기본 2배)", "args": {"factor": "float?"}},
    {"policy": "target", "scope": "any", "name": "narrow", "desc": "반경 좁히기(기본 절반)", "args": {"factor": "float?"}},
    {"policy": "target", "scope": "any", "name": "set_open_now", "desc": "지금 영업중인 곳만", "args": {"on": "bool?"}},
    {"policy": "target", "scope": "any", "name": "set_night_service", "desc": "야간진료를 표방하는 곳만. '지금이 밤이다'가 아니다", "args": {"on": "bool?"}},
    {"policy": "target", "scope": "any", "name": "set_emergency_service", "desc": "응급을 표방하는 곳만. '지금 급하다'가 아니다", "args": {"on": "bool?"}},
    {"policy": "context", "scope": "any", "name": "set_urgency", "desc": "이번 상황의 긴급도 normal|urgent. 조건을 좁히지 않는다 — 병원 종류 요구는 set_emergency_service", "args": {"level": "str"}},
    {"policy": "context", "scope": "any", "name": "set_time_intent", "desc": "시각과 그 뜻. kind=depart_at(출발)|arrive_by(도착기한)|service_at(그 시각에 여는 병원)", "args": {"kind": "str?", "at": "str?"}},
    {"policy": "target", "scope": "any", "name": "note_symptoms", "desc": "증상 표현을 말 그대로 기록 (진단 금지). 현재 이 값을 읽는 검색 경로는 없다", "args": {"terms": "list[str]"}},
    {"policy": "target", "scope": "any", "name": "clear_symptoms", "desc": "증상 기록 해제. terms 없으면 전부", "args": {"terms": "list[str]?"}},
    {"policy": "target", "scope": "any", "name": "require", "desc": "필수 태그 (24h, center, secondary)", "args": {"tags": "list[str]"}},
    {"policy": "target", "scope": "any", "name": "unrequire", "desc": "필수 태그 해제", "args": {"tags": "list[str]"}},
    {"policy": "target", "scope": "any", "name": "exclude", "desc": "결과에서 제외할 병원 id (화면 순번→id는 shown_ids로 매핑)", "args": {"ids": "list[int]"}},
    {"policy": "target", "scope": "any", "name": "pin", "desc": "위로 고정할 병원 id", "args": {"ids": "list[int]"}},
    {"policy": "journey", "scope": "any", "name": "set_mode", "desc": "선호 이동수단 walk|car|transit|null. 도보 설정을 바꾸고 싶으면 이걸 따로 부를 것", "args": {"mode": "str?"}},
    {"policy": "journey", "scope": "any", "name": "set_max_total_min", "desc": "전체 이동시간 상한(분), 수단 무관. hard=true면 초과를 결과에서 뺀다(기본 false: 표시만)", "args": {"minutes": "int?", "hard": "bool?"}},
    {"policy": "journey", "scope": "walk", "name": "set_walk_max_min", "desc": "개가 걸어도 되는 시간 상한(분). 전체 이동시간과 다르다", "args": {"minutes": "int?"}},
    {"policy": "view", "scope": "any", "name": "set_sort", "desc": "정렬 distance|duration|open_first", "args": {"sort": "str"}},
    {"policy": "view", "scope": "any", "name": "undo", "desc": "직전 상태로", "args": {}},
    {"policy": "view", "scope": "any", "name": "reset", "desc": "필터 초기화", "args": {}},
    {"policy": "view", "scope": "any", "name": "ask", "desc": "의도가 불명확할 때 되묻기. 상태 변경 없음", "args": {"question": "str"}},
]


def apply(state: EditableState, tool: str, args: dict[str, Any] | None = None) -> EditableState:
    fn = _VALIDATED_TOOLS.get(tool)
    if fn is None:
        raise ToolInputError(f"unknown tool: {tool}")
    try:
        return fn(state, **(args or {}))
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ToolInputError(f"invalid args for {tool}: {exc}") from exc
