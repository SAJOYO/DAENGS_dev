"""운송 소스 파서 단위 테스트 — **`data/raw/` 없이 돈다** (RAG-030 ①).

아래 HTML 은 실물에서 이 테스트가 보는 부분만 남긴 것이라 네트워크도 디스크도 안 탄다.

여기서 지키려는 것은 **정찰에서 두 번 데인 것들**이다 (RAG-036):
  ① 서울교통공사는 해설이 아니라 **조문형**이다 — `article` 이 아니면 조 단위 청크가 안 나온다
  ② SRT 는 본문이 `<img alt>` 에 있다 — 안 읽으면 제목만 남은 빈 문서가 조용히 적재된다
  ③ 병합 셀(`colspan`/`rowspan`)을 안 펴면 헤더와 값이 한 칸씩 밀린다. `zip` 이라 예외가 없다
"""
from __future__ import annotations

import pathlib

import pytest
from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Para, Table
from daengs_life.rag.stages.parse.extract import htmltable
from daengs_life.rag.stages.parse.parsers.transport import seoulmetro_terms as metro
from daengs_life.rag.stages.parse.parsers.transport import srt_terms as srt

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


METRO_HTML = """<html><head><title>여객운송약관(1~8호선) : 이용정보&gt;운임제도</title></head>
<body><div id="contents" class="contents">
  <div class="terms-area m-top40">
    <p class="text1">제정 2017. 5. 31. 규정 제60호</p>
    <p class="text1">개정 2026. 3. 7. 규정 제577호</p>
    <h4 class="title1">제 1 장 총칙</h4>
    <h5>제3조(정의)</h5>
    <p class="text1">이 약관에서 사용하는 용어의 정의는 다음 각 호와 같습니다.</p>
    <ol class="ol-type2 list1">
      <li>1. "여객"이란 운송계약을 맺은 사람을 말합니다.</li>
      <li>2. "승차권"이란 운송계약의 증표를 말합니다.</li>
    </ol>
  </div>
  <div class="terms-area">
    <h4 class="title1">제 7 장 휴대금지 및 제한 물품</h4>
    <h5>제35조(휴대금지품)</h5>
    <ol class="ol-type2 list1">
      <li>① 여객은 다음 각 호의 물품은 휴대할 수 없습니다.
        <ol class="ol-type2">
          <li>3. 사체</li>
          <li>4. 동물. 다만, 크기가 작은 애완동물로서 전용 이동장 등에 넣어 보이지 않게 하고,
              불쾌한 냄새가 발생하지 않도록 한 경우와 장애인보조견은 제외합니다.</li>
        </ol>
      </li>
      <li>② 직원은 휴대품의 내용을 확인할 수 있습니다.</li>
    </ol>
  </div>
  <div class="terms-area">
    <h4 class="title1">부 칙</h4>
    <p class="text1">(시행일) 이 규정은 공사의 설립등기일부터 시행한다.</p>
    <h4 class="title1">부 칙(2026.03.07.)</h4>
    <p class="text1">(시행일) 이 규정은 2026년 3월 7일부터 시행한다.</p>
    <p class="m-top40">[별표 3]</p>
    <h5 class="ag-c">정기권 이용거리 및 운임 <span>(제12조 관련)</span></h5>
    <div class="tbl-box1">
      <table class="tbl-type1">
        <caption>정기권 이용거리 및 운임 서울전용</caption>
        <thead>
          <tr><th colspan="3">정기권</th><th rowspan="2">종별 1회권 운임(원)</th>
              <th rowspan="2">종별 교통카드 운임(원)</th></tr>
          <tr><th>종별(단계)</th><th>운임(원)</th><th>이용거리</th></tr>
        </thead>
        <tbody>
          <tr><td>서울전용</td><td>68,200</td><td>서울시계내</td><td>1,850</td><td>1,750</td></tr>
        </tbody>
      </table>
    </div>
    <p class="text1">다만, 청소년이 1회권을 이용할 경우에는 어른용 1회권 운임이 적용됩니다.</p>
  </div>
</div></body></html>"""

SRT_ALT = ("SRT 반려동물 동반탑승 이용안내 (PDF다운로드).. SRT 탑승시 아래와 같이 알려드립니다."
           "-탑승가능한 반려동물 : 강아지, 고양이 등 작은 반려동물로 반려동물 이동장(45X30X25cm정도)에 "
           "넣은 것 이동장과 동물을 합친 무게가 10kg 이내의 것 "
           "-준수사항 : 광견병 예방접종 등 필요한 예방접종을 한 경우 여행이 가능합니다.")

SRT_HTML = f"""<html><head><title>반려동물 동반탑승 - SR</title></head><body>
<div class="sub_con_area">
  <div class="val_m"><h3 class="h3 dpib">반려동물/휴대품 탑승 기준 안내</h3></div>
  <div class="using_guide"><img alt="{SRT_ALT}" src="http://www.srail.or.kr/editor/x.png"></div>
</div></body></html>"""


def _doc(name: str, meta: dict | None = None) -> RawDoc:
    path = pathlib.Path(f"data/raw/transport/{name}__20260828.html")
    return RawDoc(meta=meta or {"source_url": "http://example.test/x"}, path=path,
                  meta_path=path.with_suffix(".meta.json"))


@pytest.fixture(scope="module")
def metro_parsed():
    return metro.parse(METRO_HTML.encode("utf-8"), _doc("seoulmetro-terms-line1-8"))


