"""산책 적합도 판정 (RT-001 ③).

`judge(obs, t)` — **판정은 시각의 함수다.** "지금"은 `t = now` 인 특수 경우일 뿐이고, 그래서
"지금은 안 되지만 저녁엔 된다"가 같은 응답에 들어간다. 순간 판정만 만들면 나중에 시간축을
넣을 때 룰 시그니처와 ⑥ 응답이 함께 바뀌는데, 반대 방향은 인자 하나를 고정하는 것이라 공짜다.

**축 셋, 등급 셋, 결합은 최악 우선.** 축은 상쇄 관계가 아니다 — 대기질이 아주 좋다고 폭염경보가
지워지지 않는다. 가중합은 기각됐다: 가중치에 근거가 없고, 실측 케이스에서 열 `UNSAFE` + 대기질
`GOOD` 의 평균이 `CAUTION` 이 되어 **폭염경보 발효 중에 "조심해서 다녀오세요"** 라는 틀린 답이 된다.

**임계는 이 파일에 없다.** `thresholds.yaml` 에 있고 값마다 출처가 붙어 있다 (③-d).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

from .observation import Interval, Measurement, Observations, Q, State, StateKind

THRESHOLDS_FILE = Path(__file__).with_name("thresholds.yaml")


class Grade(IntEnum):
    """순서 있는 척도에는 **이 셋만** 넣는다.

    `UNKNOWN=0` 을 척도에 넣었던 것이 첫 안의 버그였다 — "UNKNOWN 은 최악 우선에 참여하지
    않는다"를 말로 막으면, 누군가 `min(...)` 을 쓰는 순간 조용히 `UNKNOWN` 이 이긴다.
    모름은 `None` 이고, `min()` 에 섞이면 타입이 터뜨린다.

    단계를 셋으로 둔 이유는 **사용자의 행동이 셋뿐**이라서다 (나간다 / 짧게 / 안 나간다).
    기관 등급(에어코리아 4단계·자외선 5단계)과는 별개 척도라 이름을 다르게 둔다.
    """

    UNSAFE = 1
    CAUTION = 2
    GOOD = 3


class Axis(StrEnum):
    HEAT = "heat"     # 체감온도 + 폭염·한파특보 (지열 보정은 P2)
    AIR = "air"       # PM10 · PM2.5 + 황사특보
    RAIN = "rain"     # PTY · 강수량 · 낙뢰 + 호우·대설·강풍·태풍특보


@dataclass(frozen=True)
class AxisVerdict:
    grade: Grade | None                      # None = 판정 근거 없음
    basis: list[Measurement | State]         # 그 등급의 근거가 된 값
    note: str = ""                           # 사람이 읽을 한 줄 ("체감온도 33.3℃")
    # 우리가 계산한 값. `note` 에도 같은 숫자가 들어가지만 저기는 **사람용 문장**이다.
    # ⑥ 응답의 `derived` 가 이 값을 요구하는데, 서빙 층이 문장에서 숫자를 파싱하게 두면
    # 문구를 다듬는 날 API 가 깨진다. `Q` 에 안 넣는 것은 ②-d-3 결정 2 그대로 —
    # 출처가 "우리"인 값을 관측량 어휘에 섞으면 "이 값이 어디서 왔나"가 흐려진다
    derived: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    at: datetime
    grade: Grade | None                      # 아는 축들의 최악. 전부 모르면 None
    axes: dict[Axis, AxisVerdict]
    dominant: list[Axis]                     # 최악을 만든 축 — 동률이면 여럿 ← "왜"
    unknown_axes: list[Axis]                 # 판정에 못 넣은 축 ← 신뢰도
    uv: AxisVerdict | None = None            # 부가 표시. **등급에 영향 없음** (③-b ③)
    capped: bool = False                     # ⑤-a 상한이 걸렸는가 (GOOD 으로 안 올렸다)


# --- 체감온도 (③-c) — 공식 산식을 손대지 않는다 -------------------------------

def wet_bulb_stull(ta: float, rh: float) -> float:
    """습구온도 근사 (Stull 2011). **API 가 습구온도를 안 주므로 한 겹 낀다** (통상 ±0.3℃)."""
    return (ta * math.atan(0.151977 * (rh + 8.313659) ** 0.5) + math.atan(ta + rh)
            - math.atan(rh - 1.67633) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
            - 4.686035)


def summer_feels(ta: float, rh: float) -> float:
    """기상청 여름철 체감온도 (2020~). **풍속을 쓰지 않는다.**

    그래서 여름에는 바람이 판정에 반영되지 않는다 — 체감상 시원해도 공식이 반영하지 않는데,
    공식을 그대로 쓰는 원칙(③-c)을 따른다.
    """
    tw = wet_bulb_stull(ta, rh)
    return -0.2442 + 0.55399 * tw + 0.45535 * ta - 0.0022 * tw ** 2 + 0.00278 * tw * ta + 3.0


def winter_feels(ta: float, wind_ms: float) -> float:
    """풍속냉각지수 (JAG/TI 2001). 습도를 쓰지 않는다."""
    v = (wind_ms * 3.6) ** 0.16                    # m/s -> km/h
    return 13.12 + 0.6215 * ta - 11.37 * v + 0.3965 * ta * v


def feels_like(ta: float, rh: float | None, wind_ms: float | None,
               limits: dict) -> tuple[float, str]:
    """`(체감온도, 어느 산식인가)`.

    **계절이 아니라 기온으로 가른다** — 월로 가르는 기상청 운영 방식을 쓰면 10월 이상고온이나
    4월 한파에서 엉뚱한 산식을 쓰게 된다. 우리가 판정하는 것은 "지금 이 격자"다.
    """
    if ta >= limits["summer_from_c"] and rh is not None:
        return summer_feels(ta, rh), "여름식"
    if (ta <= limits["winter_to_c"] and wind_ms is not None
            and wind_ms > limits["winter_wind_min_ms"]):
        return winter_feels(ta, wind_ms), "겨울식"
    return ta, "기온 그대로"


# --- 임계 ---------------------------------------------------------------------

@lru_cache(maxsize=1)
def thresholds() -> dict:
    return yaml.safe_load(THRESHOLDS_FILE.read_text(encoding="utf-8"))


# 풍랑·폭풍해일·지진해일은 해상용, 건조는 축이 없다 — 표에 없으면 무시된다 (③-d)
_WARNING_AXIS = {StateKind[name.upper()]: Axis(axis.lower())
                 for name, axis in thresholds()["warnings"]["axes"].items()}


def _warnings_for(obs: Observations, t: datetime, axis: Axis) -> list[State]:
    """그 시각 그 축에 걸린 특보.

    **끝이 열린 특보(`valid_to=None`)는 무한정 끌고 가지 않는다.** ③-a 가 특보 커버리지를
    "현재 발효만" 으로 적었고 ~T+6h 까지만 유지한다. 그대로 두면 발효 중인 폭염경보가 T+24h
    전체를 `UNSAFE` 로 만들어 새벽 3시 체감 28℃ 도 `UNSAFE` 가 되는데, 그건 ③-b ②가
    미래 대기질에서 기각한 것과 같은 근거 없는 외삽이다.

    끝이 명시된 상태(권역 일 등급 등)는 그 구간을 그대로 믿는다 — 기관이 정한 구간이다.
    """
    horizon = obs.fetched_at + timedelta(hours=thresholds()["warnings"]["carry_forward_hours"])
    return [s for s in obs.states_at(t)
            if _WARNING_AXIS.get(s.kind) is axis
            and (s.valid_to is not None or t <= horizon)]


def _value_at(obs: Observations, t: datetime, q: Q) -> Measurement | None:
    """그 시각의 값. **관측값을 미래로 늘려 쓰지 않는다.**

    `Observations.at()` 은 일부러 계단 함수다 (`valid_at <= t` 중 가장 늦은 것) — 14:00 실황을
    14:50 판정에 쓰려면 그래야 한다. 그런데 그 성질이 **미래 시각에서는 persistence 가 된다**:
    10시 PM10 측정값이 20시 판정에도, T+24h 에도 그대로 유효한 값으로 잡힌다.

    ③-b ②가 미래 대기질 persistence 를 기각한 이유가 그대로 걸린다 — 10시간 뒤 미세먼지가
    지금과 같다는 근거가 없고, 근거 없는 외삽을 만들어 판정에 쓰는 것이다. 실제로 이 가드가
    없을 때 권역 일 등급('나쁨')이 한 번도 발동하지 않았다.

    **판정식은 새 숫자를 안 만든다** — 조회 시점(`fetched_at`)보다 미래를 물었는데 돌아온 값이
    조회 시점 이전 것이면, 그건 예보가 아니라 관측을 늘린 것이므로 버린다. 예보값은 자기
    `valid_at` 이 미래라 그대로 통과한다.
    """
    m = obs.at(t, q)
    if m is None:
        return None
    if t > obs.fetched_at and m.valid_at <= obs.fetched_at:
        return None
    return m


# --- 축 판정 ------------------------------------------------------------------

def judge_heat(obs: Observations, t: datetime) -> AxisVerdict:
    warned = _warnings_for(obs, t, Axis.HEAT)
    temp = _value_at(obs, t, Q.TEMP)
    if temp is None:
        # 특보만으로 판정하지 않는다 — 값이 없으면 `HEAT` 는 모르는 축이다.
        # 다만 특보가 있으면 근거로 남겨 ⑥ 응답이 "특보는 떴는데 값을 못 받았다"를 말할 수 있다.
        return AxisVerdict(None, list(warned), "기온을 받지 못했다")

    limits, heat = thresholds()["feels_like"], thresholds()["heat"]
    humidity = _value_at(obs, t, Q.HUMIDITY)
    wind = _value_at(obs, t, Q.WIND)
    value, formula = feels_like(
        float(temp.value),
        float(humidity.value) if humidity else None,
        float(wind.value) if wind else None,
        limits)

    basis: list[Measurement | State] = [temp]
    basis += [m for m in (humidity, wind) if m is not None and formula != "기온 그대로"]
    basis += warned
    note = f"체감온도 {value:.1f}℃ ({formula})"
    derived = {"feels_like_c": round(value, 2)}

    if warned:
        # 특보는 **보강**이지 필수가 아니다 (⑤-a 근거 1) — 값만으로도 같은 답이 나오는 것이
        # ③-d 의 설계였다. 다만 떠 있으면 그 자체가 기관의 위험 판정이다.
        return AxisVerdict(Grade.UNSAFE, basis,
                           f"{note} · {warned[0].kind.name} {warned[0].category}", derived)
    if value >= heat["unsafe_from_c"] or value <= heat["cold_unsafe_below_c"]:
        return AxisVerdict(Grade.UNSAFE, basis, note, derived)
    if value >= heat["caution_from_c"] or (
            heat["cold_caution_from_c"] <= value < heat["cold_caution_below_c"]):
        return AxisVerdict(Grade.CAUTION, basis, note, derived)
    return AxisVerdict(Grade.GOOD, basis, note, derived)


def judge_air(obs: Observations, t: datetime) -> AxisVerdict:
    warned = _warnings_for(obs, t, Axis.AIR)
    if warned:
        return AxisVerdict(Grade.UNSAFE, list(warned), f"황사 {warned[0].category}")

    air = thresholds()["air"]
    grades: list[Grade] = []
    basis: list[Measurement | State] = []
    notes: list[str] = []

    for q, key, label in ((Q.PM10, "pm10", "PM10"), (Q.PM25, "pm25", "PM2.5")):
        m = _value_at(obs, t, q)
        if m is None:
            continue
        value = float(m.value)
        bounds = air[key]
        grade = (Grade.UNSAFE if value >= bounds["unsafe_from"]
                 else Grade.CAUTION if value >= bounds["caution_from"] else Grade.GOOD)
        grades.append(grade)
        basis.append(m)
        notes.append(f"{label} {value:g}㎍/㎥")

    if grades:
        # 값이 있으면 값으로 판정한다. `grade` 필드(기관 등급)를 안 읽는 이유는 ②-d-2 —
        # 산책 임계가 기관 등급 경계와 같을 이유가 없고, 붙여 두면 어느 쪽도 못 움직인다.
        return AxisVerdict(min(grades), basis, " · ".join(notes))

    # 미래 시각은 값이 없다 → **권역 일 등급**을 쓴다 (③-b ②). persistence 는 기각됐다 —
    # 6시간 뒤 미세먼지가 지금과 같다는 근거가 없고, 그건 가중합을 기각한 논리에 스스로 걸린다.
    for state in obs.states_at(t, StateKind.AIR_FORECAST):
        grade_name = air["forecast_labels"].get(state.category)
        if grade_name:
            return AxisVerdict(Grade[grade_name], [state],
                               f"{state.area} 권역 예보 {state.category}")
    return AxisVerdict(None, [], "대기질을 확인하지 못했다")


def judge_rain(obs: Observations, t: datetime) -> AxisVerdict:
    warned = _warnings_for(obs, t, Axis.RAIN)
    if warned:
        return AxisVerdict(Grade.UNSAFE, list(warned),
                           f"{warned[0].kind.name} {warned[0].category}")

    kind = _value_at(obs, t, Q.PRECIP_KIND)
    amount = _value_at(obs, t, Q.PRECIP_MM)
    lightning = _value_at(obs, t, Q.LIGHTNING)
    if kind is None and amount is None:
        return AxisVerdict(None, [], "강수를 확인하지 못했다")

    basis: list[Measurement | State] = [m for m in (kind, amount, lightning) if m is not None]
    if lightning is not None and float(lightning.value) > 0:
        return AxisVerdict(Grade.UNSAFE, basis, "낙뢰")

    raining = (kind is not None and kind.value.raw.strip() not in ("", "0")) or (
        amount is not None and isinstance(amount.value, Interval) and amount.value.hi > 0)
    if raining:
        # **강수량 자체로는 `UNSAFE` 를 만들지 않는다** — "몇 mm 부터 위험한가"를 우리가 정하는
        # 대신 호우특보를 기관의 위험 판정으로 쓴다 (③-d).
        label = amount.value.label if amount is not None else kind.value.name
        return AxisVerdict(Grade.CAUTION, basis, f"강수 {label}")
    return AxisVerdict(Grade.GOOD, basis, "강수 없음")


def _judge_uv(obs: Observations, t: datetime) -> AxisVerdict | None:
    """부가 표시. **등급에 영향이 없다** (③-b ③).

    자외선은 산책을 막지 않는다 — 개는 털이 있어 사람과 영향이 다르고, "UV 때문에 부적합"을
    뒷받침할 기준이 없다. 게다가 그 데이터는 apihub 403 이라 한 번도 보지 못했다.
    """
    m = _value_at(obs, t, Q.UV)
    return AxisVerdict(None, [m], f"자외선지수 {float(m.value):g}") if m else None


# --- 결합 (③-b · ⑤-a) --------------------------------------------------------

def judge(obs: Observations, t: datetime) -> Verdict:
    """한 시각의 판정. 축을 셋 내고 **최악 우선**으로 합친다."""
    axes = {Axis.HEAT: judge_heat(obs, t),
            Axis.AIR: judge_air(obs, t),
            Axis.RAIN: judge_rain(obs, t)}
    known = {axis: v.grade for axis, v in axes.items() if v.grade is not None}
    unknown = [axis for axis, v in axes.items() if v.grade is None]

    # ⑤-a 근거 2 — **축은 3개지만 실패 단위는 2개다.** `HEAT` 와 `RAIN` 은 같은 provider
    # (기상청 격자)에서 온다. 둘이 함께 죽으면 열도 강수도 모르는 것이므로 산책 판정 자체가
    # 성립하지 않는다. "아는 축이 하나도 없으면" 으로 쓰면 **대기질만 살아 있을 때 그것으로
    # 답해 버린다** — 미세먼지가 좋다는 이유로 폭염 여부를 모른 채 답하는 셈이다.
    if axes[Axis.HEAT].grade is None and axes[Axis.RAIN].grade is None:
        return Verdict(t, None, axes, [], unknown, _judge_uv(obs, t))
    if not known:
        return Verdict(t, None, axes, [], unknown, _judge_uv(obs, t))

    worst = min(known.values())
    capped = False
    if unknown and worst is Grade.GOOD:
        # ⑤-a 근거 3 — **"모르는 것을 좋다고 말하지 않는다."** 상한은 `GOOD` 만 막으므로
        # 위험은 놓치지 않는다. 이게 없으면 미세먼지가 매우나쁨인 날에 "좋음"이라고 말한다.
        worst, capped = Grade.CAUTION, True

    # `dominant` 는 "왜 나쁜가"의 답이다. `GOOD` 일 때는 답할 것이 없으므로 비운다.
    dominant = [axis for axis, g in known.items() if g == worst] if worst < Grade.GOOD else []
    return Verdict(t, worst, axes, dominant, unknown, _judge_uv(obs, t), capped)


def timeline(obs: Observations, start: datetime, hours: int = 24,
             step_hours: int = 1) -> list[Verdict]:
    """T+`hours` 까지의 판정. **상한은 정책이고, 그 안의 판정 가능 여부는 데이터가 정한다** (③-a).

    `MAX_HORIZON = 6h` 같은 상수는 두 방향으로 동시에 틀린다 — 기상은 그보다 훨씬 멀리 가고
    대기질 값은 `t=now` 뿐이다. 그래서 여기서는 시각만 걸어가고, 못 채우는 축은 각 `Verdict` 의
    `unknown_axes` 가 스스로 말한다.

    **첫 점만 `start` 그대로이고 나머지는 정시에 맞춘다.** 예보값이 시간 단위라 11:47 지점은
    사실 11:00 발표값을 쓰는데(`at()` 이 계단 함수다), 라벨까지 11:47 로 두면 `windows` 가
    "20:47부터 좋다"처럼 **없는 정밀도**를 말하게 된다. 첫 점을 남기는 것은 T+0 이 진짜
    지금이어야 권장 구간이 "지금부터"를 표현할 수 있어서다. `start` 가 이미 정시면 종전과 같다.
    """
    end = start + timedelta(hours=hours)
    first = start.replace(minute=0, second=0, microsecond=0)
    if first < start:
        first += timedelta(hours=step_hours)

    marks: list[datetime] = []
    mark = first
    while mark <= end:
        marks.append(mark)
        mark += timedelta(hours=step_hours)

    points = marks if first == start else [start, *marks]
    return [judge(obs, t) for t in points]


def windows(verdicts: list[Verdict], at_least: Grade = Grade.GOOD) -> list[tuple[datetime, datetime]]:
    """`at_least` 이상이 이어지는 구간 — ⑥ 응답의 "산책 권장 시간대".

    끝 시각은 **마지막으로 좋은 시각**이다. 그다음 판정까지 좋다고 말하면 없는 것을 지어낸다.
    """
    spans: list[tuple[datetime, datetime]] = []
    start: datetime | None = None
    previous: datetime | None = None
    for v in verdicts:
        if v.grade is not None and v.grade >= at_least:
            start = start if start is not None else v.at
            previous = v.at
        elif start is not None and previous is not None:
            spans.append((start, previous))
            start = previous = None
    if start is not None and previous is not None:
        spans.append((start, previous))
    return spans
