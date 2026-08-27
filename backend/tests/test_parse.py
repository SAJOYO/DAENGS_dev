"""파서 통합 테스트 — `data/raw/` 실물을 읽는다 (RAG-018, RAG-019, RAG-020).

**네트워크도 DB도 쓰지 않는다.** 파서는 그것이 가능한 유일한 층이라 여기가 가장 촘촘해야 한다.

`data/` 는 git 미추적이라(RAG-017) 다른 PC 에는 없을 수 있다. 원본이 없으면 실패가 아니라 skip 이다
— "크롤러를 먼저 돌려라"는 상태이지 코드가 틀린 것이 아니다.

수치를 그대로 박아 둔 이유 — 법령이 개정되면 이 테스트가 깨진다. 그게 목적이다.
청크 수가 조용히 달라진 채 6단계 점수만 떨어지는 것보다, 여기서 "원문이 바뀌었다"고 알려야 한다.
"""
from __future__ import annotations

import collections

import pytest

from daengs_life.rag.core import io
from daengs_life.rag.stages.parse import registry
from daengs_life.rag.core.ir import ELEMENT_ADAPTER

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _parse(source_id: str) -> dict[str, object]:
    docs = io.raw_docs(source_id)
    if not docs:
        pytest.skip(f"data/raw 에 {source_id} 원본이 없다 — `python -m crawler run --source {source_id}` 먼저")
    mod = registry.resolve(docs[0].domain, source_id)
    return {d.doc_id: mod.parse(d.path.read_bytes(), d) for d in docs}


@pytest.fixture(scope="module")
def law() -> dict:
    return _parse("law-drf-api")


@pytest.fixture(scope="module")
def easylaw() -> dict:
    return _parse("easylaw-pet")


def _counts(parsed: dict) -> collections.Counter:
    return collections.Counter(e.type for p in parsed.values() for e in p.elements)


def _elements(parsed: dict, doc_key: str, etype: str) -> list:
    doc = next(p for k, p in parsed.items() if doc_key in k)
    return [e for e in doc.elements if e.type == etype]


# ------------------------------------------------------------------ 계약
def test_every_element_satisfies_the_ir_contract(law: dict, easylaw: dict) -> None:
    """IR 6종 밖의 필드가 섞이면 청커가 그것을 못 본다 — 그 자리에서 실패해야 한다 (RAG-018)."""
    for parsed in (law, easylaw):
        for p in parsed.values():
            for e in p.elements:
                ELEMENT_ADAPTER.validate_python(e.model_dump(exclude_none=True))


def test_element_ids_are_unique_within_a_document(law: dict, easylaw: dict) -> None:
    """요소 id 는 청크 id 의 근간이고 골든셋이 가리키는 주소다 (RAG-019). 겹치면 정답이 흔들린다."""
    for parsed in (law, easylaw):
        for doc_id, p in parsed.items():
            ids = [e.id for e in p.elements]
            assert len(ids) == len(set(ids)), f"{doc_id}: id 중복"


# ------------------------------------------------------------------ 법령 XML
def test_law_element_totals(law: dict) -> None:
    c = _counts(law)
    assert len(law) == 8
    assert c["article"] == 629          # 조문. 장·절(전문)은 heading 으로 따로 센다
    assert c["heading"] == 36
    assert sum(p.counts["skipped_forms"] for p in law.values()) == 126   # 서식 제외 (RAG-004)


def test_article_keeps_paragraph_item_hierarchy(law: dict) -> None:
    """항 두문에만 금액·기한이 있고 호에는 없다 — 계층이 무너지면 청커가 조립할 수 없다."""
    art = next(e for e in _elements(law, "animal-protection-act", "article") if e.section == "제15조")
    assert art.title == "등록대상동물의 등록 등"
    assert len(art.paragraphs) == 7
    second = art.paragraphs[1]
    assert (second.sym, second.no) == ("②", 2)
    assert "신고하여야 한다" in second.text
    assert [i.no for i in second.items] == ["1.", "2."]
    assert "30일 이내" in second.items[1].text       # 질문 2 의 정답


def test_article_section_uses_branch_number(law: dict) -> None:
    """`조문가지번호` 는 패딩 없는 정수다. 잘못 만들면 인용 문자열이 조용히 틀린다."""
    sections = {e.section for e in _elements(law, "animal-protection-decree", "article")}
    assert "제12조의3" in sections
    assert "제12조의03" not in sections


def test_unnumbered_paragraph_wrapping_items(law: dict) -> None:
    """제2조처럼 항번호 없는 항이 호를 감싸는 경우가 있다."""
    art = next(e for e in _elements(law, "animal-protection-act", "article") if e.section == "제2조")
    assert len(art.paragraphs) == 1 and art.paragraphs[0].sym is None
    definitions = art.paragraphs[0].items
    assert any("맹견" in i.text and any("로트와일러" in s.text for s in i.subitems)
               for i in definitions)


