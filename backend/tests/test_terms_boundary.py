"""약관 경계가 **잘린 문장**을 제목으로 잡지 않는가 (RAG-068 / D3 · #285).

PDF 는 줄폭에 맞춰 문장을 자른다. 그래서 "줄 끝이 `보통약관`/`특별약관`" 이라는 모양은
진짜 제목만이 아니라 **끊긴 문장 조각**도 만족한다:

    며, 이로써 회사가 지급하여야 할 해약환급금이 있을 때에는 보통약관   ← 51자, 제목으로 잡혔다

증상이 조용하다 — 파싱도 청킹도 적재도 성공하고, **인용이 문장 중간에서 시작할 뿐**이다.
2026-09-06 실측으로 농협 589 · KB 341 청크가 그 상태였다 (삼성은 레이아웃 경계를 써서 0).

네트워크도 DB 도 `data/` 도 쓰지 않는다.
"""
from __future__ import annotations

import inspect

import pytest

from daengs_life.rag.stages.parse.extract import pdf
from daengs_life.rag.stages.parse.parsers.insurance import insurer_terms_pdfs as parser

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

# 실물에서 뽑은 것들이다 — 지어내지 않았다 (`tools/d3_terms_boundary_probe.py`).
TITLES = [
    "제도성 특별약관",
    "무배당 NH펫앤미든든보험2604 특별약관",
    "반려견 배상책임(단체용) 특별약관",
    "자동갱신 적용대상 특별약관",
    "상품다수구매자 보험계약 보험료정산 추가특별약관",
    "장애인전용보험전환 특별약관",
    "반려동물장제비 부보장 특별약관",
    "반려묘 수술비(치과및구강질환포함) 확대보장(재가입형) 특별약관",
]
FRAGMENTS = [
    "며, 이로써 회사가 지급하여야 할 해약환급금이 있을 때에는 보통약관",
    "이 특별약관",
    "장과 합산하여 ‘3-6. 반려동물 치료비Ⅲ(강아지, 일반형)’보장 특별약관",
    "된 경우 계약자는 그 최초연장된 날로부터 90일 이내에 연장된 특별약관",
    "이 특별약관에 정하지 않은 사항은 무배당 NH펫앤미든든보험2604 보통약관",
    "함합니다) 계약자는 해지된 날부터 3년 이내에 회사가 정한 절차에 따라 특별약관",
    "우에는 그 지급사유가 발생한 때부터 이 특별약관 계약은 소멸되며 이 특별약관",
]


@pytest.mark.parametrize("line", TITLES)
def test_a_real_terms_name_is_kept(line: str) -> None:
    """**하나라도 죽으면 그 약관의 조가 통째로 이름을 잃는다.** 규칙을 조일 때 여기부터 본다."""
    assert parser._looks_like_title(line), line


@pytest.mark.parametrize("line", FRAGMENTS)
def test_a_wrapped_sentence_is_rejected(line: str) -> None:
    assert not parser._looks_like_title(line), line


def test_all_of_these_would_pass_the_regex() -> None:
    """**정규식만으로는 못 가른다는 것이 이 카드의 전제다.**

    위 조각들이 전부 `_RE_INSURANCE_TERMS` 를 통과한다 — 그래서 두 번째 판단이 필요하다.
    이 단언이 깨지면 정규식이 이미 조여진 것이고, 그때는 이 규칙의 값이 달라진다.
    """
    assert all(parser._RE_INSURANCE_TERMS.match(line) for line in FRAGMENTS)


# ------------------------------------------------------------------ 함정 둘 (실측으로 배웠다)

def test_a_demonstrative_needs_a_space_after_it() -> None:
    """⚠ Kiwi 가 `이륜자동차` 를 `이`(MM)+`륜` 으로 쪼갠다.

    공백을 안 보고 지시관형사를 막으면 **삼성의 진짜 제목이 같이 죽는다.**
    """
    assert parser._looks_like_title("이륜자동차 운전 및 탑승 중 상해 부담보 특별약관")
    assert not parser._looks_like_title("이 특별약관")


def test_parenthesised_qualifiers_do_not_look_like_predicates() -> None:
    """⚠ `(1일1회한)` · `(깨짐, 부러짐)` 은 제목에 흔한 한정어인데 형태소로는 서술어처럼 보인다.

    괄호를 빼기 전에는 **삼성 52건이 오탐**이었다.
    """
    for line in ("창상봉합술 치료비(1일1회한) 특별약관",
                 "응급의료 아나필락시스 진단비(연간1회한) 특별약관",
                 "골절 진단비(치아 파절(깨짐, 부러짐) 제외) 특별약관"):
        assert parser._looks_like_title(line), line


def test_a_nominal_modifier_is_allowed() -> None:
    """`…에 관한 특별약관` 은 제목이다. 용언을 통째로 막으면 이것이 죽는다."""
    assert parser._looks_like_title("보험기간 설정에 관한 추가특별약관")


# ------------------------------------------------------------------ 배선

def test_the_format_layer_takes_a_guard() -> None:
    """포맷 층이 콜백을 받아야 사이트 층이 판단을 넘길 수 있다 (RAG-041 ④ 의 경계)."""
    assert "terms_guard" in inspect.signature(pdf.elements).parameters


def test_the_insurance_parser_passes_its_guard() -> None:
    """**넘기지 않으면 규칙이 죽은 코드가 된다.** 소스를 읽어 배선을 고정한다."""
    source = inspect.getsource(parser.parse)
    assert "terms_guard=_looks_like_title" in source


def test_the_parser_version_was_bumped() -> None:
    """산출이 바뀌었으므로 판을 올려야 `rag parse` 가 낡은 결과를 다시 만든다 (RAG-066 ②).
    안 올리면 `--force` 를 기억하는 사람에게 기대게 된다."""
    assert parser.VERSION >= 5
