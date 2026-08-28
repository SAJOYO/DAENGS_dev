"""8단계 검색 — `documents` 에서 dense 로 top-k 를 꺼낸다 (RAG-026).

**9단계(Gemini)를 붙이기 전에 검색만 따로 떼어 둔다.** 답변이 이상할 때 검색이 틀렸는지 LLM 이
틀렸는지 가르려면 검색이 단독으로 관찰 가능해야 한다 — RAG-024 `판정 이후` 가 기준선으로 관통하기로
한 것과 같은 판단이고, 원인을 하나로 두려는 것이다.

**여기가 검색의 유일한 구현이다** (RAG-026 ②). CLI 는 이 결과를 찍기만 하고, 9단계와 FastAPI 도
같은 함수를 부른다. 경계를 안 지키면 9단계가 검색을 다시 짜게 되고, 그 순간 **검문소③이 확인한
것과 서빙이 실제로 하는 것이 달라진다** — 검문소③의 근거가 조용히 무효가 되는 자리다.

RAG-003(하이브리드·리랭커)이 들어와도 **바뀌는 것은 이 파일의 내부이지 시그니처가 아니다.**
호출하는 쪽이 검색 방식을 알면 그 교체가 세 곳을 고치는 일이 된다.

**하이브리드가 들어왔다 (RAG-035).** dense 와 렉시컬(형태소 FTS)을 각각 뽑아 RRF 로 섞는다.
그러면서 `search()` 의 첫 인자가 벡터에서 `Query` 로 바뀌었다 — 렉시컬은 벡터로 못 하고
**질의 텍스트가 있어야** 하기 때문이다. 위 문단의 규약을 어긴 것으로 보일 수 있으나,
규약의 뜻은 "호출부가 검색 방식을 알면 안 된다" 였고 그것은 지켜진다: 호출부는 여전히
`search(encode(질문))` 이고 `encode()` 가 벡터와 토큰을 함께 만들어 준다. **인자 이름이
바뀐 것을 결정으로 남기는 이유**는 `test_search.py` 가 그 목록을 단언하고 있어서다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core import config, tokenize
from . import embed, load

VERSION = 2          # 1 = dense 단독 / 2 = 하이브리드 (RAG-035)

# 검문소③이 보는 수이자 RAG-024 ② 의 판정 k. **같은 숫자여야 한다** — 평가에서 쓴 k 와 눈으로 보는
# k 가 다르면 6단계 점수와 8단계 인상이 어긋나도 원인을 못 짚는다
DEFAULT_K = 5

# 각 축에서 뽑는 후보 수. RRF 는 순위만 쓰므로 이 수가 곧 "몇 등까지 섞을지"다.
# 100 인 이유 — 프로토타입에서 정답이 dense 39위·렉시컬 상위권인 경우가 실익의 대부분이었다.
# 너무 크면 하위권 잡음이 RRF 점수에 섞이고, 너무 작으면 한쪽에만 잡힌 문서를 놓친다.
CANDIDATE_N = 100

# RRF 상수. RAG-003 제안값 그대로다.
# **점수가 아니라 순위만 쓰기 때문에** dense 의 코사인과 렉시컬의 ts_rank 가 스케일이 달라도
# 정규화가 필요 없다.
RRF_K = 60

# 렉시컬 축의 가중치. **1.0 이 아니다.**
#
# 균등 가중이면 "한 축에서 1위"가 너무 세다. 실측(2026-08-28) — Q5 의 정답 조문은
# dense 2위인데 렉시컬 49위였고, dense 15위·렉시컬 1위인 문서에 밀려 top-5 밖으로
# 나갔다. `ts_rank` 는 IDF 를 안 쓰므로 흔한 단어가 많은 짧은 청크가 쉽게 1위가 된다 —
# 그 잡음이 좋은 dense 신호를 이기면 안 된다.
#
# 골든셋 12문항 전수 스윕 결과(필수 재현율, dense 단독 = 6):
#     0.05  0.10 │ 0.15  0.20  0.30  0.35  0.45 │ 0.50  0.75  1.00
#        7     7 │    8     8     8     8     8 │    7     7     7
# **0.15~0.45 가 평평하고 그 구간에서 회귀가 없다.** 가운데인 0.3 을 잡았다 —
# 양끝은 한쪽이 무너지는 자리라 여유를 두는 편이 낫다.
#
# ⚠️ 이것은 **골든셋에 맞춘 상수**다. 문항이 늘거나 코퍼스 성격이 바뀌면 다시 재야 한다.
# RRF 자체는 재튜닝이 필요 없지만 이 가중치는 아니다 — 그 구분을 흐리지 말 것.
# 다시 잴 때는 `scratchpad` 가 아니라 이 표를 만든 방식(두 축의 순위를 뽑아 파이썬에서
# 가중 합)으로 스윕하면 된다. DB 왕복 없이 12문항이 몇 초다.
LEXICAL_WEIGHT = 0.3


@dataclass(frozen=True)
class Hit:
    """검색 결과 한 줄. `chunk_id` 를 항상 들고 다닌다 — 골든셋 라벨과 **같은 주소**라
    "이게 정답 청크인가"를 눈으로 대조할 수 있다 (RAG-022 ⑥B 의 논리 주소)."""
    rank: int
    score: float                  # 코사인 유사도 (1 - 거리). 벡터가 L2 정규화라 그대로 코사인이다
    chunk_id: str
    citation: str                 # 답변에 실을 인용 문자열 — KPI 그 자체
    citation_url: str | None
    section: str | None
    document_title: str
    content: str
    part: str | None              # "supplementary" = 부칙 (RAG-021 ①)
    # 어느 축이 이 문서를 끌어올렸는지. 검문소③에서 **왜 이게 올라왔는지**를 눈으로 가른다.
    # None = 그 축의 후보에 없었다는 뜻이다.
    dense_rank: int | None = None
    lexical_rank: int | None = None


@dataclass(frozen=True)
class Query:
    """검색 입력. **벡터와 토큰을 함께 들고 다닌다.**

    둘을 따로 넘기게 두면 한쪽만 넘기는 호출이 생기고, 그때 렉시컬 축이 조용히 빠진다 —
    결과가 그럴듯하게 나와서 알아채기 어렵다. 한 덩어리로 묶어 그 실수를 못 하게 한다.
    """
    vector: Any
    tsquery: str = ""             # `to_tsquery('simple', …)` 에 넣을 OR 식. 빈 값이면 dense 단독
    text: str = ""                # 원문. 로그·디버그용


# `<=>` 는 코사인 거리다. `db/indexes.sql` 이 `vector_cosine_ops` 로 인덱스를 만들었으므로
# 다른 연산자(`<->`, `<#>`)를 쓰면 인덱스를 타지 않는다 — 연산자를 여기 한 곳에만 적는 이유다.
#
# 하이브리드 (RAG-035) — 두 축을 각각 `CANDIDATE_N` 까지 뽑아 RRF 로 섞는다.
#   dense : 코사인 순위
#   lex   : `ts_rank(content_tsv, …)` 순위. 형태소 토큰이 `content_tokens` 에 들어 있다
#
# **`score` 는 여전히 코사인이다.** 순서는 RRF 가 정하지만 점수 칸의 뜻을 바꾸지 않았다 —
# 바꾸면 검문소③을 눈으로 볼 때 예전 랩과 비교가 안 되고, 0.02 같은 RRF 값은 사람이
# 읽어도 아무 정보가 없다. 대신 `dense_rank`·`lexical_rank` 로 **왜 올라왔는지**를 보인다.
# 그래서 점수가 순위와 단조롭지 않을 수 있는데, 그 불일치 자체가 "렉시컬이 끌어올렸다"는 신호다.
#
# 동점 처리 — `ts_rank` 는 동점이 흔해서 `row_number()` 만으로는 순위가 실행마다 흔들린다.
# `d.id` 를 2차 정렬키로 박아 **같은 코퍼스면 같은 순위**가 나오게 한다.
_SQL = """
WITH dense AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s) AS rn
    FROM documents
    WHERE embedding IS NOT NULL
      {filters}
    ORDER BY embedding <=> %(q)s
    LIMIT %(n)s
), lex AS (
    -- tsquery 가 비면(%(tsq)s = '') 이 CTE 는 0행이고 결과는 dense 단독이 된다.
    SELECT d.id,
           row_number() OVER (ORDER BY ts_rank(d.content_tsv, q.q) DESC, d.id) AS rn
    FROM documents d, to_tsquery('simple', NULLIF(%(tsq)s, '')) AS q(q)
    WHERE d.content_tsv @@ q.q
      {lex_filters}
    ORDER BY ts_rank(d.content_tsv, q.q) DESC, d.id
    LIMIT %(n)s
), fused AS (
    SELECT COALESCE(dn.id, lx.id) AS id,
           COALESCE(1.0 / (%(rrf)s + dn.rn), 0)
             + COALESCE(%(wlex)s / (%(rrf)s + lx.rn), 0) AS rrf,
           dn.rn AS drn, lx.rn AS lrn
    FROM dense dn FULL OUTER JOIN lex lx ON dn.id = lx.id
)
SELECT 1 - (doc.embedding <=> %(q)s) AS score,
       doc.metadata->>'chunk_id', doc.metadata->>'citation', doc.metadata->>'citation_url',
       doc.section, doc.document_title, doc.content, doc.metadata->>'part',
       f.drn, f.lrn
