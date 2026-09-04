"""특보구역 매핑표 (RT-003).

여기서 보는 것은 표 자체가 아니라 **표를 읽는 규칙**이다. 규칙이 세 가지를 해야 한다:
좁은 단위 우선(읍면동 → 시군구) · 다른 도시의 같은 이름 배제 · 모르면 빈 값.

표(`warning_areas.csv`)는 기상청 개편 때만 바뀌므로 실제 값으로 단언한다. 개편으로 깨지면
`tools/fetch_warning_areas.py` 를 다시 돌리고 이 파일의 기대값을 같이 고치는 것이 맞다 —
표를 스텁으로 바꾸면 "규칙은 맞는데 표가 낡은" 상태를 아무도 못 잡는다.
"""
from __future__ import annotations

import pytest

from daengs_life.realtime.warning_areas import OFFICE_SIDO, _index, _units, lookup


def areas(sido: str | None, sigungu: str | None, dong: str | None = None) -> tuple[str, ...]:
    return lookup(sido=sido, sigungu=sigungu, dong=dong)


def test_the_table_actually_loaded() -> None:
    """표가 안 읽히면 아래 단언이 전부 '빈 값'으로 통과해 버린다 — 먼저 막는다."""
    assert len(_index()) > 400


# ------------------------------------------------------------ 시군구 → 특보구역

@pytest.mark.parametrize(("gu", "expected"), [
    ("강남구", "서울동남권"),
    ("은평구", "서울서북권"),
    ("관악구", "서울서남권"),
])
def test_a_seoul_district_finds_its_quadrant(gu: str, expected: str) -> None:
    """RT-003 이 푸는 문제 그 자체 — 이게 없으면 `t6` 에서 자기 이름을 못 찾는다."""
    assert areas("서울", gu) == (expected,)


# ------------------------------------------------------- 읍면동이 시군구보다 먼저

def test_a_township_wins_over_its_city() -> None:
    """파주는 시군구 아래로 쪼개져 있다. 시군구로만 찾으면 세 구역을 뭉갠다."""
    assert areas("경기", "파주시", "문산읍") == ("파주동북부",)


def test_the_same_city_can_land_in_a_different_zone() -> None:
    """강릉은 산지와 평지가 다른 특보구역이다. 읍면동이 그 둘을 가르는 유일한 단서다."""
    assert areas("강원", "강릉시", "연곡면") == ("강릉산지",)
    # 평지 쪽 동은 표에 이름이 없다 — `'강릉시 산지 제외 지역'` 이라는 서술문뿐이라
    # 시군구로 떨어지고, 고도를 모르니 두 이름이 다 후보로 남는 것이 정직하다.
    assert areas("강원", "강릉시", "포남동") == ("강릉", "강릉평지")


# --------------------------------------------- 다른 도시의 같은 이름 (진짜 위험)

@pytest.mark.parametrize(("sido", "gu", "expected"), [
    ("서울", "중구", "서울서북권"),
    ("대구", "중구", "대구중부"),
    ("부산", "중구", "부산서부"),
    ("서울", "강서구", "서울서남권"),
    ("부산", "강서구", "부산서부"),
])
def test_a_name_that_exists_in_many_cities_is_not_confused(sido: str, gu: str, expected: str) -> None:
    """`중구` 는 다섯 도시에 있다. 시도로 안 좁히면 서울 사람이 인천 특보를 자기 것으로 읽는다."""
    assert areas(sido, gu) == (expected,)


def test_the_same_county_name_in_two_provinces() -> None:
    """고성군은 강원과 경남에 있다. 관서가 갈리므로 시도만으로 풀린다."""
    assert areas("강원", "고성군", "간성읍") == ("고성산지",)
    assert areas("경남", "고성군", "고성읍") == ("고성",)


def test_a_township_name_shared_inside_one_office() -> None:
    """마산면은 구례와 해남에 다 있다 — 관서(광주)가 같아 시군구 어간까지 봐야 갈린다."""
    assert areas("전남", "구례군", "마산면") == ("구례산간", "구례평지")


# ------------------------------------------------------------ 모르면 비운다

@pytest.mark.parametrize(("sido", "gu", "dong"), [
    ("서울", "없는구", "없는동"),      # 표에 없는 이름
    (None, None, None),                # 카카오가 죽어 아무것도 모를 때
    ("서울", None, None),              # 시도만 알 때 — 시도는 조회 키가 아니다
])
def test_an_unknown_place_is_empty_not_invented(sido, gu, dong) -> None:
    """빈 튜플이면 `_warning_areas` 의 시도 단축명 폴백이 그대로 살아난다 (RT-003 이전과 동일)."""
    assert areas(sido, gu, dong) == ()


def test_a_wrong_sido_does_not_match() -> None:
    """강남구를 부산이라고 하면 답하지 않는다 — 틀린 답보다 모르는 편이 낫다."""
    assert areas("부산", "강남구") == ()


# ------------------------------------------------------- 관할구역 문장 해석 (_units)

def test_a_qualifier_is_not_a_covered_unit() -> None:
    """`'강릉시 연곡면'` 의 강릉시는 연곡면을 한정하는 말이다.

    같이 담으면 강릉시가 `강릉산지` 에도 걸려 시내 한복판에서 산지 특보를 자기 것으로 읽는다.
    """
    assert _units("강릉시 연곡면.성산면.왕산면") == {"연곡면", "성산면", "왕산면"}


def test_an_exclusion_clause_is_dropped_not_parsed() -> None:
    """괄호 안은 제외 규칙이지 관할이 아니다. 해석하려면 없는 지식이 필요하다."""
    assert _units("동구.미추홀구.연수구.남동구.중구(인천영종 제외)") == {
        "동구", "미추홀구", "연수구", "남동구", "중구",
    }


def test_prose_keeps_only_the_administrative_word() -> None:
    """`'산지'`·`'제외'`·`'지역'` 은 행정단위가 아니다."""
    assert _units("강릉시 산지 제외 지역") == {"강릉시"}


def test_every_office_in_the_table_has_a_sido() -> None:
    """관서가 하나라도 빠지면 그 지역 전체가 시도 필터에서 조용히 탈락한다."""
    offices = {office for rows in _index().values() for office, _, _ in rows}
    assert offices <= set(OFFICE_SIDO)
