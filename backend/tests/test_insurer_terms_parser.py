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
    """**못 찾으면 자르지 않는다.** 임의로 자르면 약관 본문을 통째로 날릴 수 있다.

    `제1조(` 는 이제 머리 후보라(RAG-048 ⑤) 그것도 없는 문서로 본다."""
    doc = _doc(["표지"], ["제2조 (정의)"], ["제3조 (보상)"])
    assert ins._body_pages(doc) == range(0, 3)


# ------------------------------------------------------------------ ①-2 얇은 표제가 없는 판 (RAG-048 ⑤⑥)
LEGACY_TOC = ["- 3 -", "제7관 분쟁의 조정 등·······················13", "제31조(분쟁의 조정) ··········13",
              "단체취급특별약관(II) ······················91"]
LEGACY_BODY = ["- 1 -", "KB반려행복펫보험 보통약관", "제1관 목적 및 용어의 정의", "제1조(목적)", "이 보험계약은 …"]
LEGACY_LAWS = ["- 40 -", "【법규1】개인정보 보호법", "제1조(목적)"]


def test_falls_back_to_first_article_page_when_there_is_no_thin_divider() -> None:
    """KB 구형·농협은 표제 없이 `- 1 -` 뒤에 바로 `제1관 / 제1조(목적)` 이 온다."""
    doc = _doc(["KB반려행복펫보험"], LEGACY_TOC, LEGACY_BODY, ["- 2 -", "제2조(용어의 정의)"], LEGACY_LAWS)
    assert ins._body_pages(doc) == range(2, 4)


def test_fallback_skips_a_toc_without_dot_leaders() -> None:
    """삼성 소형 문서의 목차는 점선 없이 `… 목차 / 제1조(목적)` 이다 — 표제로 가른다."""
    doc = _doc(["반려견보험 애니펫 목차", "제1관 목적 및 용어의 정의", "제1조(목적)"],
               ["제1관 목적 및 용어의 정의", "제1조(목적)", "이 보험계약은 …"], LEGACY_LAWS)
    assert ins._body_pages(doc) == range(1, 2)


def test_thin_divider_wins_over_fallback() -> None:
    """순서가 중요하다 — 삼성은 얇은 표제가 먼저 잡혀 `제1조(` 규칙을 안 탄다 (11건 회귀 없음)."""
    doc = _doc(["표지"], TOC, GUIDE, ["제1조(목적)", "p.35"], DIVIDER, BODY, ANNEX, LAWS)
    assert ins._body_pages(doc) == range(4, 6)


@pytest.mark.parametrize("line", ["별표", "별 표", "별  표"])
def test_annex_marker_accepts_nh_spacing(line: str) -> None:
    """농협은 `별  표`(공백 둘)다."""
    doc = _doc(DIVIDER, BODY, [line, "【별표1】"], LAWS)
    assert ins._body_pages(doc) == range(0, 2)


@pytest.mark.parametrize("line", ["[법규1] 의료법", "【법규1】 개인정보 보호법", "【법규19】 어린이놀이시설 안전관리법 시행령"])
def test_law_marker_accepts_fullwidth_brackets(line: str) -> None:
    """삼성 `[법규1]` · KB·농협 `【법규1】`."""
    doc = _doc(DIVIDER, BODY, [line, "제2조(정의)"])
    assert ins._body_pages(doc) == range(0, 2)


# ------------------------------------------------------------------ ①-3 머리글·꼬리글·옆탭 제거 (RAG-048 ⑦)
KB_TITLE = "KB 금쪽같은 펫보험(강아지)(무배당)(26.07)"
NH_TITLE = "무배당 NH다이렉트펫앤미든든보험2604"


FILLER = [f"본문 {i}" for i in range(12)]          # 자리 규칙은 본문 쪽(얇지 않은 쪽)에서만 돈다


def _stripped(lines, title):
    return ins._StrippedPage(_Page(lines), ins._squash(title)).get_text().split("\n")


def test_kb_even_page_header_is_three_positional_lines() -> None:
    """쪽번호 / 상품명 / 현재 절 — 셋째 줄은 절 이름이라 규칙으로 못 가르고 자리로 뗀다."""
    assert _stripped(["54", KB_TITLE, "보통약관", "제1조(목적)", *FILLER], KB_TITLE) == ["제1조(목적)", *FILLER]
    # 앞 두 줄이 안 맞으면 셋째 줄을 건드리지 않는다 — 진짜 본문일 수 있다
    assert _stripped(["보통약관", "제1조(목적)", *FILLER], KB_TITLE) == ["보통약관", "제1조(목적)", *FILLER]