def test_hanja_image_tags_are_removed(law: dict) -> None:
    """조문 텍스트에 `<img src="…flDownload.do…">…</img>` 가 박혀 있다 (RAG-004 가 넘긴 항목)."""
    texts = [i.text for e in _elements(law, "livestock-epidemic-act", "article")
             for p in e.paragraphs for i in p.items for s in [i, *i.subitems] for _ in [0]
             for i in [s]]
    blob = "\n".join(texts)
    assert "<img" not in blob and "</img>" not in blob and "flDownload" not in blob
    assert "부저병(腐?病)" in blob                  # 안쪽 한자는 원문이므로 살린다


def test_fine_table_is_cell_addressable(law: dict) -> None:
    """질문 1·3 의 정답. 셀로 분해되지 않으면 `제101조제3항제4호` 를 복원할 수 없다 (RAG-020)."""
    table = next(t for t in _elements(law, "animal-protection-decree", "table")
                 if t.section == "별표 4")
    assert table.title == "과태료의 부과기준" and table.related == "제35조 관련"
    assert table.unit == "(단위: 만원)"            # 999줄 표 전체에 단 한 번 나온다
    assert table.header == ["위반행위", "근거 법조문",
                            "과태료 금액 1차 위반", "과태료 금액 2차 위반", "과태료 금액 3차 이상 위반"]
    row = next(r for r in table.rows if r[0].startswith("라. 소유자가 법 제15조제1항"))
    assert row[1:] == ["법 제101조제3항제4호", "20", "40", "60"]      # 질문 1
    leash = next(r for r in table.rows if r[0].startswith("아. 소유자등이 법 제16조제2항제1호"))
    assert leash[1:] == ["법 제101조제4항제4호", "20", "30", "50"]    # 질문 3


def test_citation_url_is_human_openable(law: dict) -> None:
    """API 주소는 키가 마스킹돼 사람이 못 연다. 답변에 실을 링크는 따로 만든다 (RAG-019)."""
    p = next(v for k, v in law.items() if "veterinarian" in k)       # 웹 원문을 수집하지 않은 법령
    assert p.citation_url.startswith("https://www.law.go.kr/%EB%B2%95%EB%A0%B9/") or \
           p.citation_url.startswith("https://www.law.go.kr/법령/")
    assert "OC=" not in p.citation_url


# ------------------------------------------------------------------ easylaw HTML
def test_easylaw_element_totals(easylaw: dict) -> None:
    c = _counts(easylaw)
    assert len(easylaw) == 14
    assert c["heading"] == 61          # plv1a 20 + plv2a 41
    assert c["para"] == 196
    assert c["aside"] == 22            # tplv4 강조 박스
    assert c["qa"] == 10               # 100문100답 (탭이 비어 있는 페이지가 2개 있다)


def test_subheading_is_the_chunk_boundary(easylaw: dict) -> None:
    """RAG-004 가 대단위(인쇄체크)가 아니라 소제목(plv2a)을 청킹 단위로 확정했다."""
    heads = _elements(easylaw, "easylaw-pet-2-2-1__", "heading")
    assert [h.text for h in heads if h.level == 2][:3] == [
        "반려견 목줄 등 안전조치하기", "반려견 인식표 부착하기", "반려견 배설물 수거하기"]
    assert all("인쇄체크" not in h.text for h in heads)


def test_highlight_box_keeps_its_own_topic(easylaw: dict) -> None:
    """질문 7 의 정답. 앞 소제목에 붙이면 배설물 수거 이야기에 묻힌다."""
    box = next(a for a in _elements(easylaw, "easylaw-pet-2-2-1__", "aside")
               if "자연공원" in (a.title or ""))
    assert "국립공원" in " ".join(box.lines)


def test_qa_carries_normalized_related_laws(easylaw: dict) -> None:
    """관련법령은 본문에서 빼고 메타로 (RAG-004). RAG-004 section 형식으로 정규화한다."""
    qa = _elements(easylaw, "easylaw-pet-2-2-1-qna", "qa")[0]
    assert qa.question.endswith("?") and qa.answer
    assert "동물보호법 제23조제1항" in qa.related_laws
    assert "조회수" not in qa.title


def test_inline_citation_is_not_split_by_anchor_tags(easylaw: dict) -> None:
    """조문 인용이 `<a>` 로 감싸여 있어 줄바꿈으로 뽑으면 "제16조"와 "제2항"이 갈라진다."""
    paras = _elements(easylaw, "easylaw-pet-2-2-1__", "para")
    assert any("동물보호법 제16조제2항제1호" in c for p in paras for c in p.cites)
