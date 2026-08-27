"""청커 통합 테스트 — `data/processed/parsed/` 실물을 읽는다 (RAG-021).

**이 파일이 검문소①이다.** RAG-021 은 "구현 뒤 질문 1~7 의 정답 청크가 하나씩 실재하는지 눈으로
확인한다"를 재개 조건으로 걸었다. 눈으로만 보면 다음 개정 때 아무도 다시 안 본다. 그래서
그 확인을 테스트로 박는다 — 정답이 사라지면 6단계 점수가 아니라 **여기서 먼저 깨진다.**

`data/` 는 git 미추적이라(RAG-017) 다른 PC 에는 없다. parsed 가 없으면 실패가 아니라 skip 이다.
재수집하면 `chunk_id` 의 날짜가 바뀌므로 **주소는 논리 주소로 대조한다** — `_find` 참고.

수치를 박아 둔 이유는 `test_parse.py` 와 같다 — 법령이 개정되면 여기서 알려야 한다.
"""
from __future__ import annotations

import collections

import pytest

from daengs_life.rag.core import io
from daengs_life.rag.stages import chunk
from daengs_life.rag.stages.goldenset import logical

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

# 2026-08-24 실측. parsed 22건 기준
# 2026-08-27 갱신 — 조례 208건 추가 (RAG-031). parsed 230건 기준.
# 늘어난 몫은 전부 조례다: article +2,334 · para +52. 법령 쪽 수치(aside·heading·qa·table)는
# 그대로다 — 조례 XML 에는 별표·서식이 없고 장·절도 Heading 으로 안 샌다(청킹 대상이 아니다).
TOTAL = 3_793
BY_TYPE = {"article": 3054, "aside": 22, "heading": 40, "para": 244, "qa": 10, "table": 423}


@pytest.fixture(scope="module")
def chunks() -> list:
    paths = io.parsed_files()
    if not paths:
        pytest.skip("data/processed/parsed 가 비었다 — `python -m rag parse` 먼저")
    out = []
    for p in paths:
        _, res = chunk.chunk_file(p)
        out += res.chunks
    return out


@pytest.fixture(scope="module")
def by_id(chunks: list) -> dict:
    return {c.chunk_id: c for c in chunks}


def _find(chunks: list, id_part: str, *must: str) -> list:
    """주소는 **논리 주소**(수집 날짜를 뺀 것)로 대조한다 — 골든셋과 같은 규약이다 (RAG-022 ⑥B).

    `chunk_id` 에는 수집 날짜가 박혀 있어(``) 재수집하는 순간 값이 바뀐다. 실제 id 로
    적어 두면 코퍼스를 다시 만들 때마다 사람이 아래 12줄을 손으로 고쳐야 하고, 그 손질이
    "정답이 사라졌다"와 "날짜가 바뀌었다"를 구분하지 못하게 만든다. 조문 번호는 재수집해도
    그대로이므로 날짜만 떼면 주소는 그대로 유효하다.
    """
    return [c for c in chunks
            if id_part in logical(c.chunk_id) and all(m in c.content for m in must)]


# ---------------------------------------------------------------- 총량
def test_total(chunks: list) -> None:
    assert len(chunks) == TOTAL


def test_by_type(chunks: list) -> None:
    assert dict(collections.Counter(c.element_type for c in chunks)) == BY_TYPE


def test_chunk_id_unique(chunks: list) -> None:
    """`chunk_id` 는 5단계 골든셋이 정답을 가리키는 주소다 (RAG-021 ⑤B).

    별표 행은 마커를 가진 것이 56%뿐이고 그중 35건이 중복이라, `r{순번}` 폴백 없이는
    유일성이 깨진다. 그 폴백이 실제로 작동하는지가 여기서 확인된다.
    """
    dup = [k for k, v in collections.Counter(c.chunk_id for c in chunks).items() if v > 1]
    assert dup == []


# ---------------------------------------------------------------- ② 길이 기준
def test_hard_cap(chunks: list) -> None:
    """② 하드 상한 7,500자. 넘으면 4단계에서 조용히 잘린다."""
    over = [(c.chunk_id, c.chars) for c in chunks if c.chars > chunk.MAX_CHARS]
    assert over == []


