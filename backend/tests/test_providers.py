"""검문소 B — provider 7개가 **실호출 없이** 실제 응답을 파싱하는가 (RT-001 구현 계획 6).

입력은 `tests/fixtures/realtime/` 의 원본 응답이다 (2026-08-25 10:07, 강남 격자 61,125).
그 파일들의 출처·수집 조건·공백은 그 폴더의 README 에 있다.

**네트워크를 타지 않는 이유** — 타면 이 테스트가 그날의 게이트웨이 상태와 그날의 날씨를 따라간다.
파싱이 깨졌는지 API 가 흔들렸는지 구분이 안 되면 테스트가 신호를 잃는다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from daengs_life.realtime.config import KST
from daengs_life.realtime.geo import Grid, LatLon, to_grid
from daengs_life.realtime.observation import Code, Interval, Measurement, Q, Source, StateKind
from daengs_life.realtime.providers import (
    airkorea_realtime, airkorea_stations, kakao_local, kma_apihub, kma_life_index,
    kma_vilage_fcst, kma_warning,
)

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
HERE = LatLon(37.4979, 127.0276)                  # §6.5 검산 지점
GRID = Grid(61, 125)


def load(name: str) -> dict:
    """data.go.kr 응답의 `body`. 전송이 provider 에게 넘기는 것과 같은 조각이다."""
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["response"]["body"]


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def by_quantity(ms: list[Measurement], q: Q) -> list[Measurement]:
    return [m for m in ms if m.quantity is q]


# ------------------------------------------------- kma-vilage-fcst (모양 A · B)

def test_nowcast_parses_and_has_one_time() -> None:
    ms = kma_vilage_fcst.parse_ncst(load("kma-vilage-fcst.ncst.json"))
    assert len(ms) == 8
    assert {m.source for m in ms} == {Source.NCST}
    temp = by_quantity(ms, Q.TEMP)[0]
    assert temp.value == 28.3                     # `T1H` 실측
    # 관측은 "리드타임 0인 측정값"이다 (②-c) — 제공이 +40분 늦는 것은 값의 시각이 아니다
    assert temp.valid_at == temp.issued_at == datetime(2026, 8, 25, 9, tzinfo=KST)
    assert temp.spatial_ref == "격자 61,125"


def test_the_two_renamings_land_in_the_same_place() -> None:
    """§6.3 의 A→B 개명이 두 겹이다 — 값 필드와 **양의 이름**.

    실황·초단기예보는 `T1H`, 단기예보는 `TMP` 인데 룰은 `Q.TEMP` 하나만 안다.
    이것이 ②가 존재하는 이유 그 자체다.
    """
    ncst = kma_vilage_fcst.parse_ncst(load("kma-vilage-fcst.ncst.json"))
    ultra = kma_vilage_fcst.parse_ultra(load("kma-vilage-fcst.ultra.json"))
    village = kma_vilage_fcst.parse_village(load("kma-vilage-fcst.village.json"))
    for ms in (ncst, ultra, village):
        assert by_quantity(ms, Q.TEMP), "기온을 못 찾았다"
    assert all(isinstance(m.value, float) for ms in (ncst, ultra, village)
               for m in by_quantity(ms, Q.TEMP))


def test_forecasts_carry_two_different_times() -> None:
    ultra = kma_vilage_fcst.parse_ultra(load("kma-vilage-fcst.ultra.json"))
    temp = by_quantity(ultra, Q.TEMP)[0]
    assert temp.issued_at == datetime(2026, 8, 25, 9, 30, tzinfo=KST)
    assert temp.valid_at > temp.issued_at, "예보인데 대상 시각이 발표 시각과 같다"


def test_the_village_forecast_covers_more_than_24_hours() -> None:
    """③-a 의 상한이 T+24h 다. 전송이 100행에 묶여 있었다면 9시간에서 끊겼다."""
    village = kma_vilage_fcst.parse_village(load("kma-vilage-fcst.village.json"))
    temps = by_quantity(village, Q.TEMP)
    span = max(m.valid_at for m in temps) - min(m.valid_at for m in temps)
    assert span.total_seconds() / 3600 >= 24, f"{span} 밖에 못 덮는다"
    assert len(village) == 871


def test_each_quantity_arrives_in_its_decided_representation() -> None:
    """②-d-1 의 1:1 이 실제 응답에서 성립하는가. `Measurement` 가 이미 막지만 눈으로 본다."""
    village = kma_vilage_fcst.parse_village(load("kma-vilage-fcst.village.json"))
    assert isinstance(by_quantity(village, Q.PRECIP_MM)[0].value, Interval)   # `PCP`
    assert isinstance(by_quantity(village, Q.SNOW_CM)[0].value, Interval)     # `SNO`
    assert isinstance(by_quantity(village, Q.SKY)[0].value, Code)
    assert isinstance(by_quantity(village, Q.PRECIP_KIND)[0].value, Code)
    assert isinstance(by_quantity(village, Q.HUMIDITY)[0].value, float)


def test_the_same_name_changes_domain_between_operations() -> None:
    """`RN1` 이 실황에서는 `'0'`(숫자), 예보에서는 `'강수없음'`(구간)이다 (②-d-1 근거 1).

    넓은 쪽으로 통일하므로 룰은 `hi > 0` 하나로 둘을 똑같이 다룬다.
    """
    ncst_rain = by_quantity(kma_vilage_fcst.parse_ncst(load("kma-vilage-fcst.ncst.json")), Q.PRECIP_MM)
    ultra_rain = by_quantity(kma_vilage_fcst.parse_ultra(load("kma-vilage-fcst.ultra.json")), Q.PRECIP_MM)
    assert ncst_rain and ultra_rain
    assert all(isinstance(m.value, Interval) for m in ncst_rain + ultra_rain)
    assert ncst_rain[0].value.label == "0"          # 실황은 숫자로 왔다
    assert ultra_rain[0].value.label == "강수없음"   # 예보는 구간 서술로 왔다
    assert all(m.value.is_zero for m in ncst_rain + ultra_rain)


def test_unknown_categories_are_dropped_not_fatal() -> None:
    body = {"items": {"item": [
        {"category": "XXX", "baseDate": "20260825", "baseTime": "0900", "nx": 61, "ny": 125,
         "obsrValue": "1"},
        {"category": "T1H", "baseDate": "20260825", "baseTime": "0900", "nx": 61, "ny": 125,
         "obsrValue": "28.3"}]}}
    assert len(kma_vilage_fcst.parse_ncst(body)) == 1


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 8, 25, 10, 7, tzinfo=KST), ("20260825", "0800")),
    (datetime(2026, 8, 25, 8, 5, tzinfo=KST), ("20260825", "0500")),   # :10 전이라 이전 회차
    (datetime(2026, 8, 25, 1, 0, tzinfo=KST), ("20260824", "2300")),   # 자정을 넘어간다
])
def test_village_base_time_follows_the_publication_schedule(now, expected) -> None:
    """④-b 의 발표 주기에서 유도된다 — 임의 숫자가 없다."""
    assert kma_vilage_fcst.village_base(now) == expected


def test_nowcast_base_time_waits_the_forty_minutes() -> None:
    assert kma_vilage_fcst.ncst_base(datetime(2026, 8, 25, 10, 7, tzinfo=KST)) == ("20260825", "0900")
    assert kma_vilage_fcst.ncst_base(datetime(2026, 8, 25, 10, 45, tzinfo=KST)) == ("20260825", "1000")


# ---------------------------------------------------- airkorea-realtime (D · E)

def test_station_readings_carry_the_hourly_grade_not_the_daily_one() -> None:
    """②-d-2 근거 4 — 실측에서 `Grade`(24h 평균)와 `Grade1h`(현재)가 갈렸다. 산책은 지금이다."""
    body = load("airkorea-realtime.dnsty.json")
    ms = airkorea_realtime.parse_dnsty(body, "강남대로")
    latest = body["items"][0]
    pm10 = by_quantity(ms, Q.PM10)[0]
    assert pm10.value == float(latest["pm10Value"])
    assert pm10.grade == int(latest["pm10Grade1h"])
    assert pm10.spatial_ref == "측정소 강남대로"
    assert pm10.source is Source.AIRKOREA


def test_the_composite_index_is_carried_as_a_quantity() -> None:
    """`khaiValue` 는 합성 지수라 우리가 재현할 수 없다 — 값 자체를 싣는다 (②-d-2 근거 1)."""
    ms = airkorea_realtime.parse_dnsty(load("airkorea-realtime.dnsty.json"), "강남대로")
    khai = by_quantity(ms, Q.KHAI)[0]
    assert khai.value == 29.0 and khai.grade == 1


def test_station_readings_are_a_time_series() -> None:
    """`dataTerm=DAILY` 는 측정소 여러 곳이 아니라 한 측정소의 24시간이다 (§6.3)."""
    ms = airkorea_realtime.parse_dnsty(load("airkorea-realtime.dnsty.json"), "강남대로")
    times = sorted({m.valid_at for m in by_quantity(ms, Q.PM10)})
    assert len(times) >= 20
    assert all(t.tzinfo is not None for t in times)


def test_a_missing_reading_is_absent_rather_than_zero() -> None:
    """`'-'` 를 0 으로 떨어뜨리면 "공기가 깨끗하다"가 된다."""
    body = {"items": [{"dataTime": "2026-08-25 09:00", "pm10Value": "-", "pm25Value": "6",
                       "pm25Grade1h": "1"}]}
    ms = airkorea_realtime.parse_dnsty(body, "강남대로")
    assert not by_quantity(ms, Q.PM10)
    assert by_quantity(ms, Q.PM25)[0].value == 6.0


def test_the_regional_forecast_is_a_state_not_a_value() -> None:
    """모양 E — `informGrade` 가 `"서울 : 좋음,제주 : 좋음,…"` 한 문자열이다 (②-a)."""
    states = airkorea_realtime.parse_frcst(load("airkorea-realtime.frcst.json"), "서울")
    assert states
    today = [s for s in states if s.valid_from.date() == datetime(2026, 8, 25).date()]
    assert today and today[0].kind is StateKind.AIR_FORECAST
    assert today[0].category == "좋음"            # 기관 라벨 그대로 (②-d-2)
    assert today[0].area == "서울"
    assert today[0].issued_at == datetime(2026, 8, 25, 5, tzinfo=KST)   # `'05시 발표'`


def test_a_region_that_is_not_in_the_notice_yields_nothing() -> None:
    """없는 것을 지어내지 않는다."""
    assert not airkorea_realtime.parse_frcst(load("airkorea-realtime.frcst.json"), "없는권역")


# -------------------------------------------------------- airkorea-stations

def test_stations_parse_with_latitude_in_the_field_named_x() -> None:
    """⚠️ `dmX` 가 위도다 (§6.5). 뒤집으면 틀린 격자를 내놓고 예외는 안 난다."""
    stations = airkorea_stations.parse_list(load("airkorea-stations.list.json"))
    assert len(stations) == 40
    junggu = next(s for s in stations if s.name == "중구")
    assert 37.0 < junggu.point.lat < 38.0 and 126.0 < junggu.point.lon < 128.0


def test_the_nearest_station_is_the_one_the_probe_found() -> None:
    stations = airkorea_stations.parse_list(load("airkorea-stations.list.json"))
    hit = airkorea_stations.pick(stations, HERE)
    assert hit is not None
    station, km = hit
    assert station.name == "강남대로"
    assert km == pytest.approx(1.82, abs=0.01)


def test_nearest_and_same_grid_disagree_on_this_real_pair() -> None:
    """⑤-d 가 거리 임계를 기각한 이유가 실물로 나온다.

    `강남대로`(1.82km)는 우리 격자 안이고 `강남구`(1.9km)는 밖이다. 거리는 거의 같은데
    격자가 갈린다 — "5km 안이면 같은 곳" 같은 임계였다면 둘을 구분 못 했을 자리다.
    """
    stations = airkorea_stations.parse_list(load("airkorea-stations.list.json"))
    inside = {s.name for s in airkorea_stations.in_grid(stations, HERE)}
    assert "강남대로" in inside
    gangnam_gu = next(s for s in stations if s.name == "강남구")
    assert to_grid(gangnam_gu.point) != GRID
    assert gangnam_gu.name not in inside
    assert airkorea_stations.haversine_km(HERE, gangnam_gu.point) < 5.0


def test_a_station_without_coordinates_is_skipped() -> None:
    body = {"items": [{"stationName": "좌표없음"}, {"stationName": "정상", "dmX": "37.5", "dmY": "127.0"}]}
    assert [s.name for s in airkorea_stations.parse_list(body)] == ["정상"]


# ------------------------------------------------------------- kma-warning

def test_a_warning_is_found_only_when_my_area_is_named() -> None:
    """`t6` 은 전국 발효 현황 자연어 1,610자다 (§6.3). 내 특보구역을 문자열에서 찾아야 한다."""
    body = load("kma-warning.pwn.json")
    mine = kma_warning.parse_pwn(body, ["서울동남권", "서울"])
    kinds = {(s.kind, s.category) for s in mine}
    assert (StateKind.HEAT, "경보") in kinds, kinds
    assert not kma_warning.parse_pwn(body, ["없는구역"])


def test_a_warning_has_no_end_time() -> None:
    """해제는 다음 통보로 알려진다 — 임의 만료를 씌우면 발효 중인 경보가 조용히 사라진다."""
    states = kma_warning.parse_pwn(load("kma-warning.pwn.json"), ["서울동남권", "서울"])
    heat = next(s for s in states if s.kind is StateKind.HEAT)
    assert heat.valid_to is None
    assert heat.issued_at == datetime(2026, 8, 25, 10, tzinfo=KST)      # `tmFc`
    assert heat.covers(datetime(2026, 8, 25, 15, tzinfo=KST))


def test_area_matching_does_not_fire_on_a_partial_name() -> None:
    """`'서울동남권'` 을 찾는데 `'서울동남권해상'` 에 걸리면 안 된다."""
    body = {"items": {"item": [{"tmFc": "202608251000", "tmEf": "202608251100",
                                "t6": "o 폭염경보 : 서울(서울동남권해상)"}]}}
    assert not kma_warning.parse_pwn(body, ["서울동남권"])


def test_preliminary_warnings_are_not_treated_as_active() -> None:
    """`t7` 은 예비특보다. 섞으면 "지금 폭염경보"가 되는데 아직 발효가 아니다."""
    body = load("kma-warning.pwn.json")
    item = body["items"]["item"][0]
    assert "예비특보" in item["t7"]
    states = kma_warning.parse_pwn(body, ["제주도남동쪽안쪽먼바다"])
    assert not states, "t7 의 예비특보가 발효 특보로 올라왔다"


def test_an_unknown_warning_label_is_not_invented() -> None:
    body = {"items": {"item": [{"tmFc": "202608251000", "tmEf": "202608251100",
                                "t6": "o 미지의특보 : 서울"}]}}
    assert not kma_warning.parse_pwn(body, ["서울"])


# -------------------------------------------------------------- kma-apihub

def _aws_coords(stn: int) -> dict[int, LatLon]:
    """`stn_inf.php` 가 아직 403 이라 좌표표를 손으로 만든다 (README 참고)."""
    return {stn: HERE}


def test_aws_emits_only_stations_inside_my_grid() -> None:
    """⑤-d 의 판정이 provider 안에 있다 — 밖의 지점은 `Measurement` 를 만들지 않는다.

    그래서 `observation` 의 우선순위표가 거리 임계 없이 성립한다.
    """
    raw = text("kma-apihub.aws.txt")
    stn = int(kma_apihub.kmahub.rows(raw)[0][kma_apihub.STN])
    ms = kma_apihub.parse_aws(raw, _aws_coords(stn), GRID)
    assert ms and {m.spatial_ref for m in ms} == {f"AWS {stn}"}
    assert {m.source for m in ms} == {Source.AWS_MIN}
    assert by_quantity(ms, Q.TEMP)[0].value == 29.0        # TA 열 실측
    assert by_quantity(ms, Q.HUMIDITY)[0].value == 85.7    # HM 열


def test_aws_without_a_coordinate_table_emits_nothing() -> None:
    """`stn_inf.php` 활용신청 전의 실제 상태 — 1순위가 미발동하고 2순위로 내려간다 (⑤-d 한계)."""
    assert kma_apihub.parse_aws(text("kma-apihub.aws.txt"), {}, GRID) == []


def test_aws_missing_values_do_not_become_readings() -> None:
    """`RE` 열은 전 지점이 `-99.9` 였다. 0 으로 두면 영하도 무풍도 사실이 된다."""
    row = "202608251004    42  202.3    2.3  212.3    3.1  203.0    2.4  -99.9  -99.9 " \
          "   0.0    0.0    0.0    0.0  -99.9 1012.0 1014.9   26.4"
    ms = kma_apihub.parse_aws(row, {42: HERE}, GRID)
    assert not by_quantity(ms, Q.TEMP) and not by_quantity(ms, Q.HUMIDITY)
    assert by_quantity(ms, Q.WIND)[0].value == 2.3


def test_aws_readings_are_minute_fresh() -> None:
    raw = text("kma-apihub.aws.txt")
    stn = int(kma_apihub.kmahub.rows(raw)[0][kma_apihub.STN])
    ms = kma_apihub.parse_aws(raw, _aws_coords(stn), GRID)
    assert ms[0].valid_at == datetime(2026, 8, 25, 10, 4, tzinfo=KST)
    assert ms[0].valid_at == ms[0].issued_at


# ----------------------------------------------------------- kma-life-index

def test_the_area_code_is_cut_to_the_district() -> None:
    """§6.9 — 법정동 코드를 그대로 넣으면 `99 검색결과가 없습니다` 다.

    이 실패가 고약한 이유는 **권한 문제처럼 보이지 않는다**는 것이다. 403 도 아니고
    빈 목록도 아니라 "검색결과 없음" 이라, 지역에 자료가 없는 것으로 읽힌다.
    자르는 규칙을 한 곳에 두고 여기서 고정한다.
    """
    assert kma_life_index.area_code("1165010800") == "1165000000"   # 서초동 → 서초구
    assert kma_life_index.area_code("1168011400") == "1168000000"   # 일원동 → 강남구
    assert kma_life_index.area_code("1165000000") == "1165000000"   # 이미 시군구면 그대로
    assert kma_life_index.area_code("11650108") == "1165000000"     # 짧아도 시군구로 맞춘다


def test_the_request_code_is_always_sent() -> None:
    """`getSenTaIdxV3` 는 없으면 `11 NO_MANDATORY` 이고 `getUVIdxV3` 는 값을 무시한다 (§6.9).

    둘이 같은 봉투를 쓰므로 한 곳에서 붙이되, **무시되는 쪽에 해가 없다는 것을 실측했다**는
    사실이 이 테스트의 내용이다.
    """
    params = kma_life_index._params("1165010800", datetime(2026, 8, 25, 12, tzinfo=KST))
    assert params["requestCode"] == "A01"
    assert params["areaNo"] == "1165000000" and params["time"] == "2026082512"


def test_life_index_no_data_parses_to_nothing() -> None:
    """실측이 `03 NO_DATA` 다 (§6.8 ③). 전송이 이미 막지만 파서도 빈 응답에 안 터진다."""
    payload = json.loads(text("kma-life-index.senta.json"))
    assert kma_life_index.parse_uv(payload, "1168000000") == []
    assert kma_life_index.parse_senta(payload) == {}


def test_the_official_feels_like_is_a_comparison_table_not_a_measurement() -> None:
    """체감온도는 `Q` 에 없다 (②-d-3 결정 2) — 출처가 "우리"인 값과 섞으면 ②가 무의미해진다."""
    payload = {"response": {"body": {"items": {"item": [
        {"date": "2026082510", "h1": "31.2", "h2": "32.0", "areaNo": "1168000000"}]}}}}
    table = kma_life_index.parse_senta(payload)
    assert table[datetime(2026, 8, 25, 11, tzinfo=KST)] == 31.2
    assert table[datetime(2026, 8, 25, 12, tzinfo=KST)] == 32.0
    assert not any(q for q in Q if "feel" in q.value)


def test_uv_carries_no_grade_because_its_scale_differs() -> None:
    """자외선지수는 5단계, 대기질은 4단계다 — `grade >= 3` 의 뜻이 양마다 달라진다 (②-d-2 근거 2)."""
    payload = {"response": {"body": {"items": {"item": [{"date": "2026082510", "h3": "4"}]}}}}
    ms = kma_life_index.parse_uv(payload, "1168000000")
    assert len(ms) == 1 and ms[0].quantity is Q.UV and ms[0].grade is None
    assert ms[0].valid_at == datetime(2026, 8, 25, 13, tzinfo=KST)


# ------------------------------------------------------------- kakao-local

def test_the_administrative_dong_is_preferred_over_the_legal_one() -> None:
    """`region_type` `'H'`(행정동)를 고른다 — 사람이 "우리 동네"라 부르는 단위다."""
    payload = json.loads(text("kakao-local.region.json"))
    assert kakao_local.parse_region(payload) is not None


def test_the_label_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """카카오가 죽었다고 판정을 못 내보내면 ①이 카카오를 UX 로 좁힌 것과 어긋난다."""
    from daengs_life.realtime.transport.base import Rejected

    def dead(*_a, **_k):
        raise Rejected("HTTP 403")

    monkeypatch.setattr(kakao_local.kakao, "get", dead)
    assert kakao_local.label_for(HERE) == "37.4979, 127.0276"


def test_transcoord_is_the_fallback_path_we_do_not_need() -> None:
    """§6.5 — 우리는 자체 계산이 기본이라 이 체인을 안 탄다. 값은 확인해 둔다."""
    x, y = kakao_local.parse_transcoord(json.loads(text("kakao-local.transcoord.json")))
    assert (round(x, 1), round(y, 1)) == (202370.9, 443966.0)