def test_kb_odd_page_side_tab_is_single_characters() -> None:
    assert _stripped(["공", "통", "사", "항", "제2조(정의)", "본문"], KB_TITLE) == ["제2조(정의)", "본문"]


def test_a_lone_single_character_line_is_content() -> None:
    """줄바꿈에 걸린 마지막 음절(삼성 `킴`)은 남는다 — 둘 이상 잇달아야 탭이다."""
    assert _stripped(["제2조(정의)", "…을 지", "킴", "다음 줄"], KB_TITLE) == ["제2조(정의)", "…을 지", "킴", "다음 줄"]


def test_samsung_thin_divider_survives_the_kb_header_rule() -> None:
    """삼성 소형 문서의 표제 쪽은 정확히 `쪽번호 / 상품명 / 보통약관` 세 줄 — KB 머리 규칙이 먹으면
    약관 경계가 사라진다. 얇은 쪽에는 자리 규칙을 걸지 않는다."""
    assert _stripped(["3", "반려견보험 애니펫", "보통약관"], "반려견보험 애니펫") == ["보통약관"]


def test_nh_running_headers_and_trailing_side_tab() -> None:
    """옆탭 `보통약관` 이 약관 경계로 잡히면 다음 쪽 본문이 조 밖으로 떨어진다 — 그래서 뗀다."""
    got = _stripped(["▶▶▶ 무배당 NH다이렉트펫앤미든든보험2604 약관", "- 44 -", *FILLER,
                     "무배당 NH다이렉트펫앤미든든보험2604 보통약관 ◀◀◀", NH_TITLE, "보통약관"], NH_TITLE)
    assert got == FILLER


def test_page_numbers_of_every_style_are_dropped() -> None:
    assert _stripped(["7", "33 / 229", "- 42 -", "-78-", "제3조(보상)"], KB_TITLE) == ["제3조(보상)"]


def test_a_bare_number_inside_the_page_is_content() -> None:
    """삼성 본문의 표를 편 번호 줄(`1`·`2`·…)은 쪽 한가운데 있다 — 첫 줄일 때만 쪽 번호다."""
    assert _stripped(["공", "통", "55", "제3조(보상)", "1", "2"], KB_TITLE) == ["제3조(보상)", "1", "2"]


def test_stripping_leaves_a_real_sentence_that_mentions_the_title() -> None:
    """상품명이 **들어간** 본문 줄은 남는다 — 상품명 **그 자체**인 줄만 뗀다."""
    line = "이 특별약관에 정하지 않은 사항은 무배당 NH다이렉트펫앤미든든보험2604"
    assert _stripped([line, "보통약관 및 해당 특별약관을 따릅니다."], NH_TITLE) == [line, "보통약관 및 해당 특별약관을 따릅니다."]


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


# ------------------------------------------------------------------ ④ 레이아웃 경계 (RAG-051, 위 ⑧)
class _StyledPage(_Page):
    """`get_text("dict")` 까지 흉내 낸 쪽. `(텍스트, 크기)` 로 받는다."""
    def __init__(self, styled):
        super().__init__([t for t, _ in styled])
        self._styled = styled

    def get_text(self, kind=None):
        if kind is None:
            return self._text
        return {"blocks": [{"type": 0, "lines": [
            {"spans": [{"text": t, "size": s}]} for t, s in self._styled]}]}


BODY_SIZE = 9.0
# 삼성 대형 판의 실물을 줄인 것 — 본문 9.0 · 조 머리 9.1 · 관 10.6 · 약관 이름 10.6 · 구분 표제 14.2
SAMSUNG = [
    [("무배당 삼성화재 다이렉트", 14.2), ("착한펫보험(강아지)(2605.1)(재가입계약용)보통약관", 14.2)],
    [("제1관 목적 및 용어의 정의", 11.4), ("제1조 (목적)", 9.1), ("이 보험계약은 …", 9.0),
     ("고의로 사실과 다르게 작성한 때에는 특별약관", 9.0), ("의 보장을 받지 못합니다.", 9.0),
     *[(f"② 회사는 다음 중 어느 한 가지의 경우에 보험금을 지급합니다 ({i})", 9.0) for i in range(8)],
     ("21 / 130", 10.0)],
    [("특별약관 일반사항", 14.2)],
    [("1.  펫 관련 특별약관", 14.2)],
    [("1-1. 반려견의료비(치과및구강질환포함)(수술당일제외,", 10.6), ("검사비포함)(재가입형) 특별약관", 10.6),
     ("제1조 (보험금의지급사유)", 9.1), ("①회사는…", 9.0), ("60 / 130", 10.0)],
    [("2-2 지정대리청구서비스Ⅲ특별약관", 10.6), ("제1관 일반사항", 10.6), ("제1조 (목적)", 9.1)],
]