def test_soft_cap_known_only(chunks: list) -> None:
    """RAG-004 2,000자를 넘는 것은 **아는 4건뿐**이다 (④ — 폴백을 두지 않기로 했다).

    조례에서 하나 늘었다 — `부칙-1제2조` 4,902자. 개정별로 쪼갠 뒤에도 남은 것이라
    (RAG-031 ③) 그 부칙 하나가 정말로 긴 경우다. 하드 상한 7,500 안이라 막지 않는다.

    늘어나면 ④ 를 재개할 트리거다. 그래서 통과가 아니라 목록을 고정한다.
    """
    over = sorted(c.chunk_id.split("#")[-1] for c in chunks if c.chars > chunk.SOFT_CHARS)
    assert over == ["별표 1의10-4-r0", "별표 1의10-6-r0", "부칙-1제2조", "제18조②"]


# ---------------------------------------------------------------- ① 입력 범위
def test_supplementary_marked(chunks: list) -> None:
    """부칙은 인덱싱하되 표시한다 — 6단계에서 재청킹 없이 필터로 끌 자리다 (①)."""
    sup = [c for c in chunks if c.part == "supplementary"]
    assert len(sup) == 244
    assert all(c.section.startswith("부칙 제") for c in sup)


def test_other_law_amendments_dropped(chunks: list) -> None:
    """타법개정은 이 법에 대한 규범이 아니라 남의 법 개정 부산물이다 (①).

    제목(`부칙(개인정보 보호법)`)과 **부칙 안의 조**(`제3조(다른 법률의 개정)`) 양쪽에서 걸러야 한다.
    후자는 ④ 에서 3,678자 청크로 드러난 구멍이었다.
    """
    assert _find(chunks, "#부칙", "다른 법률의 개정") == []


def test_form_tables_dropped(chunks: list) -> None:
    """헤더가 전무한 표 9개는 서식이다 — 표가 아니라 레이아웃이다 (④ 에서 발견).

    RAG-004 가 서식 126건을 뺀 것과 같은 종류다. 남아 있으면 `(서명 또는 인)` 같은
    빈 양식 필드가 검색 후보가 된다.
    """
    assert _find(chunks, "#별표", "서명 또는 인") == []


def test_appendix_asides_dropped(chunks: list) -> None:
    """`aside` 는 easylaw ※박스만 남는다. 별표에서 나온 도식·수식 박스 5건은 제외 (①)."""
    asides = [c for c in chunks if c.element_type == "aside"]
    assert len(asides) == 22
    assert all("easylaw" in c.doc_id for c in asides)


# ---------------------------------------------------------------- ③ 조립
def test_article_carries_law_name(chunks: list) -> None:
    """법령 조문 375개 중 자기 법령명이 본문에 등장하는 것은 0건이었다 (③(가)).

    캡션이 빠지면 `법 제101조` 가 넷 중 어느 법인지 알 수 없어 **인용 KPI 가 깨진다.**
    """
    arts = [c for c in chunks if c.element_type == "article"]
    assert arts and all(c.content.startswith(c.document_title) for c in arts)


def test_table_row_pairs_header_with_value(chunks: list) -> None:
    """`20` 이 20원인지 20만원인지는 헤더·단위와 붙어 있어야만 안다 (RAG-004 · ③(나))."""
    c = _find(chunks, "#별표 4-2-라")[0]
    assert "(단위: 만원)" in c.content
    assert "과태료 금액 1차 위반: 20" in c.content          # 헤더줄이 아니라 `헤더: 값`
    assert c.citation == "동물보호법 시행령 별표 4 라."       # 마커는 원문 표기 그대로


