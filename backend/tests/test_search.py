"""8단계 검색 테스트 — 계약과 경계를 본다 (RAG-026).

**여기서 지키는 것 중 가장 중요한 것은 `search()` 의 시그니처다.** RAG-003(하이브리드·리랭커)이
들어오면 **바뀌어야 하는 것은 이 함수의 내부이지 시그니처가 아니다** — 호출하는 쪽(CLI·9단계·
FastAPI)이 검색 방식을 알면 그 교체가 세 곳을 고치는 일이 되고, 검문소③이 확인한 것과 서빙이
하는 것이 달라진다.

DB 가 필요한 것은 없으면 skip 한다 (`test_load.py` 와 같은 처리).
"""
from __future__ import annotations

import inspect

import pytest

# `ml` 그룹이 없으면 이 파일 전체를 건너뜁니다 (#230).
#
# **CI 는 torch 를 안 깝니다** — `ml` 은 임베딩 가중치까지 딸려 오는 무거운 그룹이라
# (D-021), 기본 설치로 도는 CI 에 넣을 것이 아닙니다. 그렇다고 그냥 두면 아래 테스트가
# `ModuleNotFoundError` 로 **깨져서** 전체 스위트가 빨간불이 됩니다 — 없는 그룹은
# 실패가 아니라 skip 이 맞고, `test_gait_inference.py` 가 `--group gait` 에 대해
# 같은 방식을 쓰고 있습니다.
#
# 로컬에서 이 파일을 돌리려면: `uv sync --group ml`
pytest.importorskip("sentence_transformers", reason="질의 임베딩에는 --group ml 이 필요합니다")

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed, evaluate, goldenset, load, search

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------- 상수 = 결정
def test_default_k_matches_the_judged_k() -> None:
    """검문소③이 보는 수 = RAG-024 ②의 판정 k.

    다르면 6단계 점수와 8단계 인상이 어긋나도 원인을 못 짚는다.
    """
    assert search.DEFAULT_K == 5 == evaluate.JUDGE_K


def test_supplementary_is_included_by_default() -> None:
    """RAG-026 ① — **기본은 부칙을 보여준다.**

    검문소③은 사람이 눈으로 보는 검사 자리이고, 기본값이 이미 걸러진 결과면 **무엇이 걸러졌는지**
    를 볼 수 없다. 서빙(9단계)의 기본값은 여기서 정하지 않는다 — 검사 도구와 서빙이 같은
    기본값을 쓸 이유가 없다.
    """
    assert inspect.signature(search.search).parameters["include_supplementary"].default is True


def test_search_signature_is_the_boundary() -> None:
    """RAG-026 ② — RAG-003 이 와도 **내부만** 바뀌어야 한다.

    이 목록이 바뀌면 CLI·9단계·FastAPI 가 같이 바뀐다는 뜻이고, 그때는 의도한 변경인지
    확인해야 한다.

    **2026-08-28 에 한 번 바뀌었다** (`query_vector` → `query`, RAG-035). 하이브리드의
    렉시컬 축은 벡터로 못 하고 **질의 텍스트가 있어야** 해서다. 규약의 뜻인 "호출부가 검색
    방식을 알면 안 된다"는 지켜졌다 — 호출부는 여전히 `search(encode(질문))` 이고,
    `Query` 가 벡터와 토큰을 함께 들고 다니므로 한쪽만 넘기는 실수를 할 수 없다.
    """
    params = list(inspect.signature(search.search).parameters)
    assert params == ["query", "k", "include_supplementary", "category", "conn"]


def test_query_carries_both_axes() -> None:
    """`Query` 가 벡터와 토큰을 함께 들고 다닌다 (RAG-035).

    따로 넘기게 두면 벡터만 넘기는 호출이 생기고 **렉시컬 축이 조용히 빠진다** — dense 가
    결과를 채워 주니 검색이 되는 것처럼 보인다. `make_query` 를 통과시키는 것이 계약이다.
    """
    q = search.make_query("부산 동래구는 내장형 동물등록 비용을 지원해 주나요?", [0.0] * 8)
    assert q.vector == [0.0] * 8
    assert "동래구" in q.tsquery
    assert " | " in q.tsquery          # AND 가 아니라 OR 다 (RAG-035)
    assert q.text.startswith("부산 동래구")


