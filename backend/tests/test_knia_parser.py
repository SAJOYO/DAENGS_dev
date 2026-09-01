"""손해보험협회 반려동물보험 공시 파서 — **`data/raw/` 없이 돈다** (RAG-030 ①).

`test_benefit24_parser.py` 와 같은 규칙이다. 아래 표본은 실제 응답에서 이 테스트가 보는 필드만
남긴 것이라 네트워크도 디스크도 안 탄다.

여기서 지키려는 것은 **행이 상품이 아니라 (상품 × 담보)라서 생기는 것들**이다 — 상품으로
되접는다는 것, 되접는 키가 상품명이 아니라 `TP_CODE` 라는 것(상품명은 겹친다), 보장내용이
`Table` 이라 청커의 표 규칙을 탄다는 것, 그리고 `'-'` 가 빈 값이라는 것.
"""
from __future__ import annotations

import pathlib

import pytest

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Table
from daengs_life.rag.stages.parse.parsers.insurance import knia_disclosure as knia

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _row(tp_code: str, tp_name: str, pay_name: str, reason: str, pay: str, **over) -> str:
    base = {
        "TP_CODE": tp_code, "TP_NAME": tp_name,
        "P_CODE": "N01", "P_CODE_NM": "메리츠화재",
        "TP_NEW_CHANNEL": "대면", "ETC5": "1566-7711",
        "TP_W_BILL": "34365", "TP_M_BILL": "34465",
        "TP_ETC": "ㆍ기본계약 : 20년만기 전기납, 40세\nㆍ자기부담금 3만원 기준",
        "TP_URL": "https://www.meritzfire.com/disclosure/product-announcement/product-list.do",
        "TP_PAY_NAME": pay_name, "TP_PAY_REASON": reason, "TP_PAY": pay,
    }
    base.update(over)
    return "{" + ",".join(f"{k}:'{v}'" for k, v in base.items()) + "}"


SAMPLE = ("{list:[" + ",".join([
    _row("N01A", "(무) 펫퍼민트 Cat&Family보험2604", "일반상해80%이상후유장해",
         "상해로 장해지급률이 80%이상에 해당하는 장해상태가 되었을 때", "1억원"),
    _row("N01A", "(무) 펫퍼민트 Cat&Family보험2604", "펫퍼민트 반려묘 입원의료비보장",
         "자기부담금을 차감한 금액의 80% 보상", "500만원 한도"),
    # **상품명이 같고 TP_CODE 만 다른 상품** — 현대해상에 실재한다
    _row("N01B", "(무) 펫퍼민트 Cat&Family보험2604", "갱신형 배상책임보장",
         "타인의 신체 피해에 대한 법률상 배상책임", "1사고당 500만원 한도",
         MIN_BILL="-", TP_W_BILL="-"),
]) + "]}").encode("utf-8")


def _doc() -> RawDoc:
    path = pathlib.Path("data/raw/insurance/knia-disclosure-N01__20260828.json")
    return RawDoc(meta={}, path=path, meta_path=path.with_suffix(".meta.json"))


@pytest.fixture(scope="module")
def parsed():
    return knia.parse(SAMPLE, _doc())


# ------------------------------------------------------------------ ① 상품으로 되접는다
def test_rows_fold_into_products_by_tp_code(parsed) -> None:
    """행 3개 = 상품 2개. **`TP_CODE` 로 접는다** — 상품명으로 접으면 둘이 하나로 뭉개진다."""
    assert parsed.counts == {"products": 2, "coverages": 3}
    ids = [e.id for e in parsed.elements]
    assert ids == [
        "knia-disclosure-N01__20260828#N01A",
        "knia-disclosure-N01__20260828#N01A-보장내용",
        "knia-disclosure-N01__20260828#N01B",
        "knia-disclosure-N01__20260828#N01B-보장내용",
    ]


def test_same_product_name_does_not_collide(parsed) -> None:
    """상품명이 같아도 요소 id 가 달라야 한다. 겹치면 뒤엣것이 앞엣것을 조용히 덮는다."""
    names = {e.section for e in parsed.elements}
    assert names == {"(무) 펫퍼민트 Cat&Family보험2604"}      # 이름은 하나인데
    assert len({e.id for e in parsed.elements}) == 4          # id 는 넷이다


