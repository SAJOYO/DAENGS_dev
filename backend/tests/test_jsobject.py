"""JS 객체 리터럴 리더 — `json.loads` 가 못 여는 응답을 여는 층 (`crawler/core/jsobject.py`).

**네트워크도 디스크도 안 탄다.** 여기서 지키려는 것은 "JSON 과 다른 점"뿐이다 — 무따옴표 키,
홑따옴표 문자열, 값 안의 줄바꿈과 따옴표. 그 셋이 정규식 치환으로 때웠을 때 깨지는 자리다.

느슨해지지 않게 **실패해야 하는 것**도 함께 잠근다. 조용히 넘어가면 응답 규격이 바뀐 것을
못 알아채고, 증상은 청크 수가 줄어드는 것으로만 나타난다.
"""
from __future__ import annotations

import pytest

from daengs_life.crawler.core import jsobject

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ------------------------------------------------------------------ ① JSON 과 다른 점
def test_unquoted_keys_and_single_quoted_values() -> None:
    """실제 응답의 모양. `json.loads` 는 여기서 죽는다."""
    assert jsobject.loads("{list:[{TP_NAME:'위풍댕댕',TP_W_BILL:34365}]}") == {
        "list": [{"TP_NAME": "위풍댕댕", "TP_W_BILL": 34365}]
    }


def test_json_cannot_read_what_this_reads() -> None:
    """이 모듈이 존재하는 이유를 테스트로 박아 둔다."""
    import json
    with pytest.raises(json.JSONDecodeError):
        json.loads("{list:[{TP_NAME:'위풍댕댕'}]}")


def test_values_keep_newlines_and_double_quotes() -> None:
    """`TP_ETC` 는 줄바꿈으로 항목을 나누고 본문에 `"` 가 섞여 온다.

    따옴표를 바꿔치기하는 정규식으로 때우면 **여기서 문자열 경계가 깨진다.**
    """
    got = jsobject.loads("{TP_ETC:'ㆍ기본계약 : 20년만기\nㆍ\"자기부담금\" 3만원'}")
    assert got == {"TP_ETC": 'ㆍ기본계약 : 20년만기\nㆍ"자기부담금" 3만원'}


def test_escaped_quote_inside_value() -> None:
    assert jsobject.loads(r"{a:'2\'6'}") == {"a": "2'6"}


def test_numbers_booleans_null() -> None:
    got = jsobject.loads("{i:34365,f:104.9,neg:-3,t:true,f2:false,n:null}")
    assert got == {"i": 34365, "f": 104.9, "neg": -3, "t": True, "f2": False, "n": None}
    assert isinstance(got["i"], int) and isinstance(got["f"], float)


def test_quoted_keys_and_whitespace_are_allowed() -> None:
    assert jsobject.loads("{ 'a' : 1 , b : [ 2 , 3 ] }") == {"a": 1, "b": [2, 3]}


def test_empty_containers() -> None:
    assert jsobject.loads("{list:[]}") == {"list": []}


def test_trailing_comma_is_accepted() -> None:
    """거절해서 얻는 것이 없다 — 값을 하나도 잃지 않고, 사이트가 붙이는 날 수집만 멈춘다."""
    assert jsobject.loads("{a:1,}") == {"a": 1}
    assert jsobject.loads("[1,2,]") == [1, 2]


# ------------------------------------------------------------------ ② 실패해야 하는 것
@pytest.mark.parametrize("bad, why", [
    ("{a:1}{b:2}",      "뒤에 남은 문자"),
    ("{a:1",            "닫히지 않은 객체"),
    ("{a:'열린 채",      "닫히지 않은 문자열 = 응답이 잘렸다"),
    ("{a:undefined}",   "값이 아닌 토큰"),
    ("{a:1,a:2}",       "키 중복 — 조용히 덮으면 값 하나가 사라진다"),
])
def test_rejects_what_it_does_not_understand(bad: str, why: str) -> None:
    with pytest.raises(ValueError):
        jsobject.loads(bad)