def test_empty_tsquery_falls_back_to_dense() -> None:
    """토큰이 하나도 안 남는 질의(기호뿐)에서 렉시컬 축은 그냥 비어야 한다.

    `to_tsquery('simple', '')` 는 예외를 내므로 SQL 이 `NULLIF` 로 0행을 만든다 —
    질의가 통째로 실패하는 것과 dense 단독으로 도는 것은 다르다.
    """
    assert search.make_query("!!! ???", [0.0] * 8).tsquery == ""


# ---------------------------------------------------------------- 골든셋에서 질문을 읽는다 (RAG-026 ②)
def test_questions_come_from_the_goldenset() -> None:
    """검증질문을 코드에 박지 않는다 — 박으면 질문 목록의 단일 소스가 둘이 된다.

    2026-08-27 에 조례 3문항(S1~S3)이, 08-28 에 보조금24 2문항(S4·S5)이 붙어 7 → 12 가 됐고,
    08-30 에 펫보험 5문항(I1~I5)·항공 2문항(T4·T5)이 붙어 22 가 됐고, 09-03 에 경계 6문항
    (B1~B6)이 붙어 28 이 됐다 (RAG-055).
    **이 수를 갱신하는 것 자체가 이 테스트의 일이다** — `--questions` 가 도는 범위라
    문항이 늘거나 줄면 검문소③④의 분모가 말없이 바뀐다.

    **기권·거절 문항도 여기 들어온다.** 랩이 그 질문을 실제로 돌려야 "답했나 말았나"를 잴 수
    있어서다 — 골든셋에만 적어 두고 랩이 안 물으면 아무것도 안 재진다 (RAG-055).

    09-04 에 사망·장례 3문항(FW1~FW3)이 붙어 33 이 됐다 (RAG-059).
    09-06 에 음식 4문항(FD1~FD4)과 주거 1문항(HS1)이 붙어 38 이 됐다 (RAG-065).
    09-08 에 증상 + 제도 3문항(B7~B9)이 붙어 41 이 됐다 (RAG-078 · D17).
    """
    items = search.hand_questions()
    gs = goldenset.load()
    assert [i[0] for i in items] == [i.id for i in gs.items if i.origin == "hand"]
    assert len(items) == 41
    assert all(q for _, q, _, _ in items)
    assert {"B2", "B4", "B6"} <= {i[0] for i in items}
    # 증상 + 제도 문항도 랩이 실제로 물어야 `false_refuse` 가 재진다 (RAG-078)
    assert {"B7", "B8", "B9"} <= {i[0] for i in items}
    # 프로필 문항도 여기로 온다 — 프로필은 `cmd_generate` 가 id 로 따로 붙인다 (RAG-056)
    assert {"DP1", "DP2"} <= {i[0] for i in items}


# ---------------------------------------------------------------- 교통수단 배제 (RAG-052)
class _Cursor:
    def __init__(self, conn): self._conn = conn; self._rows = []
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        # `params` 에 기본값이 있는 이유 — `search()` 가 본 검색 앞에 `SET hnsw.ef_search` 를
        # 파라미터 없이 보낸다 (RAG-084 ⑦). 없으면 그 한 줄에 TypeError 가 난다.
        self._conn.log.append((sql, params))
        # 지역 사전 질의(RAG-063)에만 답한다 — 본 검색은 그대로 0행이다
        self._rows = self._conn.orgs if "DISTINCT metadata->>'org'" in sql else []

    def fetchall(self): return self._rows


