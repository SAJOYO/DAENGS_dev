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
import json

import pytest

from daengs_life.rag.core import io
from daengs_life.rag.stages import chunk
from daengs_life.rag.stages.goldenset import logical

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

# ---------------------------------------------------------------- 소스별 스냅샷 (RAG-042)
# **스냅샷은 소스별이다.** 합계 한 줄(`TOTAL` · `BY_TYPE`)이던 것을 쪼갰다 — 코퍼스를 늘리는
# 카드가 전부 그 한 줄에서 충돌했기 때문이다 (머지된 6개 중 4개. RAG-042). 이제 카드는
# **자기 소스 줄만** 더하고, 합계와 타입별은 아래에서 코드가 낸다.
#
# `docs` 는 **parsed 문서 수**다 (청크를 낸 문서 수가 아니다 — `NO_CHUNK_DOCS` 참고).
# 소스가 통째로 빠진 것(원본이 없는 PC)과 청크만 줄어든 것을 가른다.
#
# 이력 — 무엇이 언제 들어왔고 그 수에 **어떤 판단이 들어 있는지.** 줄을 고칠 때 같이 읽는다:
#  · 2026-08-27 `RAG-031` — 청커의 해설 분기를 소스 id 에서 문서 모양으로 바꿨다. `heading` 만
#    40 → 108 로 늘고 나머지가 그대로인 것이, 그 변경이 기존 코퍼스를 안 건드렸다는 증거다.
#  · 2026-08-28 `RAG-033` 조례 208건 · `RAG-034` 보조금24 37건 — 늘어난 몫이 전부 `article` 과
#    조례 `para`(부칙)다. **보조금24도 `article` 이다** — 조문이 아니라 필드 묶음인데 `para` 로
#    내면 청커가 조용히 버려서(`para: 소제목 밖`) 206청크가 0이 된다. RAG-034 ④ 가 이 수에 있다.
#  · 2026-08-28 `RAG-036` 코레일 약관 PDF 2건 — **표가 늘어난 것이 PDF 소스의 표시다.**
#    법령 별표와 달리 `find_tables()` 로 뽑은 것이고, 유효표 판정을 통과한 것만 들어 있다.
#  · 2026-08-28 `RAG-038` 손해보험협회 공시 7건 — **상품 하나가 요소 둘**이라 개요(`article`)와
#    보장내용(`table`)이다. 표 48개가 청크 62개인 것은 큰 표만 `헤더: 값` 으로 갈렸다는 뜻이다
#    (RAG-004 ③(나)).
#  · 2026-08-28 `RAG-039` 운송약관 HTML 3건 — **서울교통공사가 해설이 아니라 조문형이라서**
#    `article` 로 들어온다. 시드의 `pdf-entry` 분류가 틀렸다는 것이 이 수에 들어 있다.
#    `srt-terms` 의 `heading` 1 은 한 장짜리 안내다.
#  · 2026-08-29 `RAG-041` 삼성화재 약관 PDF 11건 — **부록을 잘라낸 뒤의 수다.** 안 자르면
#    `article` 이 4,693 이고 그 차이가 전부 관계법령 전문(신용정보법·상법 …)이다. 우리 문서가
#    아니라 인용을 틀리게 만든다 (RAG-041 ②).
#  · 2026-08-30 `RAG-048` KB 9건 · 농협 3건을 더해 23건 — 삼성 11건의 요소 수는 그대로다
#    (머리글·쪽 번호 줄만 빠졌다). KB 구형 2건의 본문 속 별표는 `para` 로 나와 청크로는 안 센다.
BY_SOURCE: dict[str, dict] = {
    # ---- 2026-08-29 실측 (이 워크트리에서 `chunk_file` 로 직접 셌다)
    "easylaw-pet":             {"docs": 14, "chunks": {"aside": 22, "heading": 40, "qa": 10}},
    "gov24-registration":      {"docs": 2,  "chunks": {"heading": 17}},
    "insurer-terms-pdfs":      {"docs": 23, "chunks": {"article": 4404, "table": 615}},
    "knia-disclosure":         {"docs": 7,  "chunks": {"article": 48, "table": 62}},
    "korea-kr-policy":         {"docs": 3,  "chunks": {"heading": 3}},
    # 2026-09-06 (RAG-065, #268) — 사료관리법 3법 + 공동주택관리법 3법을 더해 8 → 14.
    # 조문·별표가 대략 갑절이 되는데 **`반려동물` 이 한 번도 안 나오는 조문이 대부분이다** —
    # 사료 3법은 사업자 규제고, 공동주택 3법은 `가축` 이 시행령 제19조제2항제4호 한 번뿐이다.
    # 그 한 호와 사료 표시사항(법 제13조·시행규칙 제14조)이 이 여섯을 들인 값 전부다.
    "law-drf-api":             {"docs": 14, "chunks": {"article": 1140, "para": 362, "table": 676}},
    # 2026-09-06 (RAG-065) — 음식 3장(일반사료 구입 요령 · 반려견/반려묘 건강상식)을 더해 7 → 10.
    # 건강상식 두 장은 탭 6개 중 **먹이 둘만** 살아 문서당 청크가 3개다 (예방접종·계절별
    # 돌보기·수명표는 roadmap §5 의 🚫 건강 상식이라 파서가 버린다).
    "nias-pet":                {"docs": 12, "chunks": {"heading": 58}},
    "seoul-microchip-support": {"docs": 2,  "chunks": {"heading": 6}},
    # 항공 2곳 (RAG-046, #57). `table` 이 많은 것은 **문서가 곧 표**여서다 — 이스타는 표 하나가
    # 문서 전체이고(행마다 청크), 에어프레미아는 요금표를 페이로드에서 다시 세운 것이다.
    "airlines-pet-pages":      {"docs": 2,  "chunks": {"heading": 6, "table": 15}},

    # ---- 이 워크트리에는 원본이 없어 청킹으로는 못 쟀고, **서버 DB 의 적재분에서 확인했다**
    #      (RAG-042 ②). `documents.metadata` 에 `source_id` · `element_type` 이 그대로 있어
    #      타입별까지 대조된다. 다섯 줄 모두 단일 적재이고 `merged_from` 이 0 이라 적재 행 수가
    #      곧 청크 수다. 다시 세려면:
    #        select metadata->>'source_id', metadata->>'element_type', count(*)
    #        from documents group by 1, 2;
    "ordinance-search":        {"docs": 208, "chunks": {"article": 2334, "para": 52}},
    "benefit24-services":      {"docs": 37,  "chunks": {"article": 206}},
    "korail-terms":            {"docs": 2,   "chunks": {"article": 72, "para": 22, "table": 22}},
    "seoulmetro-terms":        {"docs": 2,   "chunks": {"article": 85, "table": 32}},
    "srt-terms":               {"docs": 1,   "chunks": {"heading": 1}},
}