@pytest.fixture(scope="module")
def srt_parsed():
    return srt.parse(SRT_HTML.encode("utf-8"), _doc("srt-terms-pet"))


# ------------------------------------------------------------------ ① 조문형이다
def test_metro_articles_carry_section_numbers(metro_parsed) -> None:
    """조가 경계다. `section` 이 곧 인용 문자열이 된다 (RAG-019)."""
    arts = [e for e in metro_parsed.elements if isinstance(e, Article)]
    assert [a.section for a in arts] == ["제3조", "제35조", "별표 3"]
    assert arts[1].title == "휴대금지품"


def test_pet_exception_stays_inside_its_article(metro_parsed) -> None:
    """이 소스가 코퍼스에 있는 이유. 단서가 조 본문과 같은 요소 안에 있어야 한 청크가 된다."""
    art = next(e for e in metro_parsed.elements if e.id.endswith("제35조"))
    items = art.paragraphs[0].items
    assert [i.no for i in items] == ["3", "4"]
    assert "애완동물" in items[1].text and "장애인보조견" in items[1].text


def test_head_paragraph_takes_the_list_as_items_not_paragraphs(metro_parsed) -> None:
    """`제3조(정의)` 는 두문 `<p>` + 호 `<ol>` 이다. 호를 항으로 세면 청크가 잘게 부서진다."""
    art = next(e for e in metro_parsed.elements if e.id.endswith("제3조"))
    assert len(art.paragraphs) == 1
    assert art.paragraphs[0].sym is None
    assert [i.no for i in art.paragraphs[0].items] == ["1", "2"]


def test_supplementary_keeps_its_original_spacing(metro_parsed) -> None:
    """`부 칙(2026.03.07.)` 의 공백을 정규화하면 청커의 타법개정 판정에 잘못 걸린다."""
    supp = [e for e in metro_parsed.elements if isinstance(e, Para)]
    assert [s.section for s in supp] == ["부칙", "부칙"]
    assert supp[1].title == "부 칙(2026.03.07.)"


def test_attachment_note_is_an_article_not_a_para(metro_parsed) -> None:
    """별표 단서를 `Para` 로 내면 조문형 문서에서 청커가 조용히 버린다 (RAG-034 ④)."""
    note = next(e for e in metro_parsed.elements if "note" in e.id)
    assert isinstance(note, Article) and note.section == "별표 3"
    assert note.head.startswith("별표 3 다만,")


def test_published_at_is_the_last_revision_not_the_supplementary_date(metro_parsed) -> None:
    """부칙 날짜까지 훑으면 시행 예정일로 조용히 밀린다."""
    assert metro_parsed.published_at == "2026-03-07"


# ------------------------------------------------------------------ ③ 병합 셀
def test_merged_header_lines_up_with_body_columns(metro_parsed) -> None:
    """헤더 6칸 · 본문 5칸이면 `zip` 이 조용히 끊겨 값이 한 칸씩 밀린다 (RAG-036 ③)."""
    table = next(e for e in metro_parsed.elements if isinstance(e, Table))
    assert len(table.header) == len(table.rows[0]) == 5
    assert table.header[0] == "정기권 종별(단계)"
    assert table.header[-1] == "종별 교통카드 운임(원)"
    assert table.section == "별표 3"
    assert dict(zip(table.header, table.rows[0]))["종별 교통카드 운임(원)"] == "1,750"


def test_htmltable_falls_back_to_the_first_row_without_thead() -> None:
    html = "<table><tr><th>구분</th><th>금액</th></tr><tr><td>기본</td><td>1,550원</td></tr></table>"
    table = BeautifulSoup(html, "lxml").find("table")
    header, rows = htmltable.header_and_rows(table)
    assert header == ["구분", "금액"]
    assert rows == [["기본", "1,550원"]]


# ------------------------------------------------------------------ ② SRT 는 alt 가 본문
def test_srt_body_comes_from_the_image_alt(srt_parsed) -> None:
    """`.sub_con_area` 의 진짜 텍스트는 제목뿐이다. alt 를 안 읽으면 빈 문서가 된다."""
    paras = [e for e in srt_parsed.elements if isinstance(e, Para)]
    body = "\n".join(p.text for p in paras)
    assert "45X30X25cm" in body and "10kg" in body and "광견병" in body
    assert not srt_parsed.warnings


def test_srt_splits_the_alt_into_its_two_axes(srt_parsed) -> None:
    """`-탑승가능한 반려동물 :` `-준수사항 :` 이 원문의 축이고 질문도 그 축으로 갈린다."""
    paras = [e for e in srt_parsed.elements if isinstance(e, Para)]
    assert len(paras) == 3
    assert paras[1].text.startswith("탑승가능한 반려동물 :")
    assert paras[2].text.startswith("준수사항 :")
    assert "PDF다운로드" not in paras[0].text          # 이미지가 겸하는 버튼 라벨은 내용이 아니다


def test_srt_warns_when_the_image_disappears() -> None:
    """이미지가 텍스트로 바뀌었거나(좋음) 교체된 것(나쁨). 조용히 넘어가면 안 된다."""
    html = ('<div class="sub_con_area"><h3>반려동물 안내</h3>'
            '<p>이동장에 넣어 주세요.</p></div>')
    parsed = srt.parse(html.encode("utf-8"), _doc("srt-terms-pet"))
    assert parsed.warnings
    assert any("이동장에 넣어 주세요." == e.text for e in parsed.elements if isinstance(e, Para))