class _Conn:
    """`search()` 가 DB 에 보내는 SQL 을 받아 적는 가짜 연결. 본 검색 결과는 늘 0행이다.

    `orgs` 에 값을 넣으면 지역 사전 질의(RAG-063)만 그것을 돌려준다.
    """
    def __init__(self): self.log = []; self.orgs: list[tuple[str]] = []
    def cursor(self): return _Cursor(self)


def _sql_for(text: str):
    """`search()` 가 보낸 **본 검색** SQL. 지역 사전(RAG-063)을 묻는 질의는 건너뛴다.

    `search()` 는 `known_orgs()` 로 `org` 목록을 먼저 묻는다. 그것도 이 가짜 연결에 기록되므로
    `log[0]` 을 집으면 본 검색이 아니라 그 질의를 집는다 — 파라미터에 벡터가 있는 쪽이 본 검색이다.
    """
    search.forget_orgs()          # 앞선 테스트가 캐시에 남긴 것을 물려받지 않는다
    conn = _Conn()
    search.search(search.Query(vector=[0.0] * 4, tsquery="", text=text), k=5, conn=conn)
    sql, params = next((s, p) for s, p in conn.log if isinstance(p, dict) and "q" in p)
    return sql, params


def test_ef_search_is_set_before_the_search() -> None:
    """**HNSW 가 있으면 이 한 줄이 recall 을 정한다** (RAG-084 ⑦).

    pgvector 기본값 40 으로 두면 dense 축이 `CANDIDATE_N`(100)을 못 채운다 — 2026-09-09 실측으로
    최종 recall@8 이 88.5% 로 떨어지고 189질의 중 3분의 1의 top-8 이 달라졌다. **인덱스가 없으면
    아무 일도 안 하므로** 인덱스를 켜기 전에 배포해도 안전하고, 그래서 여기서 순서를 단언한다.
    """
    search.forget_orgs()
    conn = _Conn()
    search.search(search.Query(vector=[0.0] * 4, tsquery="", text="아무 질의"), k=5, conn=conn)
    sent = [sql for sql, _ in conn.log]
    ef = next(i for i, sql in enumerate(sent) if "hnsw.ef_search" in sql)
    main = next(i for i, (sql, p) in enumerate(conn.log) if isinstance(p, dict) and "q" in p)
    assert ef < main, "본 검색 뒤에 걸면 그 질의는 기본값 40 으로 돈다"
    assert f"= {search.EF_SEARCH}" in sent[ef]
    assert search.EF_SEARCH >= search.CANDIDATE_N,         "ef_search 가 후보 수보다 작으면 인덱스가 후보를 다 못 준다"


def test_transport_signal_excludes_the_other_mode_on_both_axes() -> None:
    """기차 질의는 `transport-air` 를 **dense·렉시컬 양쪽에서** 뺀다 — 한 축에만 걸면 RRF 가 도로 끌어온다.

    인자가 아니라 `query.text` 에서 읽는다 (RAG-040 과 같은 이유) — 시그니처 단언이 그대로인 것이 그 증거다."""
    sql, params = _sql_for("기차에 반려동물은 몇 kg까지 태울 수 있나요?")
    assert params["excluded"] == ["transport-air"]
    assert sql.count("subcategory <> ALL(%(excluded)s)") == 2


def test_region_signal_keeps_documents_without_an_org() -> None:
    """지역 신호가 있어도 **`org` 이 없는 문서는 남긴다** (RAG-063).

    "부산 동래구에서 목줄 안 하면 과태료?" 의 답은 조례가 아니라 동물보호법에 있고 법령에는
    `org` 이 없다. 절에 `IS NULL` 이 없으면 고치려던 것보다 큰 것이 사라진다.
    """
    search.forget_orgs()
    conn = _Conn()
    conn.orgs = [("부산광역시 동래구",)]
    search.search(search.Query(vector=[0.0] * 4, tsquery="", text="부산 동래구 지원"),
                  k=5, conn=conn)
    sql, params = next((s, p) for s, p in conn.log if isinstance(p, dict) and "q" in p)
    assert params["orgs"] == ["부산광역시 동래구"]
    # 두 축 모두에 걸린다 — 한 축에만 걸면 RRF 가 다른 축에서 도로 끌어온다
    assert sql.count("metadata->>'org' IS NULL OR") == 2