# 요소가 하나도 없어 **청크를 0건 내는** parsed 문서. 100문100답 페이지인데 QnA 가 안 실린
# 둘이다. 목록으로 고정하는 이유는 RAG-030 ① 과 같다 — 조용히 0건이 되는 것을 합계 뒤에
# 숨기지 않는다. 늘어나면 파서 회귀를 먼저 의심한다.
NO_CHUNK_DOCS = {"easylaw-pet-2-1-1-qna", "easylaw-pet-2-2-2-qna"}

# ④ 소프트 상한(2,000자)을 넘는 청크 수. **막지 않고 세기만 한다** — RAG-004 ④ 가 폴백을 두지
# 않기로 했고, 늘어나면 그 결정을 재개하는 트리거다. 옆 주석이 무엇이 넘는지 말한다.
SOFT_CAP_OVER = {
    # 특별약관의 정의·보상 조. RAG-041 ⑦ 이 "경고만" 으로 둔 그것 (KB·농협 12건을 더해 91 → 177).
    # 2026-09-06 (RAG-068, #285) 177 → 179. **가짜 경계가 없어져 조가 길어진 결과다** —
    # 잘린 문장이 약관 제목으로 잡히면 거기서 조가 끊겨 짧아졌는데, 그것을 고치니 원래
    # 한 조였던 것이 다시 붙었다. RAG-004 ④ 는 이 수가 늘면 "폴백을 두지 않기로 한 결정을
    # 재개하는 트리거"라고 적어 뒀는데, 5,019청크 중 2건이라 지금은 트리거로 안 본다.
    "insurer-terms-pdfs": 179,
    "korail-terms": 2,          # 광역철도약관 제3조·제6조 — PDF 라 항으로 더 쪼갤 태그가 없다
    "law-drf-api": 4,           # 별표 1의10-4 · 1의10-6 · 제18조② + 공동주택관리법 시행령 제19조① (관리규약준칙 2,363자)
    "nias-pet": 2,              # 분실·유기 절(소제목 하나에 분실신고·습득신고·유기 셋) +
                                 # 2026-09-08 함께 여행가기(h2-1, 2,694자) — 운전 중 안기 금지부터
                                 # 검역·위탁관리업까지 주제 12개가 한 청크다. **소제목이 `para`
                                 # 블록 첫 줄에 묻혀 있어(heading 2개뿐, 옆 outing 페이지는 4개)
                                 # 헤더 인식이 못 본 파서 구멍이지 못 쪼개는 내용이 아니다** —
                                 # 후속 카드로 미룸 (#347 리뷰에서 확인, RAG-081 에 기록)
    "ordinance-search": 1,      # 부칙-1제2조 4,902자 — 개정별로 쪼갠 뒤에도 남은 긴 부칙
    "seoulmetro-terms": 2,      # 정의 조 — 항이 하나인데 그 안에 호가 26개다 (RAG-041 ④)
}