FROM fused f
JOIN documents doc ON doc.id = f.id
ORDER BY f.rrf DESC, doc.embedding <=> %(q)s
LIMIT %(k)s
"""


def encode(query: str, model_key: str | None = None):
    """질의 → 벡터. **모델을 올렸다 내린다.**

    `encode_query` 를 쓰는 것이 계약이다 — Qwen3 만 질의에 공식 지시문을 붙이는 비대칭 모델이라
    (4단계 실측) 문서 경로로 넣으면 그 모델을 자기 설계와 다르게 쓰게 된다. 지금 기본값은
    승자가 아니라 기준선 `bge-m3` 이고, 그것이 RAG-024 `판정 이후` 의 결정이다.
    """
    key = model_key or config.settings.embedding_model_key
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        vector = embed.encode_query(model, query, st=st)
    finally:
        del st
        embed.release()
    return make_query(query, vector)


def make_query(text: str, vector) -> Query:
    """벡터를 이미 만들어 둔 caller 용 (CLI 가 질의 여러 개를 한 번에 인코딩한다).

    **토큰화는 여기 한 곳에서만 한다** — 문서 쪽(`stages/load.py`)과 같은 `core.tokenize` 를
    부른다. 둘이 갈라지면 매칭이 그냥 안 되는데 dense 가 결과를 채워 줘서 안 보인다.
    """
    return Query(vector=vector, tsquery=tokenize.tsquery(text), text=text)


def search(query: Query, *, k: int = DEFAULT_K, include_supplementary: bool = True,
           category: str | None = None, conn=None) -> list[Hit]:
    """`Query` 하나로 top-k. **DB 만 만진다** — 인코딩·토큰화는 caller 가 한다.

    나눠 둔 이유는 `--questions` 가 질의 7개를 도는데 모델을 7번 올렸다 내릴 이유가 없어서다.
    (`parse_doc` 이 쓰기를 분리한 것과 같은 모양이다.)

    **`include_supplementary` 기본이 `True` 인 것은 결정이다** (RAG-026 ①). 부칙은 세 모델 모두에서
    정답이었던 적이 없지만, **검문소③은 사람이 눈으로 보는 검사 자리라 기본값이 이미 걸러진
    결과면 무엇이 걸러졌는지를 볼 수 없다.** 서빙(9단계)의 기본값은 여기서 정하지 않는다 —
    검사 도구와 서빙이 같은 기본값을 쓸 이유가 없다.
    """
    filters, lex_filters = [], []
    params: dict[str, Any] = {"q": query.vector, "k": k, "n": CANDIDATE_N,
                              "rrf": RRF_K, "wlex": LEXICAL_WEIGHT, "tsq": query.tsquery}
    if not include_supplementary:
        # part 는 metadata 안에 있고, 부칙이 아닌 청크는 아예 키가 없다 (exclude_none 으로 쓴다)
        filters.append("AND metadata->>'part' IS DISTINCT FROM 'supplementary'")
        lex_filters.append("AND d.metadata->>'part' IS DISTINCT FROM 'supplementary'")
    if category:
        filters.append("AND category = %(category)s")
        lex_filters.append("AND d.category = %(category)s")
        params["category"] = category

    own = conn is None
    conn = conn or load.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL.format(filters="\n      ".join(filters),
                                    lex_filters="\n      ".join(lex_filters)), params)
            return [
                Hit(rank=i + 1, score=float(score), chunk_id=cid, citation=cit or "",
                    citation_url=url, section=section, document_title=title or "",
                    content=content, part=part, dense_rank=drn, lexical_rank=lrn)
                for i, (score, cid, cit, url, section, title, content, part, drn, lrn)
                in enumerate(cur.fetchall())
            ]
    finally:
        if own:
            conn.close()


def hand_questions() -> list[tuple[str, str, set[str], set[str]]]:
    """검증질문 1~7 → (id, 질문, must 라벨, nice 라벨).

    **질문을 코드에 박지 않고 `goldenset.yaml` 에서 읽는다** (RAG-026 ②). 박으면 질문 목록의 단일
    소스가 둘이 되고, RAG-022 가 골든셋을 git 에 넣은 이유(판단 기록의 단일 소스)가 반쯤 무너진다.
    덤으로 **정답 라벨을 알고 있으므로** 결과에 `must`/`nice` 를 표시할 수 있어, 검문소③이
    "이게 정답인가"를 손으로 세지 않아도 된다.
    """
    from . import goldenset

    gs = goldenset.load()
    return [(i.id, i.question, set(i.must), set(i.nice))
            for i in gs.items if i.origin == "hand"]


def tier_of(chunk_id: str, must: set[str], nice: set[str]) -> str:
    """검색 결과의 `chunk_id` 가 정답 라벨인가. 라벨은 **수집 날짜를 뺀 논리 주소**다 (RAG-022 ⑥B)."""
    from . import goldenset

    logical = goldenset.logical(chunk_id)
    return "must" if logical in must else "nice" if logical in nice else "-"
