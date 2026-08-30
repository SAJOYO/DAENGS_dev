"""PDF 포맷 층 단위 테스트 — **`data/raw/` 도 PyMuPDF 도 없이 돈다** (RAG-030 ①).

**픽스처가 진짜 PDF 가 아니다.** `extract/pdf.py` 가 문서에서 쓰는 것은 `page.get_text()` 와
`page.find_tables()` 둘뿐이라, 그 둘만 흉내 내는 가짜를 넘긴다. 이유는 셋이다:

  · 실물 약관을 커밋할 수 없다 (`data/` 는 미추적, 저작권도 걸린다)
  · PyMuPDF 로 한글 PDF 를 만들려면 CJK 폰트가 필요하고, 그건 **테스트가 아니라 픽스처를
    만드는 기술**이다. 실제로 `china-s` 로 만들었더니 한글이 한 자도 안 들어가 전부 깨졌다
  · 여기서 지키려는 것은 실물의 특정 문장이 아니라 **줄을 읽는 규칙**이다

그래서 이 파일은 `pdf` 그룹 없이도 돈다. 그룹이 필요한 것은 파서 쪽 한 곳뿐이고,
**거기서는 skip 이 아니라 안내와 함께 실패해야 한다** (`test_missing_group_fails_loudly`) —
파싱이 조용히 0건으로 끝나는 것이 이 레포가 두 번 겪은 실패다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.stages.parse.extract import pdf as pdfx
from daengs_life.rag.stages.parse.parsers.transport import korail_terms


class _Tables:
    def __init__(self, tables): self.tables = tables


class _Page:
    """`extract/pdf.py` 가 쓰는 두 가지만 흉내 낸다."""
    def __init__(self, lines, tables=()):
        self._text = "\n".join(lines)
        self._tables = list(tables)

    def get_text(self): return self._text
    def find_tables(self): return _Tables(self._tables)


class _Table:
    def __init__(self, rows): self._rows = rows
    def extract(self): return self._rows


def _doc(*pages):
    return list(pages)


# 실물에서 문제가 됐던 것만 골라 담았다. 각 줄이 왜 있는지는 아래 테스트가 말한다.
SAMPLE = [
    "여객운송약관",
    "제1장총칙",
    "제1조(목적) 이약관은한국철도공사가운영하는철도에서",
    "여객운송에관한사항을정함을목적으로합니다.",
    "제22조(휴대품) ①여객은다음각호의물품을제외하고",
    "2. 동물‧식물(다만, 예방접종을한반려동물을전용가방에넣은경우제외)",
    "③이약관에서정하지않은사항은연락운송을하는기관의약관",
    "제25조에서 정한 운임과 10,000원",
    "부칙",
    "제1조(시행일) 이약관은2026년8월1일부터시행합니다.",
    "정기승차권이용에관한약관",
    "제1조(목적) 이약관은정기승차권이용에관한사항을정합니다.",
]


@pytest.fixture(scope="module")
def parsed():
    doc = _doc(_Page(SAMPLE, tables=[_Table([["항목", "금액"], ["운임", "10000"]])]))
    return pdfx.elements(doc, "korail-terms-passenger__20260828", title="여객운송약관")


def _by_type(parsed, t: str):
    return [e for e in parsed.elements if e.type == t]


# ------------------------------------------------------------------ ① chunk_id 충돌
def test_element_ids_are_unique(parsed) -> None:
    """**이 파일에서 제일 중요한 단언이다.**

    실물에서 chunk_id 가 21개 겹쳤다 — 합본의 조 번호 재시작 · 부칙 제1조 · 조 참조가
    각각 원인이었다. 겹치면 골든셋 라벨이 어느 쪽을 뜻하는지 사라지는데 **예외가 안 난다**.
    """
    ids = [e.id for e in parsed.elements]
    assert len(set(ids)) == len(ids), f"중복: {[i for i in ids if ids.count(i) > 1]}"


def test_annex_articles_get_the_terms_prefix(parsed) -> None:
    """합본이라 제1조가 두 번 나온다. 뒤쪽 약관은 이름을 앞에 붙여 구별한다."""
    sections = [a.section for a in _by_type(parsed, "article")]
    assert "제1조" in sections                                # 본 약관
    assert "정기승차권이용에관한약관 제1조" in sections        # 부속약관
    # 첫 약관은 문서 제목과 같으므로 접두어를 안 붙인다 — 인용이 짧아진다
    assert "여객운송약관 제1조" not in sections


def test_article_reference_is_not_a_head(parsed) -> None:
    """`제25조에서 정한 운임과…` 는 **본문의 조 참조**다. 조 머리로 잡으면 안 된다.

    제목 괄호를 필수로 두는 것이 그 판정이고, 안 그러면 제25조가 하나 더 생긴다.
    """
    assert "제25조" not in [a.section for a in _by_type(parsed, "article")]


def test_body_line_ending_in_terms_is_not_a_boundary(parsed) -> None:
    """`③이약관에서…기관의약관` 은 항 본문이다. 띄어쓰기가 없어 제목처럼 보인다.

    항 번호로 시작하면 제목이 아니라는 규칙이 이것을 막는다 — 안 막으면 그 뒤 조가
    전부 엉뚱한 약관 접두어를 달게 된다.
    """
    assert parsed.counts["약관"] == 2                          # 여객운송약관 · 정기승차권약관
    assert not [h for h in _by_type(parsed, "heading") if "연락운송" in h.text]


# ------------------------------------------------------------------ ② 부칙
def test_addendum_is_a_para_not_an_article(parsed) -> None:
    """부칙은 `Para` 여야 한다.

    `Article` 로 내면 ⓐ 부칙 제1조가 본문 제1조와 겹치고 ⓑ 청커의 `_supplementary`
    필터(단문 시행일·타법개정 제외)를 안 탄다 (RAG-021 ①, RAG-033 ③).
    """
    paras = _by_type(parsed, "para")
    assert len(paras) == 1
    assert paras[0].section == "부칙"
    assert "시행일" in paras[0].text or "시행일" in (paras[0].title or "")
    assert "부칙" not in [a.section for a in _by_type(parsed, "article")]


# ------------------------------------------------------------------ ③ 본문 보존
def test_article_body_keeps_following_lines(parsed) -> None:
    """조 머리 다음 줄들이 그 조에 들어가야 한다. 안 그러면 본문이 통째로 사라진다."""
    a = next(x for x in _by_type(parsed, "article") if x.section == "제22조")
    assert "반려동물" in a.head
    assert a.chars == len(a.head)                              # 청커의 2,000자 판정 입력


def test_spaces_are_not_restored(parsed) -> None:
    """**띄어쓰기를 복원하지 않는다** (RAG-036).

    Kiwi `space()` 로 넣으면 검색이 나빠진다 — 붙어 있음이 복합어 신호라
    `core/tokenize.py` 의 복합어 복원이 그것을 쓴다.
    """
    a = next(x for x in _by_type(parsed, "article") if x.section == "제1조")
    assert "이약관은한국철도공사가운영하는" in a.head


# ------------------------------------------------------------------ ④ 표 판정
# ------------------------------------------------------------------ ⑦ 본문 한가운데의 별표 (RAG-048)
def test_annex_at_page_top_is_a_para_until_the_next_article() -> None:
    """KB 구형 약관 — `[별표1] …` 표 450줄이 앞 조 `제2조(준용규정)` 에 붙어 10,432자가 됐다."""
    doc = _doc(_Page(["제2조(준용규정) 이 추가특별약관에 정하지 않은 사항은 보통약관을 따릅니다."]),
               _Page(["[별표1] 동물보호법 시행규칙 별표 3의2 제1호", "등급", "보험금액", "1급", "8천만원"]),
               _Page(["2급", "7천2백만원", "반려동물위탁비용특별약관", "제1조(보상하는 손해) 회사는 …"]))
    parsed = pdfx.elements(doc, "d", title="KB반려행복펫보험")
    arts = [e for e in parsed.elements if e.type == "article"]
    assert [a.section for a in arts] == ["제2조", "반려동물위탁비용특별약관 제1조"]
    assert "등급" not in arts[0].head and "2급" not in arts[0].head
    paras = [e for e in parsed.elements if e.type == "para"]
    assert [p.section for p in paras] == ["별표"]
    assert paras[0].title.startswith("[별표1]") and "7천2백만원" in paras[0].text
    assert parsed.counts["별표"] == 1


def test_annex_reference_mid_page_is_body() -> None:
    """농협·KB 신형 본문은 `【별표1】『…』에 따릅니다.` 처럼 줄 머리에 별표를 달고 이어진다 — 쪽 첫 줄이 아니면 본문이다."""
    doc = _doc(_Page(["제5조(보험금의 지급) 회사는", "【별표1】『보험금을 지급할 때의 적립이율 계산』에 따릅니다.", "② 다음 항"]))
    parsed = pdfx.elements(doc, "d", title="x")
    arts = [e for e in parsed.elements if e.type == "article"]
    assert len(arts) == 1 and "적립이율" in arts[0].head
    assert not [e for e in parsed.elements if e.type == "para"]


def test_layout_box_is_not_a_table() -> None:
    """행 2 · 열 2 · 빈 셀 절반 미만 (RAG-032). 레이아웃 박스를 표로 오인하면
    빈 격자가 청크가 된다."""
    assert pdfx._valid_table([["항목", "금액"], ["운임", "10000"]])
    assert not pdfx._valid_table([["한 줄뿐"]])                  # 행 부족
    assert not pdfx._valid_table([["a"], ["b"]])                # 열 부족
    assert not pdfx._valid_table([["a", ""], ["", ""]])         # 빈 셀 과반


# ------------------------------------------------------------------ ⑤ 실패는 조용하지 않게
def test_missing_group_fails_loudly(monkeypatch) -> None:
    """`pdf` 그룹이 없으면 **안내와 함께 실패**해야 한다.

    조용히 0건으로 끝나면 RAG-030 ① 이 겪은 그 소실이 된다 — 재크롤을 해도 파싱이
    빈손인데 로그에는 성공만 남는다.
    """
    import builtins
    real = builtins.__import__

    def boom(name, *a, **kw):
        if name == "pymupdf":
            raise ImportError("No module named 'pymupdf'")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", boom)
    with pytest.raises(RuntimeError, match="uv sync --group pdf"):
        korail_terms._open(b"%PDF-1.4")


def test_unknown_document_raises() -> None:
    """소스에 첨부가 늘었는데 파서를 안 고치면 여기서 걸린다."""
    path = pathlib.Path("data/raw/transport/korail-terms-newone__20260828.pdf")
    doc = RawDoc(meta={}, path=path, meta_path=path.with_suffix(".meta.json"))
    with pytest.raises(RuntimeError, match="모르는 문서"):
        korail_terms.parse(b"%PDF-1.4 stub", doc)


def test_non_pdf_raises() -> None:
    path = pathlib.Path("data/raw/transport/korail-terms-passenger__20260828.pdf")
    doc = RawDoc(meta={}, path=path, meta_path=path.with_suffix(".meta.json"))
    with pytest.raises(RuntimeError, match="PDF 가 아니다"):
        korail_terms.parse(b"<html>", doc)


# ------------------------------------------------------------------ ⑧ 약관 경계 콜백 (RAG-051)
# 보험 대형 판의 실물을 줄인 것. 줄바꿈이 문장을 `…때에는 특별약관` 에서 끊어 그 조각이 정규식에
# 걸리고, 진짜 경계 `특별약관 일반사항` 은 `약관` 으로 끝나지 않아 안 걸린다. 콜백은 레이아웃(폰트
# 크기)을 아는 사이트 층이 준다 — 여기서는 그 결과만 흉내 낸다
_INS = re.compile(r"^(?![①-⑳\d])(?=.{4,60}$).*(?:보통약관|특별약관)$")
FRAGMENT = "작성한 때에는 특별약관"
HINTED = [
    ["보통약관", "제1조(목적) 이 보험계약은", "① 계약자가 중요사항을 고의로 사실과 다르게",
     FRAGMENT, "의 보장을 받지 못합니다."],
    ["특별약관 일반사항", "제1조(목적) 이 특별약관 일반사항은"],
    ["반려견 의료비(재가입형) 특별약관", "제1조(보험금의 지급사유) 회사는"],
]
_HINTS = {0: {"보통약관"}, 1: {"특별약관 일반사항"}, 2: {"반려견 의료비(재가입형) 특별약관"}}


def _hint_by_page(pages):
    index = {id(p): i for i, p in enumerate(pages)}
    return lambda page: _HINTS[index[id(page)]]


def test_boundary_hint_replaces_the_regex_on_that_page() -> None:
    """콜백이 집어 준 줄만 경계다 — 정규식에 걸리는 본문 조각은 경계가 아니고,
    정규식에 안 걸리는 `특별약관 일반사항` 은 경계다."""
    pages = [_Page(p) for p in HINTED]
    parsed = pdfx.elements(_doc(*pages), "d", title="펫보험", terms_re=_INS,
                           boundary_hint=_hint_by_page(pages))
    assert [a.section for a in _by_type(parsed, "article")] == \
        ["제1조", "특별약관 일반사항 제1조", "반려견 의료비(재가입형) 특별약관 제1조"]
    assert parsed.counts["약관"] == 3
    body = next(a for a in _by_type(parsed, "article") if a.section == "제1조").head
    assert FRAGMENT in body and "보장을 받지 못합니다" in body       # 조각이 조 안에 남는다


def test_boundary_hint_none_falls_back_to_the_regex() -> None:
    """콜백이 `None` 을 주는 쪽(레이아웃 정보가 없는 쪽)은 종전대로 정규식이다."""
    pages = [_Page(p) for p in HINTED]
    parsed = pdfx.elements(_doc(*pages), "d", title="펫보험", terms_re=_INS,
                           boundary_hint=lambda page: None)
    sections = [a.section for a in _by_type(parsed, "article")]
    assert f"{FRAGMENT} 제1조" in sections                        # 조각이 경계가 된다 (종전 동작)
    assert "특별약관 일반사항 제1조" not in sections                # 정규식은 이것을 못 잡는다


def test_without_a_hint_nothing_changes(parsed) -> None:
    """콜백을 안 넘기면 코레일 쪽은 한 줄도 다르지 않다 — 위 ①~③ 의 단언이 그 계약이다."""
    again = pdfx.elements(_doc(_Page(SAMPLE, tables=[_Table([["항목", "금액"], ["운임", "10000"]])])),
                          "korail-terms-passenger__20260828", title="여객운송약관")
    assert [e.id for e in again.elements] == [e.id for e in parsed.elements]
