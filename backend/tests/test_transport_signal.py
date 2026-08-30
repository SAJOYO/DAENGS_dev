"""교통수단 신호 → 배제 subcategory (RAG-052). **DB 없이 돈다** — 순수 함수 층이다.

지키려는 것은 `#75` 메모 ③ 의 핵심 판단이다: **신호가 없으면 필터도 없다.** 그리고 배제가
"남기기"가 아니라는 것 — 기차 질의에서 빠지는 것은 항공뿐이고, 조항 번호를 실은 해설
(`pet-life-guide`)은 건드리지 않는다.
"""
from __future__ import annotations

import pytest

from daengs_life.rag.core import transport


@pytest.mark.parametrize("text, mode", [
    ("기차에 반려동물은 몇 kg까지 태울 수 있나요?", "rail"),
    ("SRT 반려동물 이동장 크기 제한이 어떻게 되나요?", "rail"),     # 영문 대문자
    ("ktx 탈 때", "rail"),                                          # 소문자도
    ("광역철도에 강아지", "rail"),
    ("비행기 기내에 강아지를 데리고 탈 때 무게 제한이 어떻게 되나요?", "air"),
    ("기내 반입용 반려동물 케이지 크기 규격은 어떻게 되나요?", "air"),
    ("항공사마다 다른가요", "air"),
    ("지하철에 강아지를 데려가도 되나요?", "subway"),
    ("시내버스 탈 수 있나요", "bus"),
])
def test_reads_one_mode(text: str, mode: str) -> None:
    assert transport.modes(text) == {mode}


def test_no_signal_means_no_filter() -> None:
    """`#75` 메모 ③ — "반려동물 데리고 여행" 은 전부를 봐야 한다."""
    for q in ("반려동물 데리고 여행 갈 때 준비물", "목줄 안 하면 과태료 얼마인가요?", ""):
        assert transport.modes(q) == frozenset()
        assert transport.exclusions(q) == ()


def test_rail_excludes_only_air() -> None:
    """기차 질의에서 빠지는 것은 `transport-air` 뿐 — `pet-life-guide`(easylaw)는 배제 대상이 아니다."""
    assert transport.exclusions("기차에 반려동물은 몇 kg까지 태울 수 있나요?") == ("transport-air",)


def test_air_excludes_only_rail() -> None:
    assert transport.exclusions("비행기 기내에 강아지 무게 제한") == ("transport-rail",)


def test_subway_excludes_both_because_it_has_no_source_yet() -> None:
    """지하철 소스가 아직 없다 — SRT 안내가 지하철 질의 1위에 오던 것(lap10 T1)을 막는다."""
    assert set(transport.exclusions("지하철에 강아지를 데려가도 되나요?")) == {"transport-rail", "transport-air"}


def test_two_modes_keep_everything() -> None:
    """"기차나 비행기" 는 둘 다 필요하다 — 신호가 둘이면 아무것도 안 뺀다."""
    assert transport.modes("기차나 비행기로 강아지 데려갈 때") == {"rail", "air"}
    assert transport.exclusions("기차나 비행기로 강아지 데려갈 때") == ()


def test_every_excludable_subcategory_belongs_to_a_known_mode() -> None:
    """값 사전이 어긋나면 배제가 조용히 안 걸린다."""
    assert set(transport.SUBCATEGORY) <= set(transport.MODES)