def test_empty_org_list_is_not_cached() -> None:
    """**빈 결과를 캐시하면 조용히 틀린다.**

    적재 전에 `search()` 를 한 번 부른 프로세스가 그 뒤로 영영 지역 필터를 안 켜는데,
    검색 자체는 계속 되므로 **아무 예외도 안 난다.** 그래서 빈 값일 때만 매번 다시 묻는다.
    """
    search.forget_orgs()
    empty = _Conn()
    assert search.known_orgs(empty) == ()
    filled = _Conn()
    filled.orgs = [("부산광역시",)]
    assert search.known_orgs(filled) == ("부산광역시",)   # 앞의 () 를 물려받지 않았다
    search.forget_orgs()


def test_no_transport_signal_means_no_exclusion_clause() -> None:
    """`#75` 메모 ③ — 수단이 안 적힌 질의는 필터가 안 걸린다."""
    sql, params = _sql_for("반려동물 데리고 여행 갈 때 준비물")
    assert "excluded" not in params
    assert "subcategory <> ALL" not in sql


def test_every_answer_question_has_a_must_label() -> None:
    """정답 있는 문항에 라벨이 없으면 검문소③의 ★ 표시가 의미를 잃는다.

    **기권·거절 문항은 반대로 비어 있어야 한다** (RAG-055) — 물러서는 것이 정답인 질문에
    ★ 가 찍히면 그 표시가 거짓말을 한다. 그래서 `must` 없음을 금지하는 대신 `expect` 로 가른다.
    """
    gs = goldenset.load()
    expect = {i.id: i.expect for i in gs.items}
    for qid, _, must, _ in search.hand_questions():
        if expect[qid] == "answer":
            assert must, f"{qid}: 답변 문항인데 must 라벨이 없다"
        else:
            assert not must, f"{qid}: {expect[qid]} 문항인데 must 라벨이 있다"


def test_tier_strips_the_collection_date() -> None:
    """라벨은 **수집 날짜를 뺀 논리 주소**다 (RAG-022 ⑥B). 실제 `chunk_id` 에는 날짜가 있다."""
    must = {"law-drf-api-animal-protection-act#제18조"}
    real = "law-drf-api-animal-protection-act__20260820#제18조"
    assert search.tier_of(real, must, set()) == "must"
    assert search.tier_of(real, set(), must) == "nice"
    assert search.tier_of(real, set(), set()) == "-"


# ---------------------------------------------------------------- DB 가 있을 때만
def _ready_or_skip():
    try:
        conn = load.connect()
    except Exception as exc:
        pytest.skip(f"DB 에 연결할 수 없다 ({type(exc).__name__}) — `docker compose up -d`")
    if load.count(conn) == 0:
        conn.close()
        pytest.skip("아직 적재하지 않았다 — `python -m rag load` 먼저")
    return conn


@pytest.fixture(scope="module")
def vector():
    """질의 벡터 하나. 모델 로드가 무거워 모듈당 한 번만 만든다.

    **모델은 `config` 에서 받는다 — 여기에 박지 않는다** (RAG-045). 질의 모델이 적재 모델과
    다르면 코사인이 무의미해지는데 **차원이 같아(1024) 예외가 하나도 안 난다.** 그러면 아래
    단언들이 "검색이 고장 났다"고 말하지만 실제로 고장 난 것은 이 한 줄이다 — 2026-08-29 에
    `test_checkpoint3_finds_the_maengyeon_article` 이 정확히 그렇게 깨졌다.
    """
    key = config.settings.embedding_model_key
    if not embed.parquet_path(key).is_file():
        pytest.skip(f"{key}.parquet 이 없다")
    return search.encode("맹견 사육 허가 필요한가요?", model_key=key)


