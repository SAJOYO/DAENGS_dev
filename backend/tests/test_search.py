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
    """
    params = list(inspect.signature(search.search).parameters)
    assert params == ["query_vector", "k", "include_supplementary", "category", "conn"]


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
    assert len(items) == 12
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
    """질의 벡터 하나. 모델 로드가 무거워 모듈당 한 번만 만든다."""
    key = "bge-m3"
    if not embed.parquet_path(key).is_file():
        pytest.skip(f"{key}.parquet 이 없다")
    return search.encode("맹견 사육 허가 필요한가요?", model_key=key)


def test_returns_k_hits_ranked(vector) -> None:
    """순위는 1부터 k 까지, 점수는 내림차순."""
    with _ready_or_skip() as conn:
        hits = search.search(vector, k=5, conn=conn)
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5]
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))
    assert all(-1.0 <= h.score <= 1.0 for h in hits)


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
    """지금 코퍼스는 전부 policy 라 결과가 줄지 않아야 한다 — 필터가 오작동하면 여기서 걸린다."""
    with _ready_or_skip() as conn:
        assert len(search.search(vector, k=5, category="policy", conn=conn)) == 5
        assert search.search(vector, k=5, category="food", conn=conn) == []


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
