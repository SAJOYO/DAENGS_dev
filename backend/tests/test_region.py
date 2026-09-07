"""지역 신호 → 남길 `org` (RAG-063).

`known` 은 전부 손으로 만든 값이다 — 이 테스트는 DB 도 코퍼스도 안 본다. 다만 **실재하는
흔들림을 그대로 픽스처로 쓴다** (`(구)광주광역시` · `전남광주통합특별시` · 붙어 쓴 조례명).
"""
from __future__ import annotations

from daengs_life.rag.core import region

# 2026-09-05 코퍼스의 `org` 값에서 골랐다 — 흔들림이 있는 것 위주다.
KNOWN = [
    "(구)광주광역시",
    "강원특별자치도",
    "강원특별자치도 고성군",
    "경기도 고양시",
    "경기도 수원시",
    "경상남도 고성군",
    "부산광역시",
    "부산광역시 남구",
    "부산광역시 동래구",
    "서울특별시 구로구",
    "전남광주통합특별시",
    "충청남도",
    "충청남도 당진시",
]


# ------------------------------------------------------------------ split_org
def test_split_org_separates_wide_and_local() -> None:
    assert region.split_org("부산광역시 동래구") == ("부산광역시", "동래구")
    assert region.split_org("부산광역시") == ("부산광역시", None)


def test_split_org_drops_the_legacy_prefix() -> None:
    """`(구)` 는 법제처가 폐지·통합된 기관에 붙인다. 사용자에게는 그냥 광주다."""
    assert region.split_org("(구)광주광역시") == ("광주광역시", None)


# ------------------------------------------------------------------ 규칙 넷
def test_wide_and_local_together_narrow_to_that_pair() -> None:
    """규칙 1 — 광역이 같이 적히면 그 조합으로 좁힌다. **광역 자신도 남는다.**

    "부산 동래구" 질문의 답이 부산광역시 조례에 있을 수 있어서다 — 광역 조례도 동래구민에게
    적용된다.
    """
    got = region.orgs("부산 동래구는 내장형 동물등록 비용을 지원해 주나요?", KNOWN)
    assert set(got) == {"부산광역시", "부산광역시 동래구"}
    assert "부산광역시 남구" not in got


def test_local_alone_keeps_every_city_with_that_name() -> None:
    """규칙 2 — `고성군` 은 강원에도 경남에도 있다. **하나를 고르면 틀릴 때 조용히 틀린다.**"""
    assert set(region.orgs("고성군 반려동물 지원", KNOWN)) == {
        "강원특별자치도 고성군", "경상남도 고성군"}


def test_wide_alone_keeps_the_locals_under_it() -> None:
    """규칙 3 — 지원 조례는 기초가 만든다 (RAG-033: 135곳 중 광역은 16%뿐)."""
    got = set(region.orgs("부산에서 반려동물 지원 받을 수 있나요?", KNOWN))
    assert got == {"부산광역시", "부산광역시 남구", "부산광역시 동래구"}


def test_no_region_signal_means_no_filter() -> None:
    """규칙 4 — 지역을 안 밝힌 질의는 전국을 봐야 한다."""
    assert region.orgs("강아지 등록 안 하면 어떻게 되나요?", KNOWN) == ()
    assert region.orgs("", KNOWN) == ()
    assert region.orgs("부산 동래구", []) == ()


# ------------------------------------------------------------------ 오탐 (이 카드의 실제 사고)
def test_the_cat_is_not_goyang_city() -> None:
    """**`고양이` 가 `고양시` 로 잡히면 안 된다.**

    골든셋 QA7 *"주인을 잃고 배회하는 고양이를 발견했는데요"* 가 실물이다. 부분 문자열로
    맞추던 첫 구현이 여기서 터졌고, 그래서 형태소 토큰 일치로 바꿨다 (모듈 머리말).
    **반려동물 도메인에서 `고양이` 보다 흔한 낱말이 없어서 이 오탐은 치명적이다.**
    """
    assert region.orgs("주인을 잃고 배회하는 고양이를 발견했는데요", KNOWN) == ()
    assert region.orgs("고양이 동물등록 해야 하나요?", KNOWN) == ()
    # 진짜 고양시는 여전히 잡혀야 한다 — 오탐을 막느라 정탐을 잃지 않았다.
    assert region.orgs("고양시에서 지원되나요?", KNOWN) == ("경기도 고양시",)


def test_josa_does_not_break_matching() -> None:
    """조사는 토크나이저가 떼 준다 — `부산에서` · `동래구는` · `당진시에서`."""
    assert "충청남도 당진시" in region.orgs("당진시에서 반려동물 진료비를", KNOWN)
    assert "서울특별시 구로구" in region.orgs("서울 구로구에서 중성화 수술비를", KNOWN)


# ------------------------------------------------------------------ 이름의 흔들림
def test_short_wide_aliases() -> None:
    """사용자는 "충남"이라 치고 코퍼스에는 "충청남도"가 있다. 접미사 규칙으로는 안 나온다."""
    assert set(region.orgs("충남 당진시 지원", KNOWN)) == {"충청남도", "충청남도 당진시"}


def test_merged_name_answers_to_both_halves() -> None:
    """`전남광주통합특별시` 는 통합 명칭이다 — "광주"로도 "전남"으로도 불린다.

    `startswith` 로 하면 "광주"를 놓친다. 그것이 `alias in wide` 인 이유다.
    """
    assert "전남광주통합특별시" in region.orgs("광주 반려동물 지원", KNOWN)
    assert "(구)광주광역시" in region.orgs("광주 반려동물 지원", KNOWN)
