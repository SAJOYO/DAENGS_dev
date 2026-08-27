"""9단계 생성 — 검색 결과 위에 Gemini 로 답을 만든다 (RAG-028).

**조립 순서가 사는 곳이 여기다** (RAG-028 ③). `/ask` 와 `rag generate` 가 같은 순서
(`인코딩 → search → 프롬프트 → Gemini`)를 밟는데, 그것을 라우터와 CLI 에 각각 적으면 RAG-003 이
8·9단계 사이에 끼는 날 고칠 곳이 둘이 된다 — `pipeline.py` 가 이미 그 자리를 예고해 놨다.
RAG-027 은 이 조립을 `app/services/ask.py` 에 두라고 하지만, `rag` 는 `app` 을 import 할 수 없어
(RAG-014) **`rag generate --questions`(검문소④)가 그 코드를 못 쓴다.** 그러면 검문소가 서빙과
다른 코드를 검사하게 되고, 그것이 RAG-026 ②가 8단계에서 막은 상태 그대로다.

함수가 둘인 이유 —
- `answer(question, hits)` 는 **순수**하다. 검색 결과를 받으므로 DB 도 모델도 없이 테스트된다
- `ask(...)` 가 `search` 와 그것을 잇는다. **순서는 여기 한 번만 적힌다**

**프롬프트는 일부러 순진하다.** 검문소③이 이미 알려준 것은 "검증질문 7개 중 5개는 top-5 안에
정답 조항이 없다"이고, 1랩이 **새로** 알려주는 것은 그 상태에서 LLM 이 지어내는지 물러서는지다.
근거 부족 시 거부를 프롬프트로 부탁할지 구조로 막을지는 **한 랩 돌려본 뒤** 정한다(RAG-029).
지금 방어 장치를 넣으면 관찰하려던 것을 지운다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..core import config
from . import embed, goldenset, load, search
from .search import Hit

VERSION = 1

# 답변에 조항 번호를 요구한다 — KPI("출처 링크 + 조항 번호 인용") 자체이고, 요구하지 않으면
# 검문소④가 셀 것이 없어진다. 거부는 **부탁하지 않는다**(RAG-029 가 그 자리다).
PROMPT = """당신은 한국의 반려동물 관련 제도를 안내하는 도우미입니다.

아래 [참고자료]를 근거로 [질문]에 답하세요.
답변에는 근거가 된 **법령명과 조항 번호**를 함께 밝히고, 사용한 자료의 번호를 [1] 처럼 표시하세요.

[참고자료]
{context}