DOC_COUNT = sum(v["docs"] for v in BY_SOURCE.values())                    # 304
TOTAL = sum(sum(v["chunks"].values()) for v in BY_SOURCE.values())        # 6,368 (DB 와 일치)
BY_TYPE = dict(sorted(sum((collections.Counter(v["chunks"]) for v in BY_SOURCE.values()),
                          collections.Counter()).items()))


@pytest.fixture(scope="module")
def paths() -> list:
    out = io.parsed_files()
    if not out:
        pytest.skip("data/processed/parsed 가 비었다 — `python -m rag parse` 먼저")
    return out


@pytest.fixture(scope="module")
def chunks(paths: list) -> list:
    out = []
    for p in paths:
        _, res = chunk.chunk_file(p)
        out += res.chunks
    return out


@pytest.fixture(scope="module")
def parsed_docs(paths: list) -> dict:
    """소스별 parsed 문서 수. 헤더 한 줄만 읽는다 — 청크를 안 낸 문서도 여기서는 세어진다."""
    out: dict[str, set] = collections.defaultdict(set)
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            head = json.loads(fh.readline())
        out[head["source_id"]].add(logical(head["doc_id"]))
    return out


@pytest.fixture(scope="module")
def full_corpus(paths: list) -> bool:
    """전체 코퍼스가 있는 PC 인가. **전체에서만 뜻이 있는 단언을 여기서 가른다** (RAG-042).

    `data/` 는 git 미추적이라(RAG-017) PC 마다 가진 소스가 다르다. 합계·목록형 단언은
    전체가 아니면 틀리는 게 정상이므로 skip 하고, 소스별 단언은 **가진 소스에 대해서는
    그대로 건다** — 그래야 조례가 없는 PC 에서도 자기가 건드린 소스는 지켜진다.
    """
    return len(paths) == DOC_COUNT


def _require_full(full_corpus: bool, paths: list) -> None:
    if not full_corpus:
        pytest.skip(f"부분 코퍼스다 — parsed {len(paths)}건, 스냅샷은 {DOC_COUNT}건 기준")


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
def test_by_source(chunks: list) -> None:
    """소스별 스냅샷 — **가진 소스만 대조한다** (RAG-042).

    부분 코퍼스 PC 에서도 자기가 건드린 소스는 여기서 지켜진다. 반대로 소스가 통째로
    사라진 것은 이 테스트가 못 잡는다 — 그건 `test_total` 이 전체 PC 에서 잡는다.
    """
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for c in chunks:
        seen[c.source_id][c.element_type] += 1

    unknown = sorted(set(seen) - set(BY_SOURCE))
    assert unknown == [], f"스냅샷에 없는 소스다 — `BY_SOURCE` 에 줄을 더해라: {unknown}"

    diff = {sid: {"실측": dict(sorted(cnt.items())), "스냅샷": BY_SOURCE[sid]["chunks"]}
            for sid, cnt in seen.items()
            if dict(sorted(cnt.items())) != BY_SOURCE[sid]["chunks"]}
    assert diff == {}


