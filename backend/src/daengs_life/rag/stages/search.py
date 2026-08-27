"""8단계 검색 — `documents` 에서 dense 로 top-k 를 꺼낸다 (RAG-026).

**9단계(Gemini)를 붙이기 전에 검색만 따로 떼어 둔다.** 답변이 이상할 때 검색이 틀렸는지 LLM 이
틀렸는지 가르려면 검색이 단독으로 관찰 가능해야 한다 — RAG-024 `판정 이후` 가 기준선으로 관통하기로
한 것과 같은 판단이고, 원인을 하나로 두려는 것이다.

**여기가 검색의 유일한 구현이다** (RAG-026 ②). CLI 는 이 결과를 찍기만 하고, 9단계와 FastAPI 도
같은 함수를 부른다. 경계를 안 지키면 9단계가 검색을 다시 짜게 되고, 그 순간 **검문소③이 확인한
것과 서빙이 실제로 하는 것이 달라진다** — 검문소③의 근거가 조용히 무효가 되는 자리다.

RAG-003(하이브리드·리랭커)이 들어와도 **바뀌는 것은 이 파일의 내부이지 시그니처가 아니다.**
호출하는 쪽이 검색 방식을 알면 그 교체가 세 곳을 고치는 일이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core import config
from . import embed, load

VERSION = 1

# 검문소③이 보는 수이자 RAG-024 ② 의 판정 k. **같은 숫자여야 한다** — 평가에서 쓴 k 와 눈으로 보는
# k 가 다르면 6단계 점수와 8단계 인상이 어긋나도 원인을 못 짚는다
DEFAULT_K = 5


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


# `<=>` 는 코사인 거리다. `db/indexes.sql` 이 `vector_cosine_ops` 로 인덱스를 만들었으므로
# 다른 연산자(`<->`, `<#>`)를 쓰면 인덱스를 타지 않는다 — 연산자를 여기 한 곳에만 적는 이유다.
_SQL = """
SELECT 1 - (embedding <=> %(q)s) AS score,
       metadata->>'chunk_id', metadata->>'citation', metadata->>'citation_url',
       section, document_title, content, metadata->>'part'
FROM documents
WHERE embedding IS NOT NULL
  {filters}
ORDER BY embedding <=> %(q)s
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
        return embed.encode_query(model, query, st=st)
    finally:
        del st
        embed.release()


def search(query_vector, *, k: int = DEFAULT_K, include_supplementary: bool = True,
           category: str | None = None, conn=None) -> list[Hit]:
    """벡터 하나로 top-k. **DB 만 만진다** — 인코딩은 caller 가 한다.

    나눠 둔 이유는 `--questions` 가 질의 7개를 도는데 모델을 7번 올렸다 내릴 이유가 없어서다.
    (`parse_doc` 이 쓰기를 분리한 것과 같은 모양이다.)

    **`include_supplementary` 기본이 `True` 인 것은 결정이다** (RAG-026 ①). 부칙은 세 모델 모두에서
    정답이었던 적이 없지만, **검문소③은 사람이 눈으로 보는 검사 자리라 기본값이 이미 걸러진
    결과면 무엇이 걸러졌는지를 볼 수 없다.** 서빙(9단계)의 기본값은 여기서 정하지 않는다 —
    검사 도구와 서빙이 같은 기본값을 쓸 이유가 없다.
    """
    filters = []
    params: dict[str, Any] = {"q": query_vector, "k": k}
    if not include_supplementary:
        # part 는 metadata 안에 있고, 부칙이 아닌 청크는 아예 키가 없다 (exclude_none 으로 쓴다)
        filters.append("AND metadata->>'part' IS DISTINCT FROM 'supplementary'")
    if category:
        filters.append("AND category = %(category)s")
        params["category"] = category

    own = conn is None
    conn = conn or load.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL.format(filters="\n  ".join(filters)), params)
            return [
                Hit(rank=i + 1, score=float(score), chunk_id=cid, citation=cit or "",
                    citation_url=url, section=section, document_title=title or "",
                    content=content, part=part)
                for i, (score, cid, cit, url, section, title, content, part)
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
