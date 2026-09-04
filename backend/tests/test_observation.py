"""공통 관측 모델 — 계약이 실제로 계약인가 (RT-001 구현 계획 5 · ② 전체).

여기서 지키는 것은 두 가지다. **어휘가 닫혀 있는가**(23개 · 표현 1:1 · 출처 우선순위가 총함수),
그리고 **조회 헬퍼가 ⑤-d → ②-c 순서로 고르는가.** 후자가 이 파일의 본체다 — 순서가 뒤바뀌면
15시 판정이 6시간 이내에 더 정확한 초단기예보 대신 단기예보를 쓰게 되는데, 값이 비슷해서
눈으로는 안 잡힌다.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from daengs_life.realtime.config import KST
from daengs_life.realtime.geo import Grid, LatLon
from daengs_life.realtime.observation import (
    GRADED, REPRESENTATION, NON_VALUE_SOURCES, SOURCE_PRIORITY, UNVERIFIED, Code, Interval, Measurement,
    Observations, ProviderResult, Q, ResolvedLocation, Source, State, StateKind,
    parse_interval, parse_precip_kind, parse_sky,
)

D = datetime(2026, 8, 24, tzinfo=KST)


def t(hour: int, minute: int = 0) -> datetime:
    """전부 KST aware 다 — `Measurement` 가 naive 를 거부한다."""
    return D.replace(hour=hour, minute=minute)


def temp(value: float, *, valid: datetime, issued: datetime, source: Source) -> Measurement:
    return Measurement(Q.TEMP, value, valid_at=valid, issued_at=issued,
                       source=source, spatial_ref="격자 61,125")


def observe(*measurements: Measurement, now: datetime = t(14, 50), **kwargs) -> Observations:
    location = ResolvedLocation(point=LatLon(37.4979, 127.0276), grid=Grid(61, 125), label="역삼동")
    return Observations(location=location, fetched_at=now,
                        measurements=list(measurements), **kwargs)


# ------------------------------------------------------------------ 어휘 (②-d-3)

def test_the_vocabulary_is_the_23_that_were_decided() -> None:
    assert len(list(Q)) == 23


def test_every_quantity_has_exactly_one_representation() -> None:
    """②-d-1 의 "quantity ↔ 표현 1:1" 은 문서가 아니라 검사여야 뜻이 있다."""
    missing = [q.name for q in Q if q not in REPRESENTATION]
    assert not missing, missing
    assert {Q.PRECIP_MM, Q.SNOW_CM} == {q for q in Q if REPRESENTATION[q] is Interval}
    assert {Q.PRECIP_KIND, Q.SKY} == {q for q in Q if REPRESENTATION[q] is Code}


def test_every_source_has_a_priority() -> None:
    """우선순위가 총함수가 아니면 빠진 출처가 조용히 꼴찌가 된다.

    예외는 **값을 만들지 않는 출처**뿐이고 그것도 코드에 명시돼 있다 (RT-002 ②-a) —
    측정소 목록·AWS 지점표·표기는 `ProviderResult` 로만 나타난다.
    """
    assert set(Source) - NON_VALUE_SOURCES == set(SOURCE_PRIORITY)
    assert not (NON_VALUE_SOURCES & set(SOURCE_PRIORITY))
    assert SOURCE_PRIORITY[0] is Source.AWS_MIN           # ⑤-d 1순위
    assert SOURCE_PRIORITY.index(Source.FCST_ULTRA) < SOURCE_PRIORITY.index(Source.FCST_VILLAGE)


def test_derived_quantities_are_not_in_the_vocabulary() -> None:
    """체감온도는 `Q` 에 없다 — 출처가 "우리"라 섞으면 ②의 존재 이유가 흐려진다 (②-d-3 결정 2)."""
    assert not [q for q in Q if "feel" in q.value or "senta" in q.value]


def test_unverified_quantities_are_marked() -> None:
    """표에 있되 확인 전이라는 사실이 코드에 남아야 한다.

    **`LGT` 는 RT-004 에서 빠졌다** — 실측은 여전히 `'0'` 뿐이지만 활용가이드 260623 이
    도메인을 숫자로 못박았다(에너지밀도 0.2~100kA/㎢). `UV` 는 apihub 403 이라 응답 자체를
    못 봐서 남는다 — **둘의 성격이 다르다는 것이 이 테스트가 지키는 것이다.**
    """
    assert UNVERIFIED == {Q.UV}


# --------------------------------------------------- 경계에서의 검사 (②-d-1 · ②-d-2)

def test_a_wrong_representation_fails_at_the_provider_boundary() -> None:
    with pytest.raises(TypeError, match="TEMP"):
        Measurement(Q.TEMP, Interval("강수없음", 0.0, 0.0), valid_at=t(14), issued_at=t(14),
                    source=Source.NCST, spatial_ref="격자 61,125")


def test_precip_must_be_an_interval_even_when_the_api_sends_a_number() -> None:
    """실황의 `RN1 '0'` 은 폭 0 인 구간으로 올라온다 (②-d-3 결정 1)."""
    with pytest.raises(TypeError, match="PRECIP_MM"):
        Measurement(Q.PRECIP_MM, 0.0, valid_at=t(14), issued_at=t(14),
                    source=Source.NCST, spatial_ref="격자 61,125")
    ok = Measurement(Q.PRECIP_MM, Interval("0", 0.0, 0.0), valid_at=t(14), issued_at=t(14),
                     source=Source.NCST, spatial_ref="격자 61,125")
    assert ok.value.is_zero


def test_naive_times_are_rejected() -> None:
    """naive 와 aware 가 섞이면 조회 헬퍼 안쪽에서 TypeError 가 난다 — 경계에서 막는다."""
    with pytest.raises(ValueError, match="시간대"):
        Measurement(Q.TEMP, 28.3, valid_at=datetime(2026, 8, 25, 9), issued_at=t(9),
                    source=Source.NCST, spatial_ref="격자 61,125")


def test_a_grade_on_a_quantity_that_has_none_is_rejected() -> None:
    """기온에 공식 등급은 애초에 없다 — `None` 은 결함이 아니라 정상이다 (②-d-2)."""
    with pytest.raises(ValueError, match="공식 등급이 없다"):
        Measurement(Q.TEMP, 33.1, valid_at=t(14), issued_at=t(14),
                    source=Source.NCST, spatial_ref="격자 61,125", grade=2)
    assert Q.PM10 in GRADED


# ------------------------------------------------------- 출처 우선순위 (⑤-d → ②-c)

def test_source_priority_beats_a_newer_issue_time() -> None:
    """②-c 가 남긴 바로 그 케이스다.

    `valid_at=15:00` 에 초단기예보(13:30 발표)와 단기예보(14:00 발표)가 둘 다 있다.
    최신 발표만 보면 단기예보가 이기는데, **6시간 이내는 초단기예보가 더 정확하다.**
    """
    obs = observe(
        temp(33.0, valid=t(15), issued=t(14, 0), source=Source.FCST_VILLAGE),
        temp(32.4, valid=t(15), issued=t(13, 30), source=Source.FCST_ULTRA),
    )
    picked = obs.at(t(15), Q.TEMP)
    assert picked is not None
    assert picked.source is Source.FCST_ULTRA, "발표 시각이 출처 우선순위를 이겼다"


def test_within_one_source_the_newest_issue_wins() -> None:
    """단기예보는 1일 8회 발표한다 — 같은 시각에 대한 발표가 여럿 쌓인다 (②-c)."""
    obs = observe(
        temp(31.0, valid=t(18), issued=t(5), source=Source.FCST_VILLAGE),
        temp(33.0, valid=t(18), issued=t(14), source=Source.FCST_VILLAGE),
        temp(32.0, valid=t(18), issued=t(11), source=Source.FCST_VILLAGE),
    )
    picked = obs.at(t(18), Q.TEMP)
    assert picked is not None and picked.issued_at == t(14) and picked.value == 33.0


def test_aws_in_the_same_grid_wins_everything() -> None:
    """⑤-d 1순위. 같은 격자가 아닌 지점은 provider 가 애초에 만들지 않으므로 여기 오면 이긴다."""
    obs = observe(
        temp(33.1, valid=t(14), issued=t(14), source=Source.NCST),
        Measurement(Q.TEMP, 33.4, valid_at=t(14), issued_at=t(14, 35),
                    source=Source.AWS_MIN, spatial_ref="AWS 108"),
    )
    picked = obs.at(t(14), Q.TEMP)
    assert picked is not None and picked.source is Source.AWS_MIN


# ------------------------------------------------------------------ 조회 헬퍼

def test_at_is_a_step_function_not_a_nearest_match() -> None:
    """`valid_at <= t` 중 가장 늦은 것. "가까운 것"은 허용 오차라는 근거 없는 숫자를 요구한다."""
    obs = observe(
        temp(33.1, valid=t(14), issued=t(14), source=Source.NCST),
        temp(33.0, valid=t(15), issued=t(14), source=Source.FCST_VILLAGE),
    )
    assert obs.at(t(14, 59), Q.TEMP).value == 33.1     # 15시에 가깝지만 아직 14시 값이 유효하다
    assert obs.at(t(15), Q.TEMP).value == 33.0
    assert obs.at(t(13), Q.TEMP) is None               # 그 이전 값은 없다


def test_latest_does_not_answer_with_tomorrows_forecast() -> None:
    """목록에는 T+24h 예보가 섞여 있다. `valid_at` 최대를 쓰면 "지금 기온"이 내일 값이 된다."""
    obs = observe(
        temp(33.1, valid=t(14), issued=t(14), source=Source.NCST),
        temp(28.0, valid=t(23), issued=t(14), source=Source.FCST_VILLAGE),
        now=t(14, 50),
    )
    assert obs.latest(Q.TEMP).value == 33.1


def test_series_keeps_one_per_valid_at() -> None:
    """③의 타임라인이 걷는 시계열 — 발표가 겹쳐도 시각당 하나다."""
    obs = observe(
        temp(31.0, valid=t(18), issued=t(5), source=Source.FCST_VILLAGE),
        temp(33.0, valid=t(18), issued=t(14), source=Source.FCST_VILLAGE),
        temp(30.0, valid=t(19), issued=t(14), source=Source.FCST_VILLAGE),
    )
    series = obs.series(Q.TEMP)
    assert [m.valid_at for m in series] == [t(18), t(19)]
    assert series[0].value == 33.0
    assert obs.series(Q.PM10) == []


def test_missing_quantity_is_none_not_an_error() -> None:
    assert observe().latest(Q.PM10) is None


# ------------------------------------------------------------------ 상태 (②-a)

def test_a_warning_with_no_end_time_still_covers_now() -> None:
    """특보는 해제 시각이 미정이라 `valid_to=None` 이 정상이다."""
    heat = State(StateKind.HEAT, "경보", "서울동남권", valid_from=t(11), valid_to=None,
                 issued_at=t(10), source=Source.WARNING, detail="폭염경보")
    obs = observe(states=[heat])
    assert obs.states_at(t(15), StateKind.HEAT) == [heat]
    assert obs.states_at(t(9)) == []
    assert obs.states_at(t(15), StateKind.COLD) == []


def test_the_agency_label_is_kept_verbatim() -> None:
    """정규화하면 룰이 그걸로 판정하고 싶어지고, 표시와 산책 임계가 함께 묶인다 (②-d-2 와 같은 이유)."""
    heat = State(StateKind.HEAT, "경보", "서울동남권", valid_from=t(11), valid_to=None,
                 issued_at=t(10), source=Source.WARNING)
    assert heat.category == "경보"


# ------------------------------------------------------------- 부분 실패 (②-e)

def test_a_dead_provider_is_one_failure_not_many_missing_values() -> None:
    """죽은 provider 는 양이 여럿인데 실패는 하나다 — 개수가 안 맞아서 값으로 표현할 수 없다."""
    obs = observe(providers=[
        ProviderResult(Source.AIRKOREA, ok=False, reason="resultCode 05", attempts=3),
        ProviderResult(Source.NCST, ok=True),
    ])
    assert obs.latest(Q.PM10) is None                  # 값은 그냥 없다
    failed = obs.failed()
    assert len(failed) == 1 and failed[0].reason == "resultCode 05"  # 이유는 따로 남는다


def test_stale_is_recorded_on_the_provider_not_the_value() -> None:
    """⑤-c 가 "옛 값을 썼다"를 ⑥ 응답에 실을 자리."""
    result = ProviderResult(Source.AIRKOREA, ok=True, stale=True)
    assert result.ok and result.stale


# ----------------------------------------------------------- 구간 파서 (②-d-1)

@pytest.mark.parametrize(("raw", "lo", "hi"), [
    ("강수없음", 0.0, 0.0),
    ("적설없음", 0.0, 0.0),
    ("1.0mm 미만", 0.0, 1.0),
    ("30.0~50.0mm", 30.0, 50.0),
    ("50.0mm 이상", 50.0, float("inf")),
    ("0", 0.0, 0.0),                    # 실황 `RN1` — 점은 폭 0 인 구간
    ("2.5", 2.5, 2.5),
])
def test_interval_forms(raw: str, lo: float, hi: float) -> None:
    got = parse_interval(raw)
    assert (got.lo, got.hi) == (lo, hi)
    assert got.label == raw


def test_the_interval_predicates_the_rules_will_use() -> None:
    """②-d-1 의 표 그대로 — 구간을 점으로 바꾸지 않아도 판정이 된다."""
    # 괄호가 필요하다 — `x > 0 is False` 는 파이썬에서 `x > 0 and 0 is False` 로 묶인다
    assert not (parse_interval("강수없음").hi > 0)          # 비 안 온다
    assert parse_interval("1.0mm 미만").hi > 0              # 온다
    assert parse_interval("30.0~50.0mm").lo >= 30           # 30mm 이상이다
    assert not (parse_interval("1.0mm 미만").lo >= 30)


def test_an_unknown_interval_string_fails_loudly() -> None:
    """실측 당일은 비가 안 와서 `'강수없음'` 만 봤다 — 구간 문자열은 문서 기준이다.

    조용히 0 이 되면 비 오는 날 "강수 없음"이라고 답하고도 아무 신호가 없다.
    """
    with pytest.raises(ValueError, match="모르는 구간 표기"):
        parse_interval("조금 옴")


@pytest.mark.parametrize("raw", ["-", "", "None", None])
def test_the_documented_empty_markers_mean_no_precipitation(raw) -> None:
    """RT-004 — 활용가이드 260623 이 강수량·신적설 표 밑에 똑같이 달아 둔 각주:

        ※ -, null, 0값은 '강수없음'   /   ※ -, null, 0값은 '적설없음'

    **PTY 5·6·7 과 같은 유형의 사고였다** — 문서에 있는 값인데 `ValueError` 로 터져서 그 응답의
    measurement 가 통째로 빠진다. `0` 은 폭 0 구간으로 이미 받고 있었고, `-` 와 빈 값만 구멍이었다.
    """
    got = parse_interval(raw)
    assert (got.lo, got.hi) == (0.0, 0.0)


@pytest.mark.parametrize("raw", ["강수없음", "1mm 미만", "6.2", "30.0~50.0mm", "50.0mm 이상",
                                 "적설없음", "0.5cm 미만", "5.0cm 이상"])
def test_every_documented_interval_string_parses(raw: str) -> None:
    """활용가이드 260623 의 강수량(RN1·PCP)·신적설(SNO) 범주 표에 적힌 표기 전부.

    **E1 을 닫지는 않는다** — 문서가 정한 표기를 다 받는지까지가 여기고, `1.0~29.9mm` 구간의
    실수값이 실제로 어떤 문자열로 오는지는 비 오는 날 확인할 일로 남는다.
    """
    assert parse_interval(raw).hi >= 0.0


# --------------------------------------------------------------- 코드 파서

@pytest.mark.parametrize(("raw", "name"), [
    ("0", "none"), ("1", "rain"), ("2", "rain_snow"), ("3", "snow"), ("4", "shower"),
    # RT-004 — **문서 근거**로 넣은 셋 (활용가이드 260623 의 `(초단기)` 줄). 실측이 아니다
    ("5", "drizzle"), ("6", "drizzle_snow"), ("7", "snow_flurry"),
])
def test_precip_codes(raw: str, name: str) -> None:
    got = parse_precip_kind(raw)
    assert got == Code(name, raw)


def test_an_unknown_precip_code_still_fails_rather_than_becoming_none() -> None:
    """**RT-004 는 ②-d-3 을 없앤 것이 아니라 아는 값을 늘린 것이다.**

    5·6·7 을 넣었다고 "모르면 `NONE`" 으로 돌아서면, 문서에 없는 코드가 생기는 날 눈 날리는데
    "강수 없음"이 된다. 8 은 문서 기준으로도 없는 코드라 여전히 터져야 한다 — 이 테스트가
    그 원칙의 회귀다.
    """
    with pytest.raises(ValueError, match="모르는 PTY 코드"):
        parse_precip_kind("8")


def test_the_precip_domain_matches_the_documented_ultra_short_range() -> None:
    """문서가 정한 것은 **오퍼레이션별 도메인**이다 (활용가이드 260623):

        (초단기) 없음(0) 비(1) 비/눈(2) 눈(3) 소나기(4) 빗방울(5) 빗방울눈날림(6) 눈날림(7)
        (단기)   없음(0) 비(1) 비/눈(2) 눈(3) 소나기(4)

    단기가 초단기의 **부분집합**이라 표를 하나로 뒀다. 그 사실이 깨지면(예: 단기에만 있는 코드가
    생기면) 표를 갈라야 하므로 여기서 먼저 걸리게 한다.
    """
    assert all(parse_precip_kind(str(c)) for c in range(8))
    with pytest.raises(ValueError):
        parse_precip_kind("8")


def test_sky_codes() -> None:
    assert parse_sky("1").name == "clear"
    assert parse_sky("3").name == "partly_cloudy"
    assert parse_sky("4").name == "cloudy"
    with pytest.raises(ValueError, match="모르는 SKY"):
        parse_sky("2")