def test_doc_count_per_source(parsed_docs: dict) -> None:
    """문서 수도 소스별로 본다 — 청크 수만 보면 **문서 하나가 통째로 빠진 것**을 놓친다."""
    assert set(parsed_docs) <= set(BY_SOURCE)   # 새 소스는 test_by_source 가 먼저 알려 준다
    diff = {sid: (len(v), BY_SOURCE[sid]["docs"]) for sid, v in parsed_docs.items()
            if len(v) != BY_SOURCE[sid]["docs"]}
    assert diff == {}, f"소스별 parsed 문서 수가 다르다 (실측, 스냅샷): {diff}"


def test_no_new_empty_docs(chunks: list, parsed_docs: dict) -> None:
    """청크를 0건 내는 문서가 늘지 않았다 — 합계 하나였을 때는 안 보이던 자리다 (RAG-042)."""
    chunked = {logical(c.doc_id) for c in chunks}
    empty = {d for v in parsed_docs.values() for d in v} - chunked
    assert empty <= NO_CHUNK_DOCS, f"청크를 0건 낸 문서가 새로 생겼다: {sorted(empty - NO_CHUNK_DOCS)}"


def test_total(chunks: list, paths: list, full_corpus: bool) -> None:
    """합계는 **전체 코퍼스에서만** 본다. 값은 `BY_SOURCE` 가 낸다 — 손으로 고칠 자리가 아니다."""
    _require_full(full_corpus, paths)
    assert len(chunks) == TOTAL


def test_by_type(chunks: list, paths: list, full_corpus: bool) -> None:
    _require_full(full_corpus, paths)
    assert dict(sorted(collections.Counter(c.element_type for c in chunks).items())) == BY_TYPE


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
    """RAG-004 2,000자를 넘는 것은 **아는 것뿐**이다 (④ — 폴백을 두지 않기로 했다).

    **목록이 아니라 소스별 건수로 고정한다** (RAG-042). 옛날에는 `chunk_id` 9개를 적어 뒀는데
    RAG-041 이 삼성 약관으로 91건을 들여오면서 못 쓰게 됐고, `#53` 이 그 목록을 갱신하지 않아
    **전체 코퍼스가 있는 PC 에서 계속 실패하고 있었다.** 91개를 나열하는 대신 소스별로 세면
    카드가 자기 줄만 고치면 되고, "늘어나면 ④ 를 재개한다"는 트리거는 그대로 산다.

    무엇이 넘는지는 `SOFT_CAP_OVER` 옆 주석에 있다. 하드 상한(7,500자)은 `test_hard_cap` 이
    따로 지키고, DB 전수로 확인해 초과 0건이다.
    """
    seen = collections.Counter(c.source_id for c in chunks if c.chars > chunk.SOFT_CHARS)
    present = {c.source_id for c in chunks}
    expect = {sid: n for sid, n in SOFT_CAP_OVER.items() if sid in present}
    assert dict(sorted(seen.items())) == dict(sorted(expect.items()))


# ---------------------------------------------------------------- ① 입력 범위
def test_supplementary_marked(chunks: list) -> None:
    """부칙은 인덱싱하되 표시한다 — 6단계에서 재청킹 없이 필터로 끌 자리다 (①).

    기대값은 `BY_SOURCE` 의 `para` 에서 낸다 — **부칙이 곧 `para` 다.** 박아 두면 코퍼스를
    늘리는 카드가 스냅샷과 여기 두 군데를 고쳐야 하고, 그 둘은 반드시 어긋난다 (RAG-042).
    """
    sup = [c for c in chunks if c.part == "supplementary"]
    seen = {c.source_id for c in chunks}
    assert len(sup) == sum(BY_SOURCE[s]["chunks"].get("para", 0) for s in seen)
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
