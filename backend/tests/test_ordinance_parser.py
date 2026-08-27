"""조례(자치법규) XML 파서 단위 테스트 — **`data/raw/` 없이 돈다** (RAG-030 ①, RAG-031 ③).

`test_parse.py` 는 실물 원본을 읽고 없으면 skip 한다. 그 skip 이 이관 버그를 한 번 숨겼고,
RAG-030 이 거기서 *"회귀 테스트는 `data/` 없이 도는 것으로 넣는다"* 를 남겼다. 이 파일은 그
규칙을 따른다 — 아래 XML 은 실제 응답에서 가져와 줄인 것이라 네트워크도 디스크도 안 탄다.

여기서 지키려는 것은 **법령 규격과 다른 지점 넷**이다. 조례 파서가 법령 파서를 닮아 가려는
힘이 계속 작용하는 자리라(둘 다 "조문 XML" 로 보인다), 어긋나면 여기서 깨져야 한다.
"""
from __future__ import annotations

import pathlib

import pytest

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Heading, Para
from daengs_life.rag.stages.parse.parsers.subsidy import ordinance_search

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# 실제 응답(MST=1834553 등)에서 이 테스트가 보는 부분만 남긴 것.
# `조문여부=N`(장 제목) · 가지번호 · 항/호 줄바꿈 · 부칙이 한 문서 안에 다 들어가도록 합쳤다.
SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<LawService>
  <자치법규기본정보>
    <자치법규ID>2232894</자치법규ID>
    <자치법규일련번호>1834553</자치법규일련번호>
    <공포일자>20230707</공포일자>
    <공포번호>5071</공포번호>
    <자치법규명>강원특별자치도 반려동물 보호 및 학대방지 조례</자치법규명>
    <시행일자>20230707</시행일자>
    <자치법규종류>C0001</자치법규종류>
    <지자체기관명>강원특별자치도</지자체기관명>
    <제개정정보>일부개정</제개정정보>
  </자치법규기본정보>
  <조문>
    <조 조문번호="000000"><조문번호>000000</조문번호><조문여부>N</조문여부>
      <조내용>제1장 총칙</조내용></조>
    <조 조문번호="000100"><조문번호>000100</조문번호><조문여부>Y</조문여부>
      <조제목>목적</조제목>
      <조내용>제1조(목적) 이 조례는 반려동물의 보호에 관한 사항을 규정함을 목적으로 한다.</조내용></조>
    <조 조문번호="000200"><조문번호>000200</조문번호><조문여부>Y</조문여부>
      <조제목>정의</조제목>
      <조내용>제2조(정의) 이 조례에서 사용하는 용어의 뜻은 다음과 같다.
1.“반려동물”이란 개ㆍ고양이 등 가정에서 기르는 동물을 말한다.
2.“소유자등”이란 반려동물을 사육ㆍ관리하는 사람을 말한다.</조내용></조>
    <조 조문번호="001203"><조문번호>001203</조문번호><조문여부>Y</조문여부>
      <조제목>중성화 지원</조제목>
      <조내용>제12조의3(중성화 지원) ① 도지사는 「동물보호법」 제2조에 따른 반려동물의 중성화 수술비를 지원할 수 있다.
② 제1항에 따른 지원의 기준은 규칙으로 정한다.</조내용></조>
  </조문>
  <부칙>
    <부칙공포일자>20230707</부칙공포일자>
    <부칙공포번호>5071</부칙공포번호>
    <부칙내용>부칙 &lt;제5071호, 2023. 7. 7.&gt;
이 조례는 공포한 날부터 시행한다.</부칙내용>
  </부칙>