def _rrf(h) -> float:
    """SQL 이 **정렬에 쓰는 그 값** 을 그대로 재현한다 (RAG-035).

    `dense_rank`/`lexical_rank` 가 `None` 이면 그 축의 후보에 없었다는 뜻이라 0 을 더한다 —
    SQL 의 `COALESCE(..., 0)` 과 같다.
    """
    d = 1.0 / (search.RRF_K + h.dense_rank) if h.dense_rank is not None else 0.0
    lx = search.LEXICAL_WEIGHT / (search.RRF_K + h.lexical_rank) if h.lexical_rank is not None else 0.0
    return d + lx


def test_returns_k_hits_ranked(vector) -> None:
    """**앞의 k 개**는 순위 1..k, 그리고 **RRF 내림차순**이다.

    ⚠️ **점수(코사인) 내림차순이 아니다** (RAG-045 에서 고쳤다). 예전에는 그렇게 단언했고
    순수 dense 였던 시절(RAG-026)에는 맞았는데, RAG-035 가 하이브리드로 바꾸면서
    `ORDER BY f.rrf DESC, embedding <=> q` 가 됐다. **정렬 키는 RRF 이고 `score` 칸은 여전히
    코사인**이라, 형태소 축이 끌어올린 문서는 코사인이 낮아도 위에 온다 — 실측으로 4위 0.751 ·
    5위 0.752 가 나온다. 낡은 단언이 살아 있었던 것은 `bge-m3.parquet` 이 없어 이 모듈이 통째로
    skip 되고 있었기 때문이다(RAG-045 ②와 같은 자리).

    ⚠️ **2026-08-28 에 계약이 늘었다** (RAG-040 인용 확장). `search()` 는 이제 top-k **뒤에**
    확장분을 덧붙이므로 길이가 k 보다 클 수 있다. `k` 의 뜻은 그대로 "검색 top-k" 이고,
    확장분은 `cited_by` 가 채워져 있어 앞의 k 개와 구분된다 — 그래서 여기서도 앞 k 개만 본다.
    확장분까지 순위 순서를 요구하면 안 된다: 그것은 순위 밖에서 인용을 따라 들어온 것이다.
    """
    with _ready_or_skip() as conn:
        hits = search.search(vector, k=5, conn=conn)
    top = [h for h in hits if h.cited_by is None]
    assert [h.rank for h in top] == [1, 2, 3, 4, 5]
    # RRF 는 내림차순. 동점이면 코사인이 tie-break 다 — SQL 의 ORDER BY 두 칸 그대로.
    for a, b in zip(top, top[1:]):
        assert (_rrf(a), a.score) >= (_rrf(b) - 1e-9, b.score - 1e-9), (
            f"{a.rank}위 rrf={_rrf(a):.6f} score={a.score:.3f} 뒤에 "
            f"{b.rank}위 rrf={_rrf(b):.6f} score={b.score:.3f} 가 왔다")
    assert all(-1.0 <= h.score <= 1.0 for h in hits)
    assert all(h.rank > 5 for h in hits if h.cited_by is not None)


def test_hits_carry_the_citation_fields(vector) -> None:
    """`citation`·`chunk_id` 가 비면 KPI(출처 링크 + 조항 인용)를 못 만든다."""
    with _ready_or_skip() as conn:
        hits = search.search(vector, k=5, conn=conn)
    assert all(h.chunk_id for h in hits)
    assert any(h.citation for h in hits)


def test_no_supplementary_actually_filters(vector) -> None:
    """플래그가 실제로 부칙을 뺀다. 안 빠지면 RAG-026 ① 의 비교 자체가 성립하지 않는다."""
    with _ready_or_skip() as conn:
        wide = search.search(vector, k=50, conn=conn)
        narrow = search.search(vector, k=50, include_supplementary=False, conn=conn)
    assert not any(h.part == "supplementary" for h in narrow)
    assert len(narrow) <= len(wide)