def test_easylaw_caption_keeps_h1(chunks: list) -> None:
    """h2 `이동장비에 넣는 등 안전조치를 취한 후 탑승하기` 가 4번 반복된다 (③(라)).

    h1(시내버스·고속버스·전철·기차)이 없으면 네 청크가 구분되지 않는다.
    """
    hits = [c for c in _find(chunks, "easylaw-pet-2-2-2",
                             "이동장비에 넣는 등 안전조치를 취한 후 탑승하기")
            if c.element_type == "heading"]          # 소제목 청크만 (같은 h1 밑 aside 는 제외)
    assert len(hits) == 4
    # 넷의 section 이 **완전히 같다** — 그래서 h1 이 캡션에 필요하다
    assert len({c.section for c in hits}) == 1
    # 캡션 2번째 줄이 h1 이고 넷이 서로 다르다 (시내버스·고속버스·전철·기차)
    assert len({c.content.split("\n")[1] for c in hits}) == 4


def test_qa_carries_related_laws(chunks: list) -> None:
    """qa 10건 중 본문에 법령명이 있는 것은 2건뿐이었다 (③(마) 정정).

    조문에서 법령명 복원율이 0% 였던 것과 같은 병리라 `related_laws` 를 본문에 넣는다.
    RAG-004 판정표의 "관련법령은 메타로" 를 정정한 결과다.
    """
    qas = [c for c in chunks if c.element_type == "qa"]
    assert len(qas) == 10
    assert sum("관련 법령: " in c.content for c in qas) == 9      # 1건은 related_laws 가 비어 있다


def test_row_self_contained(chunks: list) -> None:
    """청크 행은 자기완결적이다 (⑤A) — 하류 세 층이 파일 경계 없이 읽는다."""
    c = next(c for c in chunks if c.element_type == "article")
    assert c.document_title and c.category and c.raw_file
    assert c.citation_url                                      # 답변에 실을 링크가 행에 있다


# ---------------------------------------------------------------- 검문소① — 질문 1~7
# RAG-004 가 원문에서 확인한 정답 위치다. 이 목록이 곧 검문소①이고,
# 하나라도 깨지면 RAG-021 의 해당 절을 재개해야 한다.
ANSWERS = [
    ("Q1 등록 의무",        "animal-protection-act#제15조",   ["등록하여야 한다"]),
    ("Q1 과태료 100만원",   "animal-protection-act#제101조",  ["100만원 이하의 과태료"]),
    ("Q1 별표4 라목 금액",  "animal-protection-decree#별표 4", ["제15조제1항", "20", "40", "60"]),
    ("Q2 변경신고 30일",    "animal-protection-act#제15조",   ["30일 이내"]),
    ("Q3 목줄 안전조치",    "animal-protection-act#제16조",   ["안전조치"]),
    ("Q3 목줄 2미터",       "animal-protection-rule#제11조",  ["2미터"]),
    ("Q3 easylaw 목줄",     "easylaw-pet-2-2-1#h2",          ["목줄"]),
    ("Q4 로트와일러(법)",   "animal-protection-act#제2조",    ["로트와일러"]),
    ("Q4 맹견 범위(규칙)",  "animal-protection-rule#제2조",   ["로트와일러"]),
    ("Q5 맹견사육허가",     "animal-protection-act#제18조",   ["맹견사육허가"]),
    ("Q6 광견병 예방접종",  "livestock-epidemic-act#제15조",  ["예방접종"]),
    ("Q7 국립공원 ※박스",   "easylaw-pet-2-2-1#note",        ["자연공원"]),
]


@pytest.mark.parametrize("name,id_part,must", ANSWERS, ids=[a[0] for a in ANSWERS])
def test_checkpoint1(chunks: list, name: str, id_part: str, must: list) -> None:
    assert _find(chunks, id_part, *must), f"{name}: 정답 청크가 없다 — RAG-021 재개 조건"


def test_q4_subitem_survives(chunks: list) -> None:
    """질문 4 의 정답은 `제2조 제5호 **가목**` 이다 — 호가 아니라 목에 있다.

    `item.text` 에 목이 들어 있지 않으므로 조립에서 `subitems` 를 빼면 **로트와일러가 사라진다.**
    이 테스트는 그 회귀를 막는다 (구현 중 실측으로 발견).
    """
    c = _find(chunks, "animal-protection-act#제2조", "로트와일러")[0]
    assert "도사견" in c.content and "핏불테리어" in c.content