def _layout_doc(pages, title="무배당 삼성화재 다이렉트 착한펫보험(강아지)(2605.1)(재가입계약용)"):
    class _Doc(list):
        page_count = property(len)
    return ins._StrippedDoc(_Doc(_StyledPage(p) for p in pages), title, range(len(pages)))


def test_body_size_is_the_char_weighted_mode() -> None:
    assert _layout_doc(SAMSUNG)._body_size == BODY_SIZE


def test_wrapped_name_is_joined_and_numbered_prefix_is_dropped() -> None:
    """두 줄로 감긴 이름은 한 줄이 되고 목차식 번호(`1-1. `)는 떨어진다 — 인용에 실리는 문자열이다."""
    page = _layout_doc(SAMSUNG)[4]
    assert page.boundaries() == {"반려견의료비(치과및구강질환포함)(수술당일제외, 검사비포함)(재가입형) 특별약관"}
    assert page.get_text().split("\n")[0] == "반려견의료비(치과및구강질환포함)(수술당일제외, 검사비포함)(재가입형) 특별약관"


def test_body_fragment_at_body_size_is_not_a_boundary() -> None:
    """`…때에는 특별약관` 은 정규식에 걸리지만 본문 크기다 — 그 쪽의 경계는 비어 있고 줄은 그대로다."""
    page = _layout_doc(SAMSUNG)[1]
    assert page.boundaries() == set()
    assert "고의로 사실과 다르게 작성한 때에는 특별약관" in page.get_text().split("\n")


def test_general_terms_divider_and_group_titles_are_boundaries() -> None:
    """`특별약관 일반사항` 은 `약관` 으로 안 끝나지만 제1조부터 다시 세는 진짜 경계다."""
    doc = _layout_doc(SAMSUNG)
    assert doc[2].boundaries() == {"특별약관 일반사항"}
    assert doc[3].boundaries() == {"펫 관련 특별약관"}                  # `1.  ` 가 떨어진다


def test_title_page_lines_are_joined_into_the_full_product_name() -> None:
    """표제 두 줄이 이어져야 `_same_document` 가 문서 제목과 겹친다고 보고 첫 약관에 접두어를 안 붙인다."""
    page = _layout_doc(SAMSUNG)[0]
    (name,) = page.boundaries()
    assert name == "무배당 삼성화재 다이렉트 착한펫보험(강아지)(2605.1)(재가입계약용)보통약관"


def test_division_head_at_display_size_is_not_joined_into_the_name() -> None:
    """`제1관 일반사항` 은 이름과 같은 크기(10.6)로 바로 뒤에 오지만 관 머리라 잇지 않는다.
    마침표 없는 번호(`2-2 `)도 떨어진다."""
    page = _layout_doc(SAMSUNG)[5]
    assert page.boundaries() == {"지정대리청구서비스Ⅲ특별약관"}
    assert page.get_text().split("\n")[:2] == ["지정대리청구서비스Ⅲ특별약관", "제1관 일반사항"]


def test_pages_without_dict_fall_back_to_the_plain_path() -> None:
    """가짜 쪽(`get_text()` 만)이 하나라도 있으면 문서 전체가 종전 경로 — 경계는 `None`, 줄은 그대로."""
    class _Doc(list):
        page_count = property(len)
    doc = ins._StrippedDoc(_Doc([_Page(["보통약관", "제1조(목적)"])]), "x", range(1))
    assert doc._body_size is None
    assert doc[0].boundaries() is None
    assert doc[0].get_text() == "보통약관\n제1조(목적)"


def test_layout_is_only_wired_for_measured_firms() -> None:
    """문턱은 판형이 정한다 — 크기 분포를 잰 삼성만. KB·농협은 정규식 그대로다."""
    assert ins._firm_key("insurer-terms-pdfs-samsung-ZPY008010_0_20260701__20260828") in ins._LAYOUT_FIRMS
    assert ins._firm_key("insurer-terms-pdfs-kb-25343_1_1__20260830") not in ins._LAYOUT_FIRMS
    assert ins._firm_key("insurer-terms-pdfs-nh-F004262903__20260830") not in ins._LAYOUT_FIRMS