def test_category_filter(vector) -> None:
    """필터가 실제로 가르는가.

    길이가 아니라 **검색분(`cited_by is None`)의 수**를 센다 — 확장분은 인용을 따라 들어온 것이라
    카테고리 필터의 관심사가 아니다 (RAG-040).

    ⚠ **2026-09-06(RAG-065)에 이 테스트의 전제가 바뀌었다.** 그전까지는 코퍼스가 전부 `policy` 라
    `food` 가 **빈 결과**인 것을 고정하고 있었다. `F1` 이 그 칸을 채웠으므로(사료관리법 3법 +
    `nias-pet` 해설 3장) 이제 둘 다 결과가 있어야 하고, **서로 겹치지 않아야** 한다.
    빈 결과를 고정하던 자리가 이제 "두 칸이 실제로 갈리는가"를 고정한다.
    """
    with _ready_or_skip() as conn:
        policy = search.search(vector, k=5, category="policy", conn=conn)
        food = search.search(vector, k=5, category="food", conn=conn)

    kept = [h for h in policy if h.cited_by is None]
    assert len(kept) == 5
    assert food, "food 칸이 비었다 — F1(RAG-065)이 적재한 문서가 안 보인다"

    # 같은 질의인데 두 칸의 검색분이 겹치면 필터가 안 걸린 것이다.
    a = {h.chunk_id for h in policy if h.cited_by is None}
    b = {h.chunk_id for h in food if h.cited_by is None}
    assert not (a & b), f"두 카테고리가 같은 청크를 준다: {sorted(a & b)}"


# ---------------------------------------------------------------- 인용 확장 (RAG-040)
def test_refs_in_text_reads_the_three_notations() -> None:
    """세 표기를 다 읽는다 — 코퍼스에 실제로 있는 문장 그대로다.

    괄호(보조금24) · 낫표(해설 본문) · 맨몸(법령 본문). 하나라도 놓치면 다리가 끊긴다.
    """
    cases = {
        "부산광역시 영도구 광견병 예방접종 시술비 지원 법령: 수의사법(제30조) / 가축전염병 예방법(제15조)":
            [("수의사법", "제30조"), ("가축전염병 예방법", "제15조")],
        "인천광역시 광견병 예방접종비 지원 법령: 가축전염병 예방법 시행령(제13조, 제1항)":
            [("가축전염병 예방법 시행령", "제13조")],
        "「동물보호법」 제2조에 따라 등록해야 합니다": [("동물보호법", "제2조")],
        "경기도 의왕시 … 법령: 수의사법 시행규칙(제22조의14)": [("수의사법 시행규칙", "제22조의14")],
    }
    for text, want in cases.items():
        assert search.refs_in_text(text) == want, text


def test_refs_in_text_ignores_prose_without_a_law() -> None:
    """조문 번호가 없으면 아무것도 안 나온다.

    ⚠️ **이 테스트는 성능 회귀도 잡는다.** 처음 구현은 법령명을 한 정규식으로 잡았는데
    (`[가-힣]+(?:\\s*[가-힣]+)*?…법`), 법령명이 **없는** 긴 한글 문장에서 중첩 수량자가 폭주해
    사실상 정지했다. 지금은 조문 번호를 먼저 찾고 앞 40자만 되짚으므로 구간이 상수다.
    """
    long_prose = "광견병 예방접종 시술비 지원 사업 안내 " * 200
    assert search.refs_in_text(long_prose) == []


