"""검문소 C — 판정이 ADR 의 실측 검산을 재현하는가 (RT-001 구현 계획 7 · ③ 전체).

두 종류를 확인한다.

1. **산식이 ③-c 의 검산값을 그대로 낸다** — 습구 26.25℃ · 체감 33.27℃, 그리고 습도만 바꾼 표.
   이건 숫자 대조라 완전하다.
2. **결합 규칙이 설계대로 움직인다** — 최악 우선 · ⑤-a 상한 · `dominant` · 특보 유지 범위.

> ⚠️ **8/24 드라이런을 통째로 재현하지는 못한다.** 그날의 예보 응답을 픽스처로 남기지 않았고
> (프로브는 레포에 없다), ADR 이 적어 둔 것은 시각별 습도가 아니라 결과 요약이다. 그래서
> **완전히 명세된 14:00 지점 케이스는 값 그대로 재현**하고, 시간축은 8/25 픽스처로 확인한다.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daengs_life.realtime.config import KST
from daengs_life.realtime.geo import Grid, LatLon
from daengs_life.realtime.observation import (
    Code, Interval, Measurement, Observations, ProviderResult, Q, ResolvedLocation, Source,
    State, StateKind,
)
from daengs_life.realtime.providers import airkorea_realtime, kma_vilage_fcst, kma_warning
from daengs_life.realtime.rules import (
    THRESHOLDS_FILE, Axis, Grade, feels_like, judge, summer_feels, thresholds, timeline,
    wet_bulb_stull, windows, winter_feels,
)

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
HERE = LatLon(37.4979, 127.0276)
GRID = Grid(61, 125)


def at(hour: int, day: int = 24, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=KST)


def m(q: Q, value, *, valid: datetime, source: Source = Source.NCST, grade=None) -> Measurement:
    return Measurement(q, value, valid_at=valid, issued_at=valid, source=source,
                       spatial_ref="격자 61,125", grade=grade)


def observe(measurements=(), states=(), *, now: datetime = at(14)) -> Observations:
    return Observations(
        ResolvedLocation(HERE, GRID, "역삼동", station="강남대로"),
        now, list(measurements), list(states), [])


# ------------------------------------------------------- ③-c 산식 (숫자 대조)

def test_the_formula_reproduces_the_recorded_check() -> None:
    """③-c 실측 검산 — 2026-08-24 14:00 강남, 기온 33.1℃ · 습도 57%."""
    assert wet_bulb_stull(33.1, 57) == pytest.approx(26.25, abs=0.005)
    assert summer_feels(33.1, 57) == pytest.approx(33.27, abs=0.005)


# --- RT-004 산식 검산 ---------------------------------------------------------
#
# **위의 두 테스트는 우리 구현이 우리 구현과 같은지만 본다.** 기록된 33.27 도 우리가 낸 값이라,
# 옮겨 적을 때 계수 하나를 잘못 쳤어도 셋이 사이좋게 틀린 채로 통과한다. E3 가 "산식 검산"을
# 남겨 둔 이유가 그것이고, 아래가 그 자리를 메운다 — **공표된 식을 다른 형태로 다시 적어**
# 우리 것과 대조한다. 계수 오타는 여기서만 잡힌다.

def _published_summer(ta: float, rh: float) -> float:
    """기상청 여름철 체감온도 (2022-06-02 개정). 공표형 그대로 — 항 묶음이 우리와 다르다.

        체감온도 = 0.45535×Ta + 3.0 - 0.2442 + (0.55399 + 0.00278×Ta)×Tw - 0.0022×Tw²
    """
    tw = _published_stull(ta, rh)
    return 0.45535 * ta + 3.0 - 0.2442 + (0.55399 + 0.00278 * ta) * tw - 0.0022 * tw ** 2


def _published_stull(ta: float, rh: float) -> float:
    """Stull(2011) 습구온도 근사. **API 가 습구온도를 안 줘서 한 겹 낀다** (③-c)."""
    return (ta * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(ta + rh) - math.atan(rh - 1.67633)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def _published_winter(ta: float, wind_kmh: float) -> float:
    """풍속냉각지수 (JAG/TI 2001). **풍속은 km/h** — 우리 쪽은 m/s 를 받아 3.6 을 곱한다.

        체감온도 = 13.12 + 0.6215×T + (0.3965×T - 11.37)×V^0.16
    """
    return 13.12 + 0.6215 * ta + (0.3965 * ta - 11.37) * wind_kmh ** 0.16


@pytest.mark.parametrize("ta", [25.0, 28.0, 30.0, 33.1, 36.0, 40.0])
@pytest.mark.parametrize("rh", [10.0, 30.0, 57.0, 70.0, 90.0, 100.0])
def test_the_summer_formula_matches_the_published_one(ta: float, rh: float) -> None:
    """여름식과 그 안의 습구온도 근사를 **공표형과 항등**으로 확인한다.

    `abs=1e-9` 는 실수 재배열 오차만 허용하는 값이다 — 계수가 한 자리라도 다르면 이 폭을
    한참 넘는다. 근사(±0.3℃)의 오차는 **여기서 재는 것이 아니다**: 우리도 공표형도 같은
    Stull 을 쓰므로 그 오차는 양쪽에 똑같이 들어 있고, 이 테스트는 옮겨 적기만 본다.
    """
    assert wet_bulb_stull(ta, rh) == pytest.approx(_published_stull(ta, rh), abs=1e-9)
    assert summer_feels(ta, rh) == pytest.approx(_published_summer(ta, rh), abs=1e-9)


@pytest.mark.parametrize("ta", [10.0, 5.0, 0.0, -5.0, -15.0])
@pytest.mark.parametrize("wind_ms", [1.4, 3.0, 5.0, 10.0])
def test_the_winter_formula_matches_the_published_one(ta: float, wind_ms: float) -> None:
    """겨울식은 습구온도가 안 껴서 **정확히** 맞아야 한다.

    같이 확인하는 것이 단위다 — 공표식의 `V` 는 **km/h** 인데 우리가 받는 `WSD` 는 m/s 다.
    3.6 을 빠뜨리면 값이 조용히 따뜻해지는데(풍속을 1/3.6 로 읽으므로) 예외가 하나도 안 난다.
    """
    assert winter_feels(ta, wind_ms) == pytest.approx(
        _published_winter(ta, wind_ms * 3.6), abs=1e-9)


def test_the_wind_unit_conversion_is_not_silently_droppable() -> None:
    """위 테스트가 단위를 정말 잡는지 — 3.6 을 빼면 다른 답이 나오는 것을 명시해 둔다."""
    assert winter_feels(-5.0, 3.0) != pytest.approx(_published_winter(-5.0, 3.0), abs=0.1)


@pytest.mark.parametrize(("rh", "expected"), [(40, 31.5), (50, 32.6), (57, 33.3),
                                              (70, 34.4), (80, 35.3)])
def test_humidity_alone_moves_the_verdict_across_a_threshold(rh: int, expected: float) -> None:
    """③-c 의 표 — 기온 33.1℃ 고정, 습도만으로 **3.8℃**가 움직인다.

    이것이 "왜 기온이 아니라 체감온도인가"의 답이고, ③-b 가 축 사이 누적 보정을 안 하는 대신
    기온 × 습도는 축 **안에서** 처리한다고 한 자리다.
    """
    assert summer_feels(33.1, rh) == pytest.approx(expected, abs=0.05)


def test_the_formula_is_chosen_by_temperature_not_by_month() -> None:
    """월로 가르면 10월 이상고온이나 4월 한파에서 엉뚱한 산식을 쓴다 (③-c 기각 사유)."""
    limits = thresholds()["feels_like"]
    assert feels_like(33.1, 57, 1.7, limits)[1] == "여름식"
    assert feels_like(5.0, 50, 3.0, limits)[1] == "겨울식"
    assert feels_like(15.0, 50, 3.0, limits)[1] == "기온 그대로"
    # 겨울식은 **공식 적용 조건**이 풍속 > 1.3 m/s 다 — 바람이 약하면 쓰지 않는다
    assert feels_like(5.0, 50, 0.5, limits)[1] == "기온 그대로"


def test_wind_chill_is_below_the_air_temperature() -> None:
    assert winter_feels(-5.0, 4.0) < -5.0


def test_a_missing_input_falls_back_rather_than_guessing() -> None:
    """습도를 못 받으면 여름식을 못 쓴다 — 없는 값을 지어내지 않고 기온을 쓴다."""
    assert feels_like(33.1, None, 1.7, thresholds()["feels_like"]) == (33.1, "기온 그대로")


# ------------------------------------------ 검문소 C — 8/24 14:00 지점 케이스

def _dry_run_1400() -> Observations:
    """ADR 이 완전히 명세한 케이스: 기온 33.1 · 습도 57 · PM10 37 · PM2.5 29 · PTY 0 + 폭염경보."""
    heat = State(StateKind.HEAT, "경보", "서울동남권", valid_from=at(11), valid_to=None,
                 issued_at=at(10), source=Source.WARNING)
    return observe([
        m(Q.TEMP, 33.1, valid=at(14)),
        m(Q.HUMIDITY, 57.0, valid=at(14)),
        m(Q.WIND, 1.7, valid=at(14)),
        m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
        m(Q.PRECIP_MM, Interval("0", 0.0, 0.0), valid=at(14)),
        m(Q.PM10, 37.0, valid=at(14), source=Source.AIRKOREA, grade=2),
        m(Q.PM25, 29.0, valid=at(14), source=Source.AIRKOREA, grade=2),
    ], [heat], now=at(14))


def test_the_dry_run_verdict_is_reproduced() -> None:
    """ADR ③-d 검문소 — `HEAT=UNSAFE` · `AIR=GOOD` · `RAIN=GOOD` → `UNSAFE`, `dominant=[HEAT]`."""
    v = judge(_dry_run_1400(), at(14))
    assert v.axes[Axis.HEAT].grade is Grade.UNSAFE
    assert v.axes[Axis.AIR].grade is Grade.GOOD        # 둘 다 보통이라 GOOD (환경부 4→3 압축)
    assert v.axes[Axis.RAIN].grade is Grade.GOOD
    assert v.grade is Grade.UNSAFE
    assert v.dominant == [Axis.HEAT]
    assert not v.unknown_axes and not v.capped
    assert "33.3" in v.axes[Axis.HEAT].note


def test_the_heat_verdict_holds_without_the_warning() -> None:
    """⑤-a 근거 1 — **특보는 보강이지 필수가 아니다.** 값만으로 같은 답이 나온다.

    ③-d 가 "특보가 아니라 값으로 판정한다"고 정한 것이 ⑤의 견고성을 만들어 준 자리다.
    """
    obs = _dry_run_1400()
    without = observe(obs.measurements, [], now=at(14))     # 특보 provider 가 죽은 경우
    v = judge(without, at(14))
    assert v.axes[Axis.HEAT].grade is Grade.UNSAFE          # 체감 33.27 ≥ 33
    assert v.grade is Grade.UNSAFE


# --------------------------------------------------------- ③-b 결합 규칙

def test_worst_first_not_a_weighted_average() -> None:
    """가중합이면 열 `UNSAFE` + 대기질 `GOOD` 이 `CAUTION` 이 된다.

    **폭염경보 발효 중에 "조심해서 다녀오세요"는 틀린 답이다.** 축은 상쇄 관계가 아니다.
    """
    v = judge(_dry_run_1400(), at(14))
    grades = [a.grade for a in v.axes.values()]
    assert Grade.UNSAFE in grades and Grade.GOOD in grades
    assert v.grade is Grade.UNSAFE, "상쇄가 일어났다"


def test_unknown_is_not_on_the_scale() -> None:
    """첫 안의 버그 — `UNKNOWN=0` 을 척도에 넣으면 `min()` 을 쓰는 순간 조용히 이긴다."""
    assert [g.name for g in Grade] == ["UNSAFE", "CAUTION", "GOOD"]
    assert min(Grade) is Grade.UNSAFE


def test_dominant_is_empty_when_nothing_is_wrong() -> None:
    """`dominant` 는 "왜 나쁜가"의 답이다. `GOOD` 일 때는 답할 것이 없다."""
    v = judge(observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                       m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
                       m(Q.PM10, 10.0, valid=at(14), source=Source.AIRKOREA)], now=at(14)), at(14))
    assert v.grade is Grade.GOOD and v.dominant == []


def test_dominant_lists_every_axis_that_ties() -> None:
    v = judge(observe([
        m(Q.TEMP, 32.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),     # 체감 31.5 -> CAUTION
        m(Q.PRECIP_KIND, Code("rain", "1"), valid=at(14)),                     # 비 -> CAUTION
        m(Q.PM10, 10.0, valid=at(14), source=Source.AIRKOREA),                 # GOOD
    ], now=at(14)), at(14))
    assert v.grade is Grade.CAUTION
    assert sorted(v.dominant) == sorted([Axis.HEAT, Axis.RAIN])


def test_uv_is_carried_but_changes_nothing() -> None:
    """③-b ③ — 자외선은 산책을 막지 않는다. 개는 털이 있고 뒷받침할 기준이 없다."""
    base = [m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
            m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
            m(Q.PM10, 10.0, valid=at(14), source=Source.AIRKOREA)]
    with_uv = base + [m(Q.UV, 9.0, valid=at(14), source=Source.LIFE_INDEX, grade=None)]
    assert judge(observe(base, now=at(14)), at(14)).grade is Grade.GOOD
    v = judge(observe(with_uv, now=at(14)), at(14))
    assert v.grade is Grade.GOOD and v.uv is not None and "9" in v.uv.note


# ------------------------------------------------------- ⑤-a 저하 정책

def test_an_unknown_axis_never_lets_the_answer_reach_good() -> None:
    """⑤-a 근거 3 — **"모르는 것을 좋다고 말하지 않는다."**

    이게 없으면 미세먼지가 매우나쁨인 날에 에어코리아가 죽었다는 이유로 "좋음"이라고 말한다.
    """
    v = judge(observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                       m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14))], now=at(14)), at(14))
    assert v.axes[Axis.AIR].grade is None
    assert v.unknown_axes == [Axis.AIR]
    assert v.grade is Grade.CAUTION and v.capped


def test_the_cap_only_blocks_good_so_danger_is_not_hidden() -> None:
    """상한은 `GOOD` 만 막는다 — 열이 `UNSAFE` 면 대기질을 몰라도 `UNSAFE` 다."""
    obs = observe([m(Q.TEMP, 33.1, valid=at(14)), m(Q.HUMIDITY, 57.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14))], now=at(14))
    v = judge(obs, at(14))
    assert v.unknown_axes == [Axis.AIR]
    assert v.grade is Grade.UNSAFE and not v.capped


def test_when_the_grid_provider_dies_there_is_no_verdict() -> None:
    """⑤-a — `HEAT` 와 `RAIN` 은 **같은 provider**에서 온다. 둘이 함께 죽으면 답할 것이 없다."""
    obs = observe([m(Q.PM10, 10.0, valid=at(14), source=Source.AIRKOREA)], now=at(14))
    v = judge(obs, at(14))
    assert v.grade is None
    assert sorted(v.unknown_axes) == sorted([Axis.HEAT, Axis.RAIN])


# ---------------------------------------------- ③-b ② 미래 대기질 (persistence 기각)

def test_future_air_uses_the_regional_daily_forecast() -> None:
    """persistence 는 기각됐다 — 6시간 뒤 미세먼지가 지금과 같다는 근거가 없다.

    기관이 **실제로 예보한** 권역 일 등급을 쓴다. 해상도가 떨어지지만 지어낸 값이 아니다.
    """
    forecast = State(StateKind.AIR_FORECAST, "나쁨", "서울",
                     valid_from=at(0), valid_to=at(0) + timedelta(days=1),
                     issued_at=at(5), source=Source.AIRKOREA_FORECAST)
    obs = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
                   m(Q.PM10, 10.0, valid=at(10), source=Source.AIRKOREA)],   # 10시에만 값이 있다
                  [forecast], now=at(10))
    now_verdict = judge(obs, at(10))
    later = judge(obs, at(20))
    assert now_verdict.axes[Axis.AIR].grade is Grade.GOOD          # 값으로 판정
    assert "측정소" not in later.axes[Axis.AIR].note
    assert later.axes[Axis.AIR].grade is Grade.CAUTION             # 권역 일 등급 '나쁨'
    assert "권역 예보" in later.axes[Axis.AIR].note


def test_measured_values_beat_the_regional_forecast_when_both_exist() -> None:
    forecast = State(StateKind.AIR_FORECAST, "매우나쁨", "서울",
                     valid_from=at(0), valid_to=at(0) + timedelta(days=1),
                     issued_at=at(5), source=Source.AIRKOREA_FORECAST)
    obs = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
                   m(Q.PM10, 10.0, valid=at(14), source=Source.AIRKOREA)], [forecast], now=at(14))
    assert judge(obs, at(14)).axes[Axis.AIR].grade is Grade.GOOD


# ---------------------------------------------- 특보를 미래로 끌고 가는 범위

def test_an_open_ended_warning_does_not_own_the_whole_timeline() -> None:
    """③-a — 특보는 "현재 발효만" 이고 ~T+6h 까지만 유지한다.

    `valid_to=None` 을 그대로 두면 발효 중인 폭염경보가 **T+24h 전체를 `UNSAFE`** 로 만든다.
    새벽 3시 체감 28℃ 도 `UNSAFE` 가 되는데, 그건 ③-b ②가 미래 대기질에서 기각한 것과
    같은 종류의 근거 없는 외삽이다.
    """
    heat = State(StateKind.HEAT, "경보", "서울동남권", valid_from=at(11), valid_to=None,
                 issued_at=at(10), source=Source.WARNING)
    cool = [m(Q.TEMP, 24.0, valid=at(h)) for h in range(11, 24)]
    cool += [m(Q.HUMIDITY, 50.0, valid=at(h)) for h in range(11, 24)]
    cool += [m(Q.PRECIP_KIND, Code("none", "0"), valid=at(h)) for h in range(11, 24)]
    obs = observe(cool, [heat], now=at(12))

    assert judge(obs, at(14)).axes[Axis.HEAT].grade is Grade.UNSAFE     # T+2h — 특보가 산다
    assert judge(obs, at(22)).axes[Axis.HEAT].grade is Grade.GOOD       # T+10h — 값으로 돌아간다
    assert thresholds()["warnings"]["carry_forward_hours"] == 6


def test_a_state_with_an_explicit_end_is_trusted_for_its_whole_span() -> None:
    """권역 일 등급은 기관이 구간을 정해 줬다 — 그건 그대로 믿는다."""
    forecast = State(StateKind.AIR_FORECAST, "나쁨", "서울",
                     valid_from=at(0), valid_to=at(0) + timedelta(days=1),
                     issued_at=at(5), source=Source.AIRKOREA_FORECAST)
    obs = observe([m(Q.TEMP, 20.0, valid=at(0)), m(Q.HUMIDITY, 50.0, valid=at(0)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(0))], [forecast], now=at(1))
    assert judge(obs, at(23)).axes[Axis.AIR].grade is Grade.CAUTION     # T+22h 인데 유효하다


# ------------------------------------------------------------- ③-d 강수

def test_rain_alone_never_reaches_unsafe() -> None:
    """"몇 mm 부터 위험한가"를 우리가 정하는 대신 **호우특보가 기관의 위험 판정**이다."""
    pouring = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                       m(Q.PRECIP_KIND, Code("rain", "1"), valid=at(14)),
                       m(Q.PRECIP_MM, Interval("50.0mm 이상", 50.0, float("inf")), valid=at(14))],
                      now=at(14))
    assert judge(pouring, at(14)).axes[Axis.RAIN].grade is Grade.CAUTION

    warned = State(StateKind.HEAVY_RAIN, "경보", "서울", valid_from=at(13), valid_to=None,
                   issued_at=at(13), source=Source.WARNING)
    with_warning = observe(pouring.measurements, [warned], now=at(14))
    assert judge(with_warning, at(14)).axes[Axis.RAIN].grade is Grade.UNSAFE


def test_the_interval_predicate_treats_nowcast_and_forecast_alike() -> None:
    """②-d-1 이 강수를 `Interval` 로 둔 덕에 `hi > 0` 하나로 끝난다."""
    for label, lo, hi, expected in [("강수없음", 0.0, 0.0, Grade.GOOD),
                                    ("0", 0.0, 0.0, Grade.GOOD),
                                    ("1.0mm 미만", 0.0, 1.0, Grade.CAUTION)]:
        obs = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                       m(Q.PRECIP_MM, Interval(label, lo, hi), valid=at(14))], now=at(14))
        assert judge(obs, at(14)).axes[Axis.RAIN].grade is expected, label


def test_lightning_is_unsafe() -> None:
    obs = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
                   m(Q.LIGHTNING, 1.0, valid=at(14))], now=at(14))
    assert judge(obs, at(14)).axes[Axis.RAIN].grade is Grade.UNSAFE


@pytest.mark.parametrize(("lgt", "expected"), [
    (0.0, Grade.GOOD),        # 문서에 "0 = 낙뢰" 라는 말이 없다. 실측도 `'0'` 뿐이었다
    (0.2, Grade.UNSAFE),      # 문서 범위의 **하한** — 여기가 안 걸리면 약한 낙뢰를 놓친다
    (100.0, Grade.UNSAFE),    # 상한
])
def test_the_lightning_threshold_matches_the_documented_domain(lgt: float, expected: Grade) -> None:
    """RT-004 — `LGT` 는 코드가 아니라 **에너지밀도 0.2~100kA/㎢** 다 (활용가이드 260623).

    그 도메인이라야 `judge_rain` 의 `> 0` 이 뜻을 갖는다: 실제 낙뢰는 0.2 부터 시작하므로
    0 은 "없음"이고 0 초과는 전부 낙뢰다. **코드값(0·1·2·3 같은 등급)이었다면 `> 0` 은
    "낮음"까지 `UNSAFE` 로 만드는 셈이라 뜻이 달라졌다** — 그래서 도메인 확정이 먼저였다.
    """
    obs = observe([m(Q.TEMP, 20.0, valid=at(14)), m(Q.HUMIDITY, 50.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14)),
                   m(Q.LIGHTNING, lgt, valid=at(14))], now=at(14))
    assert judge(obs, at(14)).axes[Axis.RAIN].grade is expected


# ------------------------------------------------- ③-d 임계표의 규율 (메타 테스트)

def test_every_uncited_number_is_marked_as_ours() -> None:
    """③-d 원칙 1 — 출처가 없으면 `우리 선택` 이라고 **명시**한다.

    파트①의 KPI 가 "출처 링크 + 조항 번호 인용"인데 파트②에서 근거 없는 숫자를 조용히 박으면
    같은 기준을 스스로 어긴다. 이 테스트가 그 규율을 문서가 아니라 검사로 만든다.
    """
    lines = THRESHOLDS_FILE.read_text(encoding="utf-8").splitlines()
    ours = {"summer_from_c", "caution_from_c", "cold_caution_from_c", "cold_caution_below_c"}
    cited = {"winter_to_c", "winter_wind_min_ms", "unsafe_from_c", "cold_unsafe_below_c",
             "caution_from", "unsafe_from", "carry_forward_hours"}
    for i, line in enumerate(lines):
        key = re.match(r"^\s{2,}(\w+):\s*-?[\d.]+\s*$", line)
        if not key:
            continue
        name = key.group(1)
        assert name in ours or name in cited, f"{name}: 출처 분류가 없다"
        context = " ".join(lines[max(0, i - 14):i + 2])
        if name in ours:
            assert "우리 선택" in context, f"{name}: 비인용인데 표시가 없다"
        else:
            assert re.search(r"기상청|환경부|JAG|공식|③-a", context), f"{name}: 출처 인용이 없다"


def test_the_environment_ministry_bands_are_not_adjusted_for_dogs() -> None:
    """개가 지면 가까이 호흡하는 것은 맞지만 **정량화할 근거가 없다** (③-d)."""
    air = thresholds()["air"]
    assert (air["pm10"]["caution_from"], air["pm10"]["unsafe_from"]) == (81, 151)
    assert (air["pm25"]["caution_from"], air["pm25"]["unsafe_from"]) == (36, 76)


# ------------------------------------------- 시간축 — 8/25 실제 응답 위에서

def _from_fixtures() -> tuple[Observations, datetime]:
    def body(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["response"]["body"]

    measurements = (kma_vilage_fcst.parse_ncst(body("kma-vilage-fcst.ncst.json"))
                    + kma_vilage_fcst.parse_ultra(body("kma-vilage-fcst.ultra.json"))
                    + kma_vilage_fcst.parse_village(body("kma-vilage-fcst.village.json"))
                    + airkorea_realtime.parse_dnsty(body("airkorea-realtime.dnsty.json"), "강남대로"))
    states = (kma_warning.parse_pwn(body("kma-warning.pwn.json"), ["서울동남권", "서울"])
              + airkorea_realtime.parse_frcst(body("airkorea-realtime.frcst.json"), "서울"))
    now = datetime(2026, 8, 25, 10, 0, tzinfo=KST)
    return Observations(ResolvedLocation(HERE, GRID, "역삼동", station="강남대로"),
                        now, measurements, states, []), now


def test_the_timeline_answers_every_hour_to_the_horizon() -> None:
    obs, now = _from_fixtures()
    line = timeline(obs, now, hours=24)
    assert len(line) == 25
    assert all(v.grade is not None for v in line), "판정이 뚫린 시각이 있다"
    assert line[-1].at == now + timedelta(hours=24)


def test_the_afternoon_is_unsafe_and_the_night_recovers() -> None:
    """③-a 가 요구한 모양 — "지금은 안 되지만 저녁엔 된다"가 한 응답에 들어간다.

    8/24 드라이런과 같은 패턴이다(오후 `UNSAFE`/heat → 저녁 `CAUTION` → 밤 `GOOD`).
    숫자는 8/25 응답이라 시각이 한두 칸 다르다.
    """
    obs, now = _from_fixtures()
    line = {v.at.hour + (24 if v.at.day == 26 else 0): v for v in timeline(obs, now, hours=24)}

    for hour in (14, 15, 16):
        assert line[hour].grade is Grade.UNSAFE, hour
        assert line[hour].dominant == [Axis.HEAT]
    assert line[20].grade is Grade.GOOD
    for hour in (24 + 3, 24 + 5, 24 + 7):                 # 익일 새벽
        assert line[hour].grade is Grade.GOOD


def test_the_coolest_hour_is_before_dawn() -> None:
    obs, now = _from_fixtures()
    heat_notes = {v.at: v.axes[Axis.HEAT].note for v in timeline(obs, now, hours=24)}
    values = {t: float(re.search(r"([\d.]+)℃", note).group(1)) for t, note in heat_notes.items()}
    coolest = min(values, key=lambda t: values[t])
    assert coolest.hour in range(2, 8), coolest
    assert values[coolest] == pytest.approx(28.0, abs=0.1)


def test_windows_report_the_recommended_spans() -> None:
    """⑥ 응답의 `windows`. 끝 시각은 **마지막으로 좋은 시각**이다 — 그다음까지 좋다고 하면 지어낸다."""
    obs, now = _from_fixtures()
    line = timeline(obs, now, hours=24)
    spans = windows(line)
    assert spans, "권장 구간이 하나도 없다"
    evening = max(spans, key=lambda s: s[1] - s[0])
    assert evening[0].hour == 20 and evening[0].day == 25
    assert all(judge(obs, s).grade is Grade.GOOD for s, _ in spans)
    assert windows(line, Grade.CAUTION)[-1][0] <= evening[0]


def test_windows_are_empty_when_nothing_qualifies() -> None:
    obs = observe([m(Q.TEMP, 40.0, valid=at(14)), m(Q.HUMIDITY, 80.0, valid=at(14)),
                   m(Q.PRECIP_KIND, Code("none", "0"), valid=at(14))], now=at(14))
    assert windows(timeline(obs, at(14), hours=3)) == []


def test_a_failed_provider_is_visible_next_to_the_verdict() -> None:
    """②-e — 판정과 실패가 한 묶음이라 짝이 어긋날 수 없다."""
    obs, now = _from_fixtures()
    obs.providers.append(ProviderResult(Source.AIRKOREA, ok=False, reason="resultCode 05", attempts=3))
    assert judge(obs, now).grade is not None
    assert [p.reason for p in obs.failed()] == ["resultCode 05"]