</LawService>
"""


def _doc(meta: dict | None = None) -> RawDoc:
    """디스크를 안 탄다 — 파서는 바이트와 meta 만 본다."""
    path = pathlib.Path("data/raw/subsidy/ordinance-search-2232894__20260827.xml")
    return RawDoc(meta=meta or {}, path=path, meta_path=path.with_suffix(".meta.json"))


@pytest.fixture(scope="module")
def parsed():
    return ordinance_search.parse(SAMPLE.encode("utf-8"), _doc())


def _articles(parsed) -> dict[str, Article]:
    return {e.section: e for e in parsed.elements if isinstance(e, Article)}


# ------------------------------------------------------------------ ① 조문번호 인코딩
def test_article_number_is_four_digits_plus_two_digit_branch(parsed) -> None:
    """`001203` → 제12조의3. 법령은 `조문번호`+`조문가지번호` 두 태그였지만 조례는 한 필드다.

    잘라 읽는 자리를 잘못 잡으면 `제1203조` 가 되는데, **예외가 안 나고 인용만 조용히 틀린다.**
    """
    assert set(_articles(parsed)) == {"제1조", "제2조", "제12조의3"}


def test_chapter_heading_is_not_counted_as_an_article(parsed) -> None:
    """`조문여부=N` 은 장·절 제목이다 (법령의 `전문`). 이때 조문번호가 000000 이라
    번호에서 섹션을 못 만들고 본문에서 읽어야 한다."""
    headings = [e for e in parsed.elements if isinstance(e, Heading)]
    assert [(h.section, h.text) for h in headings] == [("제1장", "제1장 총칙")]
    assert parsed.counts["articles"] == 3          # 장 제목은 조 수에 안 들어간다


# ------------------------------------------------------------------ ② 항·호는 태그가 아니라 줄이다
def test_items_survive_as_line_breaks_not_as_paragraph_objects(parsed) -> None:
    """조례에는 `<항><호><목>` 이 없다. 줄로 보존하지 않으면 호 경계가 사라져
    답변에서 "몇 호까지가 한 호인지" 를 못 밝힌다."""
    art = _articles(parsed)["제2조"]
    assert art.paragraphs == []                    # 법령과 달리 항 객체가 안 생긴다
    lines = art.head.split("\n")
    assert len(lines) == 3
    assert lines[1].startswith("1.“반려동물”")
    assert lines[2].startswith("2.“소유자등”")


def test_article_head_keeps_the_whole_text_and_chars_matches(parsed) -> None:
    """`chars` 는 청커의 2,000자 판정 입력이다 (RAG-004). head 와 어긋나면 분할이 틀어진다."""
    art = _articles(parsed)["제12조의3"]
    assert art.head.startswith("제12조의3(중성화 지원) ①")
    assert "② 제1항에 따른" in art.head
    assert art.chars == len(art.head)


# ------------------------------------------------------------------ ③ 문서 헤더
def test_citation_url_is_the_name_url_not_the_serial_url(parsed) -> None:
    """`ordinSeq` 는 개정마다 바뀌어 링크가 늙는다 (RAG-031 ④). 이름 URL 을 쓴다."""
    assert parsed.citation_url == (
        "https://www.law.go.kr/자치법규/"
        "%EA%B0%95%EC%9B%90%ED%8A%B9%EB%B3%84%EC%9E%90%EC%B9%98%EB%8F%84"
        "%EB%B0%98%EB%A0%A4%EB%8F%99%EB%AC%BC%EB%B3%B4%ED%98%B8%EB%B0%8F"
        "%ED%95%99%EB%8C%80%EB%B0%A9%EC%A7%80%EC%A1%B0%EB%A1%80")
    assert "ordinSeq" not in parsed.citation_url


def test_meta_published_at_wins_over_the_body(parsed) -> None:
    """시행일자 기준을 목록조회 하나로 맞춘다 — 법령에서 본문 시행일자가 현행이 아니었던 전례."""
    assert parsed.published_at == "2023-07-07"
    assert ordinance_search.parse(
        SAMPLE.encode("utf-8"), _doc({"published_at": "2024-01-01"})).published_at == "2024-01-01"


def test_single_addendum_becomes_one_para(parsed) -> None:
    paras = [e for e in parsed.elements if isinstance(e, Para)]
    assert len(paras) == 1
    assert paras[0].section == "부칙"
    assert "공포한 날부터 시행한다" in paras[0].text


# 실제 응답(MST 2183723 등)에서 가져온 형태. `<부칙>` 태그는 하나인데 안에 개정별 부칙이
# 여러 벌 들어 있고, **각각에 제1조·제2조가 있다.**
MULTI_ADDENDUM = SAMPLE.replace(
    """<부칙내용>부칙 &lt;제5071호, 2023. 7. 7.&gt;