def test_expansion_pulls_the_cited_article() -> None:
    """보조금24 `근거법령` 청크가 가리키는 조문이 실제로 딸려 온다 (RAG-040).

    `Q6`("광견병 접종 의무인가요?")가 이 카드의 판정 문항이었다 — 코퍼스에 `광견병` 과 `의무` 를
    함께 말하는 청크는 없지만, 보조금24 청크가 *"법령: 가축전염병 예방법(제15조)"* 로 **어디에
    있는지**를 말한다. 검색은 그 다리를 이미 top-8 에 올려놓고도 건너지 않았다.

    **`vector` 픽스처를 안 쓴다.** 확장은 질의 벡터를 `ORDER BY` 에만 쓰므로 DB 에 있는 임베딩
    아무거나면 충분하고, 그러면 `*.parquet` 이 없는 워크트리에서도 이 검사가 실제로 돈다 —
    `data/` 가 비어 skip 되는 검사가 이미 많다.
    """
    with _ready_or_skip() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT metadata->>'chunk_id', metadata->>'citation', section,
                       document_title, content, embedding
                FROM documents
                WHERE content LIKE '%가축전염병 예방법(제15조)%' LIMIT 1
            """)
            row = cur.fetchone()
        if row is None:
            pytest.skip("보조금24 근거법령 청크가 코퍼스에 없다 — `#39` 가 적재한 것이다")
        cid, cit, section, title, content, emb = row
        hit = search.Hit(rank=1, score=0.5, chunk_id=cid, citation=cit or "", citation_url=None,
                         section=section, document_title=title or "", content=content,
                         part=None, dense_rank=1)
        assert ("가축전염병 예방법", "제15조") in search.cited_refs([hit])

        out = search.expand_citations([hit], search.Query(vector=emb), conn=conn)

    pulled = [h for h in out if h.cited_by == cid]
    assert any(h.document_title == "가축전염병 예방법" and h.section == "제15조" for h in pulled)
    assert out[0] is hit                      # 원래 순위는 손대지 않는다


def test_checkpoint3_finds_the_maengyeon_article(vector) -> None:
    """검문소③ 중 확인된 한 자리를 고정한다 — Q5 의 필수 `동물보호법 제18조` 가 top-5 에 있다.

    검문소③ 전체(7문항)를 단언하지 않는 이유는 그것이 **눈으로 보는 검사**이기 때문이다.
    다만 한 번 확인된 성공은 회귀로 잃지 않게 박아 둔다 — `tests/test_chunk.py` 가 검문소①의
    12청크를 단언한 것과 같은 처리다.
    """
    with _ready_or_skip() as conn:
        hits = search.search(vector, k=5, conn=conn)
    tiers = [search.tier_of(h.chunk_id, {"law-drf-api-animal-protection-act#제18조"}, set())
             for h in hits]
    assert "must" in tiers


# ------------------------------------------- 시행령의 맨몸 `법` 과 항 단위 조회 (RAG-058)
def test_parent_law_only_for_subordinate_titles() -> None:
    """모법은 **시행령·시행규칙에서만** 나온다 (RAG-058 ①).

    조례도 본문에서 `법` 을 쓰지만 **어느 법인지 제목이 말해 주지 않는다** — 조례 하나가
    여러 법을 인용한다. 여기서 넓히면 엉뚱한 법의 같은 번호를 근거로 싣게 된다.
    """
    assert search.parent_law("동물보호법 시행령") == "동물보호법"
    assert search.parent_law("가축전염병 예방법 시행규칙") == "가축전염병 예방법"
    assert search.parent_law("동물보호법") is None
    assert search.parent_law("서울특별시 동물보호 조례") is None
    assert search.parent_law(None) is None


def test_refs_read_the_hang_not_just_the_article() -> None:
    """조문을 **항까지** 읽는다 (RAG-058).

    DB 의 `section` 이 `제101조제4항` 이라 글자 그대로 맞아야 한다. 조까지만 읽으면
    `제101조` 로 조회해 다섯 항 어느 것도 못 찾는다.
    """
    got = search.refs_in_text("근거 법조문: 법 제101조제4항제4호", "동물보호법 시행령")
    assert got == [("동물보호법", "제101조제4항")]


def test_bare_law_resolves_even_behind_a_subject() -> None:
    """별표는 두 모양으로 쓴다 — `법 제101조…` 와 `소유자등이 법 제16조…`.

    **마지막 낱말이 `법`** 인지를 보므로 둘 다 잡힌다. 앞에 붙은 것은 문장의 주어이지
    법령명의 일부가 아니다.
    """
    text = "위반행위: 소유자등이 법 제16조제2항제1호에 따른 안전조치를 하지 않은 경우"
    assert search.refs_in_text(text, "동물보호법 시행령") == [("동물보호법", "제16조제2항")]


def test_bare_law_is_dropped_when_the_owner_is_not_subordinate() -> None:
    """소유 문서를 모르거나 시행령이 아니면 **맨몸 `법` 을 풀지 않는다.**

    지어내는 것보다 못 찾는 편이 낫다 (RAG-058 ①). 이 규칙이 `…이 법` 같은 산문도 같이
    걸러 준다 — 조례 본문에서 나오던 `란 법` · `소유자가 법` 이 그것이다.
    """
    text = "근거 법조문: 법 제101조제4항제4호"
    assert search.refs_in_text(text) == []
    assert search.refs_in_text(text, "서울특별시 동물보호 조례") == []
    assert search.refs_in_text("이 법 제5조에 따라", "동물보호법") == []


def test_full_law_names_are_untouched_by_the_bare_law_rule() -> None:
    """`가축전염병 예방법` 은 마지막 낱말이 `예방법` 이라 모법 치환에 안 걸린다.

    시행령 안에서도 다른 법을 이름으로 인용하는 자리가 있어서, 그것까지 모법으로 바꾸면
    엉뚱한 조문이 온다.
    """
    got = search.refs_in_text("「가축전염병 예방법」 제15조에 따라", "동물보호법 시행령")
    assert got == [("가축전염병 예방법", "제15조")]


def test_article_only_strips_the_hang() -> None:
    """조 폴백 키 (RAG-058 ②). 항이 없으면 그대로다."""
    assert search.article_only("제101조제4항") == "제101조"
    assert search.article_only("제52조의4제2항") == "제52조의4"
    assert search.article_only("제16조") == "제16조"


def test_cited_refs_skips_what_the_hits_already_have_by_article() -> None:
    """`제16조` 가 이미 히트에 있으면 `제16조제2항` 참조도 뺀다 (RAG-058 ②).

    안 빼면 항 조회가 빗나가고 **폴백이 같은 청크를 도로 데려와** `MAX_EXPANDED` 를 한 자리
    쓴다. `expand_citations` 가 마지막에 거르기는 하지만 그때는 이미 늦다.
    """
    have = search.Hit(rank=1, score=0.5, chunk_id="x#제16조", citation="", citation_url=None,
                      section="제16조", document_title="동물보호법", content="", part=None)
    citing = search.Hit(
        rank=2, score=0.4, chunk_id="y#별표", citation="", citation_url=None,
        section="별표 4", document_title="동물보호법 시행령", part=None,
        content="위반행위: 소유자등이 법 제16조제2항제1호에 따른 안전조치를 하지 않은 경우")
    assert search.cited_refs([have, citing]) == []


def test_expansion_falls_back_from_hang_to_article(vector) -> None:
    """항으로 못 찾으면 조로 찾는다 (RAG-058 ②).

    `동물보호법 제16조` 는 2,000자 미만이라 조 하나로 저장돼 있는데, 시행령 별표는 그것을
    `법 제16조제2항제1호` 로 가리킨다. 폴백이 없으면 이 참조는 영영 빗나간다.
    """
    citing = search.Hit(
        rank=1, score=0.4, chunk_id="probe#별표", citation="", citation_url=None,
        section="별표 4", document_title="동물보호법 시행령", part=None,
        content="위반행위: 소유자등이 법 제16조제2항제1호에 따른 안전조치를 하지 않은 경우")
    with _ready_or_skip() as conn:
        out = search.expand_citations([citing], vector, conn=conn)
    pulled = [h for h in out if h.cited_by == "probe#별표"]
    assert any(h.document_title == "동물보호법" and h.section == "제16조" for h in pulled), \
        "조 폴백이 `제16조` 를 데려오지 못했다"