[질문] {question}
"""

_ITEM = "[{n}] {citation} — {title}{section}\n{content}"

# 답변에서 조항 번호를 뽑는 정규식. `제15조` · `제15조의2` 를 잡는다.
# **항·호까지 잡지 않는 이유** — 검색 단위(청크)가 조 단위라 항까지 대조하면 실재하는데도
# 없다고 세게 된다. 검문소④는 "지어냈는가"를 보는 것이지 인용의 정밀도를 보는 것이 아니다.
ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Answer:
    """답변 하나 + **그 답을 만든 근거 전부**.

    `hits` 를 들고 다니는 것이 RAG-028 ②다 — 1랩의 `/ask` 는 제품이 아니라 **검문소④가 읽는 관찰
    도구**이고, 무엇을 컨텍스트로 줬는지 함께 말하지 않으면 "인용한 조항이 실재했나"를 셀 수 없다.
    """
    question: str
    text: str
    hits: list[Hit]
    model: str                          # 실제로 답을 만든 Gemini 모델
    embedding_model: str                # 검색에 쓴 임베딩 모델 — 둘 다 있어야 랩 비교가 성립한다
    cited: list[str] = field(default_factory=list)        # 답변에 등장한 조항 번호 (등장 순)
    ungrounded: list[str] = field(default_factory=list)   # 그중 컨텍스트에 없는 것 = 검문소④

    @property
    def grounded(self) -> list[str]:
        return [a for a in self.cited if a not in self.ungrounded]


# ---------------------------------------------------------------- 프롬프트
def build_context(hits: list[Hit]) -> str:
    """참고자료 블록. **`content` 를 자르지 않는다** (RAG-028 ②).

    검문소③ B 가 찾은 상황 때문이다 — easylaw 해설 청크는 `citation` 이 해설 주소인데 **본문 안에
    「동물보호법」 제2조 같은 조항 번호가 박혀 있다.** 자르면 모델이 그걸 보고 인용했는지, 지어냈는지
    구분할 수 없게 된다.
    """
    return "\n\n".join(
        _ITEM.format(n=h.rank, citation=h.citation or h.chunk_id,
                     title=h.document_title,
                     section=f" · {h.section}" if h.section else "",
                     content=h.content)
        for h in hits
    )


def build_prompt(question: str, hits: list[Hit]) -> str:
    return PROMPT.format(context=build_context(hits), question=question)


# ---------------------------------------------------------------- 검문소④
def cited_articles(text: str) -> list[str]:
    """답변에 등장한 조항 번호 (중복 제거, 등장 순)."""
    seen: dict[str, None] = {}
    for m in ARTICLE_RE.findall(text):
        seen.setdefault(m, None)
    return list(seen)


def ungrounded_articles(text: str, hits: list[Hit]) -> list[str]:
    """답변이 든 조항 번호 중 **컨텍스트에 없는 것**. 검문소④가 세는 수 그대로다.

    ⚠️ **느슨한 검사다.** `제2조` 가 컨텍스트의 *다른 법령* 제2조로 맞아 버릴 수 있어 실제보다
    적게 나온다. 그래도 이 방향의 오차가 옳다 — 1 이상이면 그것은 **확실히** 지어낸 것이고,
    "0이면 프롬프트로 충분하다"는 판정은 RAG-029 에서 더 엄한 검사로 다시 본다.
    법령명까지 짝지어 대조하는 것이 KPI 에 맞지만, 그 정밀도는 관찰이 아니라 **방어 장치**의
    일이고 방어 장치는 1랩을 보고 나서 정한다.
    """
    haystack = "\n".join(f"{h.citation}\n{h.content}" for h in hits)
    return [a for a in cited_articles(text) if a not in haystack]


# ---------------------------------------------------------------- 생성
def _client(api_key: str | None = None):
    """지연 생성. 키가 없으면 여기서 죽는다 — 검색까지 다 해 놓고 마지막에 죽지 않게."""
    from google import genai

    key = api_key or config.settings.gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY 가 없다 — backend/.env 를 확인할 것")
    return genai.Client(api_key=key)


def answer(question: str, hits: list[Hit], *, client=None, model: str | None = None,
           embedding_model: str | None = None) -> Answer:
    """**순수하다** — 검색 결과를 받는다. DB 도 임베딩 모델도 안 만진다.

    나눠 둔 이유는 `search()` 가 `conn` 을 받게 한 것과 같다: 테스트가 손으로 만든 `Hit` 몇 개로
    프롬프트 조립과 검문소④ 산식을 붙잡을 수 있어야 하고, 그러려면 이 함수가 Gemini 말고는
    아무것도 필요로 하면 안 된다.
    """
    name = model or config.settings.gemini_model
    cli = client or _client()
    resp = cli.models.generate_content(model=name, contents=build_prompt(question, hits))
    text = (resp.text or "").strip()
    return Answer(
        question=question, text=text, hits=hits, model=name,
        embedding_model=embedding_model or config.settings.embedding_model_key,
        cited=cited_articles(text), ungrounded=ungrounded_articles(text, hits),
    )


def ask(question: str, *, k: int = search.DEFAULT_K, include_supplementary: bool = True,
        category: str | None = None, model_key: str | None = None,
        st=None, conn=None, client=None, model: str | None = None) -> Answer:
    """질문 하나 → 답 하나. **9단계의 순서가 이 세 줄이다.**

    `st`(임베딩 모델)·`conn`(DB)·`client`(Gemini) 셋 다 **받으면 만들지도 닫지도 않는다** — ①의
    규약이고 `search(conn=None)` 의 `own` 패턴 그대로다. CLI 는 7문항을 돌며 모델을 한 번만
    올리고, 서버는 lifespan 이 올린 것을 넘긴다.

    `include_supplementary` 기본이 `True` 인 것은 **엔진의 기본값**이지 서빙의 기본값이 아니다.
    서빙 정책은 `app/services/ask.py` 가 갖는다 — RAG-026 ①이 *"검사 도구와 서빙이 같은 기본값을
    쓸 이유가 없다"* 며 비워 둔 자리이고, 값은 1랩을 돌고 정한다.
    """
    key = model_key or config.settings.embedding_model_key
    if st is None:
        vector = search.encode(question, model_key=key)
    else:
        vector = embed.encode_query(embed.MODELS[key], question, st=st)

    hits = search.search(vector, k=k, include_supplementary=include_supplementary,
                         category=category, conn=conn)
    return answer(question, hits, client=client, model=model, embedding_model=key)


# ---------------------------------------------------------------- 덤프 (RAG-028 ⑥)
class DumpHeader(_Base):
    """랩 하나의 전제. **코퍼스 스냅샷이 여기 있어야 2랩 비교가 성립한다** (RAG-024 ④와 같은 규약)."""
    type: str = "header"
    version: int = VERSION
    lap: str
    generated_at: str
    gemini_model: str
    embedding_model: str
    documents: int                    # 코퍼스 스냅샷 — 이 숫자가 2랩에서 달라지는 것이 실험 그 자체다
    k: int
    questions: int


class DumpHit(_Base):
    rank: int
    score: float
    chunk_id: str
    logical: str                      # 수집 날짜를 뺀 주소 (RAG-028 ⑥ⓑ)
    citation: str
    tier: str                         # must / nice / -


class DumpRow(_Base):
    """문항 하나. **비교 축 셋이 전부 여기 있다** (RAG-028 ⑥ⓐ) — `hit_ids`·`cited`·`ungrounded`.

    `text` 도 남기지만 **비교 축이 아니다.** LLM 이 비결정적이라 답변 문장을 대조하면 랩 사이의
    차이가 코퍼스 때문인지 샘플링 때문인지 안 갈린다. 눈으로 읽으려고 남길 뿐이다.
    """
    type: str = "answer"
    id: str
    question: str
    text: str
    hits: list[DumpHit]
    cited: list[str]
    ungrounded: list[str]


def dump_rows(items: list[tuple[str, Answer, set[str], set[str]]]) -> list[DumpRow]:
    return [
        DumpRow(
            id=qid, question=a.question, text=a.text,
            hits=[DumpHit(rank=h.rank, score=round(h.score, 6), chunk_id=h.chunk_id,
                          logical=goldenset.logical(h.chunk_id), citation=h.citation,
                          tier=search.tier_of(h.chunk_id, must, nice))
                  for h in a.hits],
            cited=a.cited, ungrounded=a.ungrounded,
        )
        for qid, a, must, nice in items
    ]


def dump_header(lap: str, items: list[tuple[str, Answer, set[str], set[str]]], k: int,
                conn=None) -> DumpHeader:
    first = items[0][1]
    own = conn is None
    conn = conn or load.connect()
    try:
        n = load.count(conn)
    finally:
        if own:
            conn.close()
    return DumpHeader(
        lap=lap, generated_at=datetime.now(config.KST).isoformat(timespec="seconds"),
        gemini_model=first.model, embedding_model=first.embedding_model,
        documents=n, k=k, questions=len(items),
    )