이 조례는 공포한 날부터 시행한다.</부칙내용>""",
    """<부칙내용>부    칙 &lt;조례 제2630호, 2018.12.04.&gt; 이 조례는 공포한 날부터 시행한다.
부    칙 &lt;조례 제2845호, 2022.10.18.&gt; 제1조(시행일) 이 조례는 공포한 날부터 시행한다.
제2조(다른 조례의 개정) 다른 조례 일부를 다음과 같이 개정한다.
부칙 &lt;조례 제2995호, 2025.12.31.&gt; 제1조(시행일) 이 조례는 2026년 1월 1일부터 시행한다.
제2조(다른 조례의 개정) 위원회 조례를 폐지한다.
제3조(경과조치) 종전의 위원회는 이 조례에 따른 위원회로 본다.</부칙내용>""")


def test_each_revision_addendum_becomes_its_own_para() -> None:
    """**한 문서 안에서 chunk_id 가 겹치던 실제 버그의 회귀 테스트다** (실측 5건).

    통째로 한 Para 에 넣으면 청커가 안쪽 "제2조" 를 보고 `#부칙-1제2조` 를 만드는데,
    개정마다 제2조가 있어 같은 id 가 두 번 나온다. 청크 주소는 골든셋이 가리키는 값이라
    겹치면 라벨이 어느 쪽을 뜻하는지 사라진다 — 그런데 **예외는 하나도 안 난다.**
    """
    out = ordinance_search.parse(MULTI_ADDENDUM.encode("utf-8"), _doc())
    paras = [e for e in out.elements if isinstance(e, Para)]
    assert [p.id.split("#")[-1] for p in paras] == ["부칙-1", "부칙-2", "부칙-3"]
    assert len({p.id for p in paras}) == 3
    # 경계는 머리줄이다. 원문의 `부    칙` 은 _clean 이 `부 칙` 으로 줄여 놓는다
    assert paras[0].title.startswith("부 칙 <조례 제2630호")
    assert paras[2].title.startswith("부칙 <조례 제2995호")
    # 각 부칙의 조가 자기 묶음 안에 남는다
    assert "제3조(경과조치)" in paras[2].text
    assert "제3조(경과조치)" not in paras[1].text


def test_element_ids_are_unique(parsed) -> None:
    """청크 id 의 근간이라 문서 안에서 유일해야 한다 (RAG-019)."""
    for src in (parsed, ordinance_search.parse(MULTI_ADDENDUM.encode("utf-8"), _doc())):
        ids = [e.id for e in src.elements]
        assert len(set(ids)) == len(ids)


def test_document_order_is_preserved(parsed) -> None:
    """장 제목 → 조문 → 부칙. 순서가 깨지면 사람이 읽을 때만 이상하고 테스트는 통과한다."""
    assert [type(e).__name__ for e in parsed.elements] == [
        "Heading", "Article", "Article", "Article", "Para"]


# ------------------------------------------------------------------ ④ 규격이 바뀌면 조용히 넘어가지 않는다
def test_wrong_root_raises_instead_of_returning_empty() -> None:
    """법령 XML(`<기본정보>`)을 이 파서에 물리면 0건이 아니라 예외여야 한다."""
    with pytest.raises(RuntimeError, match="자치법규기본정보"):
        ordinance_search.parse("<LawService><기본정보/></LawService>".encode("utf-8"), _doc())


def test_no_articles_raises() -> None:
    with pytest.raises(RuntimeError, match="조문이 하나도 없다"):
        ordinance_search.parse(
            "<LawService><자치법규기본정보><자치법규명>x</자치법규명>"
            "</자치법규기본정보><조문/></LawService>".encode("utf-8"), _doc())


def test_unreadable_article_number_is_kept_with_a_warning() -> None:
    """번호를 못 읽어도 조를 버리지 않는다 — 조용한 소실이 규격 변화를 가장 늦게 알린다."""
    broken = SAMPLE.replace(
        '<조 조문번호="000100"><조문번호>000100</조문번호>',
        '<조 조문번호="1"><조문번호>1</조문번호>')
    out = ordinance_search.parse(broken.encode("utf-8"), _doc())
    sections = [e.section for e in out.elements if isinstance(e, Article)]
    assert "제1조?" in sections
    assert out.counts["articles"] == 3
    assert any("조문번호를 읽지 못함" in w for w in out.warnings)
