"""보험약관 파서 단위 테스트 — **`data/raw/` 도 PyMuPDF 도 없이 돈다** (RAG-030 ①).

`test_pdf_extract.py` 와 같은 규칙이다. 파서가 문서에서 쓰는 것은 `page.get_text()` 뿐이라
(포맷 층에 넘기기 전 페이지 범위를 재는 데만 쓴다) 그것만 흉내 내는 가짜를 넘긴다.

여기서 지키려는 것은 **한 파일 안의 세 덩어리를 가르는 규칙**이다 (RAG-041 ②③) — 어디서
시작하고 어디서 끊는지, 그리고 그 판정이 목차·본문 문장에 속지 않는지. 실물 약관을 커밋할 수
없기도 하지만(저작권 — data-sources §12), 지켜야 하는 것이 특정 문장이 아니라 규칙이라 그렇다.
"""
from __future__ import annotations

import pytest

from daengs_life.rag.stages.parse.parsers.insurance import insurer_terms_pdfs as ins

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class _Page:
    def __init__(self, lines): self._text = "\n".join(lines)
    def get_text(self): return self._text


def _doc(*pages):
    """`_body_pages` 는 인덱싱과 `page_count` 만 쓴다."""
    class _Doc(list):
        page_count = property(len)
    return _Doc(_Page(p) for p in pages)


# 실물의 구조를 줄인 것. 쪽 번호가 곧 인덱스다
TOC = ["무배당 삼성화재 다이렉트 반려묘보험(2605.1)", "(재가입계약용) 보통약관",
       "제1관 목적 및 용어의 정의", "제1조 (목적)"] + [f"항목 {i}" for i in range(100)]
GUIDE = ["약관을 쉽게 이용할 수 있는 방법", "제8조(보험금의 지급절차)", "p.35"]
DIVIDER = ["무배당 삼성화재 다이렉트 반려묘보험(2605.1)", "(재가입계약용) 보통약관"]
BODY = ["제1관 목적 및 용어의 정의", "제1조 (목적)", "이 보험계약은 …"]
ANNEX = ["별표", "[별표1] 보험금을 지급할 때의 적립이율 계산"]
LAWS = ["[법규1] 의료법", "제2조(정의)"]


# ------------------------------------------------------------------ ① 범위 자르기
def test_body_starts_at_the_thin_divider_not_the_toc() -> None:
    """목차에도 `보통약관` 이 있다. **가르는 축은 문자열이 아니라 페이지의 두께다.**"""
    doc = _doc(["표지"], TOC, GUIDE, DIVIDER, BODY, ANNEX, LAWS)
    assert ins._body_pages(doc) == range(3, 5)      # DIVIDER 부터 ANNEX 전까지


def test_cut_at_annex_even_when_laws_come_later() -> None:
    """`[법규N]` 만 보면 그 사이의 별표가 남는다 — 실측에서 조 하나가 37,771자를 삼켰다."""
    doc = _doc(DIVIDER, BODY, ANNEX, ["[별표2] …"], LAWS)
    assert ins._body_pages(doc) == range(0, 2)


def test_cut_at_laws_when_there_is_no_annex() -> None:
    """소형 문서(53~63쪽)에는 `별표` 표제가 없다. 그때는 `[법규N]` 이 유일한 마커다."""
    doc = _doc(DIVIDER, BODY, LAWS)
    assert ins._body_pages(doc) == range(0, 2)


def test_no_markers_reads_everything() -> None:
    """**못 찾으면 자르지 않는다.** 임의로 자르면 약관 본문을 통째로 날릴 수 있다."""
    doc = _doc(["표지"], ["제1조 (목적)"], ["제2조 (정의)"])
    assert ins._body_pages(doc) == range(0, 3)


def test_empty_pages_do_not_break_the_scan() -> None:
    doc = _doc(DIVIDER, [], BODY, [], LAWS)
    assert ins._body_pages(doc) == range(0, 4)


# ------------------------------------------------------------------ ② 약관 경계 정규식
@pytest.mark.parametrize("line", [
    "(재가입계약용) 보통약관",
    "반려묘 수술비(치과및구강질환포함) 확대보장(재가입형) 특별약관",   # 34자·공백 있음
    "보험료 자동납입 특별약관",
    "제도성 특별약관",
])
def test_terms_boundary_accepts_real_headings(line: str) -> None:
    """포맷 층 기본값(공백 없는 4~30자)은 이것들을 **0개** 잡았다 (RAG-041 ⑤)."""
    assert ins._RE_INSURANCE_TERMS.match(line)


@pytest.mark.parametrize("line, why", [
    ("보장내용 등 필요한 사항을 정한 약관", "본문 문장 — `약관` 까지 열면 경계가 된다"),
    ("① 회사는 아래와 같은 사실이 있을 경우에는 이 특별약관", "항 번호로 시작하는 본문"),
    ("1-1. 반려묘 수술비 확대보장(재가입형) 특별약관", "목차 줄 — 번호 접두사"),
    ("제20조 (특별약관의 성립)", "조 머리이지 약관 이름이 아니다"),
    ("약관", "너무 짧다"),
])
def test_terms_boundary_rejects_look_alikes(line: str, why: str) -> None:
    assert not ins._RE_INSURANCE_TERMS.match(line), why


# ------------------------------------------------------------------ ③ 실패해야 하는 것
def test_not_a_pdf_fails_loudly() -> None:
    from daengs_life.rag.core.io import RawDoc
    import pathlib
    path = pathlib.Path("data/raw/insurance/insurer-terms-pdfs-samsung-X__20260829.pdf")
    doc = RawDoc(meta={"document_title": "x"}, path=path, meta_path=path.with_suffix(".meta.json"))
    with pytest.raises(RuntimeError, match="PDF 가 아니다"):
        ins.parse(b"<html>login</html>", doc)


def test_missing_title_fails_instead_of_inventing_one() -> None:
    """상품명은 크롤러가 목록에서 읽어 온다. 없으면 여기서 지어내지 않는다."""
    from daengs_life.rag.core.io import RawDoc
    import pathlib
    path = pathlib.Path("data/raw/insurance/insurer-terms-pdfs-samsung-X__20260829.pdf")
    doc = RawDoc(meta={}, path=path, meta_path=path.with_suffix(".meta.json"))
    with pytest.raises(RuntimeError, match="document_title"):
        ins.parse(b"%PDF-1.4 ...", doc)
