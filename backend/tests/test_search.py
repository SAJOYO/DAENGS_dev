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

    2026-08-27 에 조례 3문항(S1~S3)이, 08-28 에 보조금24 2문항(S4·S5)이 붙어 7 → 12 가 됐다.
    **이 수를 갱신하는 것 자체가 이 테스트의 일이다** — `--questions` 가 도는 범위라
    문항이 늘거나 줄면 검문소③④의 분모가 말없이 바뀐다.
    """
    items = search.hand_questions()
    gs = goldenset.load()
    assert [i[0] for i in items] == [i.id for i in gs.items if i.origin == "hand"]
    assert len(items) == 15
    assert all(q for _, q, _, _ in items)


def test_every_hand_question_has_a_must_label() -> None:
    """정답 없는 문항이 섞이면 검문소③의 ★ 표시가 의미를 잃는다."""
    assert all(must for _, _, must, _ in search.hand_questions())


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

    **모델은 `config` 에서 받는다 — 여기에 박지 않는다** (RAG-044). 질의 모델이 적재 모델과
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

    ⚠️ **점수(코사인) 내림차순이 아니다** (RAG-044 에서 고쳤다). 예전에는 그렇게 단언했고
    순수 dense 였던 시절(RAG-026)에는 맞았는데, RAG-035 가 하이브리드로 바꾸면서
    `ORDER BY f.rrf DESC, embedding <=> q` 가 됐다. **정렬 키는 RRF 이고 `score` 칸은 여전히
    코사인**이라, 형태소 축이 끌어올린 문서는 코사인이 낮아도 위에 온다 — 실측으로 4위 0.751 ·
    5위 0.752 가 나온다. 낡은 단언이 살아 있었던 것은 `bge-m3.parquet` 이 없어 이 모듈이 통째로
    skip 되고 있었기 때문이다(RAG-044 ②와 같은 자리).

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
    """지금 코퍼스는 전부 policy 라 결과가 줄지 않아야 한다 — 필터가 오작동하면 여기서 걸린다.

    길이가 아니라 **검색분(`cited_by is None`)의 수**를 센다 — 확장분은 인용을 따라 들어온 것이라
    카테고리 필터의 관심사가 아니다 (RAG-040).
    """
    with _ready_or_skip() as conn:
        hits = search.search(vector, k=5, category="policy", conn=conn)
        assert len([h for h in hits if h.cited_by is None]) == 5
        assert search.search(vector, k=5, category="food", conn=conn) == []


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