# ------------------------------------------------------------------ ② 요소 타입 둘
def test_overview_is_article_and_coverage_is_table(parsed) -> None:
    """개요는 `Article`(=제목+본문 한 청크), 보장내용은 `Table`(=청커의 표 규칙)."""
    overview, coverage = parsed.elements[0], parsed.elements[1]
    assert isinstance(overview, Article) and overview.paragraphs == []
    assert isinstance(coverage, Table)
    assert coverage.header == ["담보명", "지급사유", "지급액"]
    assert coverage.rows == [
        ["일반상해80%이상후유장해",
         "상해로 장해지급률이 80%이상에 해당하는 장해상태가 되었을 때", "1억원"],
        ["펫퍼민트 반려묘 입원의료비보장", "자기부담금을 차감한 금액의 80% 보상", "500만원 한도"],
    ]


def test_table_title_does_not_repeat_the_product_name(parsed) -> None:
    """청커의 표 캡션이 `문서제목 + section + title` 이라, 둘 다에 상품명을 넣으면 두 번 나온다."""
    coverage = parsed.elements[1]
    assert coverage.title == "보장내용"
    assert coverage.section == "(무) 펫퍼민트 Cat&Family보험2604"


# ------------------------------------------------------------------ ③ 값 다루기
def test_overview_carries_the_answerable_fields(parsed) -> None:
    """`TP_ETC` 가 이 소스에서 자기부담금·기준계약 조건을 들고 있는 유일한 필드다."""
    head = parsed.elements[0].head
    assert head.startswith("(무) 펫퍼민트 Cat&Family보험2604\n")
    assert "보험회사: 메리츠화재" in head
    assert "판매채널: 대면" in head
    assert "자기부담금 3만원 기준" in head
    assert "특이사항: ㆍ기본계약 : 20년만기 전기납, 40세" in head


def test_premium_is_formatted_and_labelled_as_an_example(parsed) -> None:
    """`34365` 그대로 두면 읽기 어렵고, '예시' 를 빼면 누구에게나 그 값인 것처럼 읽힌다."""
    assert "예시보험료: 여성 34,365원 / 남성 34,465원" in parsed.elements[0].head


def test_dash_is_an_empty_value_not_a_literal(parsed) -> None:
    """원본은 빈 값을 `''` 가 아니라 `'-'` 로 준다. 그대로 실으면 `여성 -원` 이 된다."""
    assert knia._clean("-") == ""
    assert knia._won("-") == ""
    head = parsed.elements[2].head                      # TP_W_BILL 이 '-' 인 상품
    assert "여성 -" in head and "-원" not in head


# ------------------------------------------------------------------ ④ 문서 수준
def test_document_level_values(parsed) -> None:
    assert parsed.document_title == "메리츠화재 반려동물보험 공시"
    # 공시 기준일자가 원본 어디에도 없다. 상품명 끝의 `2604` 는 상품 개정 연월이지 공시 시점이 아니다
    assert parsed.published_at is None
    # 답변에 실을 링크는 그 회사 공시실이다 (원본이 행마다 들고 있다)
    assert parsed.citation_url.startswith("https://www.meritzfire.com/")


# ------------------------------------------------------------------ ⑤ 실패해야 하는 것
def test_empty_list_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="list 가 비었다"):
        knia.parse(b"{list:[]}", _doc())


def test_row_without_tp_code_fails_loudly() -> None:
    """`TP_CODE` 가 없으면 상품 경계가 사라진다 — 조용히 한 덩어리가 되게 두지 않는다."""
    bad = "{list:[{TP_NAME:'x',P_CODE_NM:'메리츠화재',TP_PAY_NAME:'a',TP_PAY_REASON:'b',TP_PAY:'c'}]}"
    with pytest.raises(RuntimeError, match="TP_CODE"):
        knia.parse(bad.encode("utf-8"), _doc())
