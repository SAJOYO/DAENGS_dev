"""보조금24 JSON 파서 단위 테스트 — **`data/raw/` 없이 돈다** (RAG-030 ①).

`test_ordinance_parser.py` 와 같은 규칙이다. 아래 JSON 은 실제 응답에서 이 테스트가 보는
필드만 남긴 것이라 네트워크도 디스크도 안 탄다.

여기서 지키려는 것은 **조문 구조가 없는 소스라서 생기는 것들**이다 — 묶음이 청크 경계라는 것,
`||` 를 편다는 것, `None` 을 빈 값으로 본다는 것, 그리고 **요소 타입이 `article` 이어야 한다는
것**(`para` 로 내면 청커가 조용히 버린다).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article
from daengs_life.rag.stages.parse.parsers.subsidy import benefit24_services as b24

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


SAMPLE = json.dumps({
    "currentCount": 1, "matchCount": 10958, "page": 1, "perPage": 1, "totalCount": 10958,
    "data": [{
        "서비스ID": "305000000130",
        "서비스명": "취약계층 반려동물 의료비 지원",
        "소관기관명": "서울특별시 동대문구",
        "서비스목적": "취약계층 반려동물에게  필수 동물의료를 지원",
        "지원내용": "마리당 20만원 이내 지원,\r\n보호자 1만원 부담",
        "지원유형": "서비스(의료)",
        "지원대상": "기초생활수급자, 차상위계층 및 한부모가족",
        "선정기준": "관내 주민등록을 둔 사람",
        "신청방법": "지정 동물병원 방문신청",
        "신청기한": "~예산소진시까지",
        "구비서류": "신분증||동물등록증",
        "공무원확인구비서류": None,
        "본인확인필요구비서류": "해당없음",
        "온라인신청사이트URL": None,
        "접수기관명": "지정 동물병원",
        "문의처": "동대문구/02-2127-4000||다산콜/120",
        "법령": "동물보호법(제4조)||수의사법",
        "자치법규": None,
        "수정일시": "2026-08-14",
    }],
}, ensure_ascii=False)


def _doc(meta: dict | None = None) -> RawDoc:
    path = pathlib.Path("data/raw/subsidy/benefit24-services-305000000130__20260828.json")
    return RawDoc(meta=meta or {}, path=path, meta_path=path.with_suffix(".meta.json"))


@pytest.fixture(scope="module")
def parsed():
    return b24.parse(SAMPLE.encode("utf-8"), _doc())


def _by_section(parsed) -> dict[str, Article]:
    return {e.section: e for e in parsed.elements}


# ------------------------------------------------------------------ ① 요소 타입
def test_elements_are_articles_not_paras(parsed) -> None:
    """**`para` 로 내면 청커가 통째로 버린다** — `chunk.py` 의 `para: 소제목 밖`.

    easylaw 가 아닌 문서의 para 는 부칙이 아니면 드롭되는데 **예외가 안 난다.** 206개 청크가
    조용히 0이 되는 실패라, 타입을 바꾸는 순간 여기서 걸리게 해 둔다.
    """
    assert parsed.elements
    assert all(isinstance(e, Article) for e in parsed.elements)
    assert all(e.paragraphs == [] for e in parsed.elements)   # 항이 없어야 한 필드 = 한 청크다


# ------------------------------------------------------------------ ② 묶음이 경계다
def test_groups_become_sections_in_order(parsed) -> None:
    """묶음 이름이 곧 인용 문자열이다 ("… 지원내용"). 순서가 문서 순서다."""
    assert [e.section for e in parsed.elements] == [
        "서비스목적", "지원내용", "지원대상", "신청방법", "문의처", "근거법령"]
    assert parsed.counts["fields"] == 6


def test_a_group_keeps_field_names_in_the_body(parsed) -> None:
    """묶음 안에 필드가 둘 이상이면 어느 줄이 무엇인지 살아 있어야 한다."""
    body = _by_section(parsed)["지원대상"].head
    assert body.splitlines() == [
        "지원대상: 기초생활수급자, 차상위계층 및 한부모가족",
        "선정기준: 관내 주민등록을 둔 사람",
    ]


def test_empty_fields_are_skipped_not_rendered_as_none(parsed) -> None:
    """없는 필드는 `None` 으로 온다. 그대로 실으면 본문에 'None' 이 박힌다."""
    apply = _by_section(parsed)["신청방법"].head
    assert "None" not in apply
    assert "공무원확인구비서류" not in apply          # None 이라 빠진다
    assert "온라인신청사이트URL" not in apply
    assert "본인확인필요구비서류: 해당없음" in apply   # 값이 있으면 남는다
    # 자치법규가 None 이라 근거법령 묶음에는 법령만 남는다
    assert _by_section(parsed)["근거법령"].head == "법령: 동물보호법(제4조) / 수의사법"


def test_double_pipe_is_flattened(parsed) -> None:
    """`||` 는 이 API 의 다중값 구분자다. 그대로 두면 본문에 파이프가 박힌다."""
    assert "||" not in "\n".join(e.head for e in parsed.elements)
    assert "문의처: 지정 동물병원" not in _by_section(parsed)["문의처"].head   # 접수기관명과 안 섞인다
    assert "문의처: 동대문구/02-2127-4000 / 다산콜/120" in _by_section(parsed)["문의처"].head


def test_whitespace_is_squeezed(parsed) -> None:
    """`\\r\\n` 과 연속 공백이 원본에 섞여 온다."""
    assert _by_section(parsed)["지원내용"].head == (
        "지원내용: 마리당 20만원 이내 지원, 보호자 1만원 부담\n지원유형: 서비스(의료)")
    assert "  " not in _by_section(parsed)["서비스목적"].head


def test_chars_matches_head(parsed) -> None:
    """`chars` 는 청커의 2,000자 판정 입력이다 (RAG-004)."""
    assert all(e.chars == len(e.head) for e in parsed.elements)


# ------------------------------------------------------------------ ③ 문서 헤더
def test_citation_url_is_derived_from_the_service_id(parsed) -> None:
    """API 주소는 키가 `***` 로 가려져 사람이 못 연다 (RAG-012). 목록의 `상세조회URL` 과
    표본 6/6 이 일치해 서비스ID 하나로 유도한다."""
    assert parsed.citation_url == "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/305000000130"


def test_title_gets_the_org_prefix(parsed) -> None:
    """사업명에 지자체가 안 들어 있는 경우가 많다 — 검문소③에서 `동대문구` 질의의 1위가
    **충청북도** 사업이었다. 청크 본문이 `{document_title} {head}` 라 제목에 넣으면 들어간다."""
    assert parsed.document_title == "서울특별시 동대문구 취약계층 반려동물 의료비 지원"
    assert parsed.citation_url.endswith("/305000000130")     # 링크는 그대로다


def test_org_prefix_is_not_duplicated() -> None:
    """`충청북도 충청북도 …` 를 만들지 않는다."""
    other = json.loads(SAMPLE)
    other["data"][0]["소관기관명"] = "충청북도"
    other["data"][0]["서비스명"] = "충청북도 취약계층 반려동물 의료비 지원"
    out = b24.parse(json.dumps(other, ensure_ascii=False).encode("utf-8"), _doc())
    assert out.document_title == "충청북도 취약계층 반려동물 의료비 지원"


def test_published_at_is_the_modified_date(parsed) -> None:
    """지원사업은 해마다 바뀐다. 기준일이 답변에 실려야 낡은 정보를 알아챈다 (카드 메모 ⑤)."""
    assert parsed.published_at == "2026-08-14"


def test_modified_date_accepts_the_list_format() -> None:
    """상세는 `2026-01-29`, 목록은 `20260129201825` 로 형식이 다르다."""
    other = json.loads(SAMPLE)
    other["data"][0]["수정일시"] = "20260129201825"
    assert b24.parse(json.dumps(other, ensure_ascii=False).encode("utf-8"),
                     _doc()).published_at == "2026-01-29"


def test_extra_carries_the_corpus_link_fields(parsed) -> None:
    """`법령`·`자치법규` 는 코퍼스 안의 다른 문서와 **같은 문자열**이다 — 나중에 문서 간
    링크를 걸 접점이라 값을 흘리지 않는다."""
    assert parsed.extra["law"] == "동물보호법(제4조) / 수의사법"
    assert parsed.extra["ordinance"] == ""
    assert parsed.extra["org"] == "서울특별시 동대문구"
    assert parsed.extra["service_id"] == "305000000130"


# ------------------------------------------------------------------ ④ 규격이 바뀌면 조용히 넘어가지 않는다
def test_empty_data_raises() -> None:
    with pytest.raises(RuntimeError, match="data 가 비었다"):
        b24.parse(b'{"data": []}', _doc())


def test_missing_service_id_raises() -> None:
    with pytest.raises(RuntimeError, match="서비스ID"):
        b24.parse('{"data":[{"서비스명":"x"}]}'.encode("utf-8"), _doc())


def test_no_usable_field_raises() -> None:
    """필드 이름이 통째로 바뀌면 빈 문서를 만들지 말고 멈춰야 한다."""
    with pytest.raises(RuntimeError, match="본문 필드를 하나도 못 뽑았다"):
        b24.parse('{"data":[{"서비스ID":"1","서비스명":"x"}]}'.encode("utf-8"), _doc())


def test_missing_support_content_warns() -> None:
    """지원 내역이 없는 서비스는 껍데기다. 버리지는 않되 알려 준다."""
    other = json.loads(SAMPLE)
    other["data"][0]["지원내용"] = None
    other["data"][0]["지원유형"] = None
    out = b24.parse(json.dumps(other, ensure_ascii=False).encode("utf-8"), _doc())
    assert any("지원내용이 비었다" in w for w in out.warnings)
    assert "지원내용" not in {e.section for e in out.elements}
