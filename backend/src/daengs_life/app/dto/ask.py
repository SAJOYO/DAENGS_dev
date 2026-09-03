"""`POST /life/ask` 의 공개 계약 (RAG-028 ② · RAG-027 의 V 층).

⚠️ **이 응답은 제품 계약이 아니라 콘솔 관측용이고, 전문을 유지한다** (RAG-028 ② · 로드맵 §5).
검문소④가 읽는 것 — *"LLM 이 인용한 조항 번호가 실제로 컨텍스트에 있었나"* — 를 보려면 응답이
**무엇을 컨텍스트로 줬는지 함께 말해야** 하고, 그래서 근거를 `content` 전문까지 통째로 싣는다.
제품이었다면 안 했을 선택이라 ADR 에 적어 뒀다.

**줄이는 자리는 여기가 아니라 어댑터다.** 사용자에게 닿는 길은 `POST /assistant/query` 하나이고,
거기서 `orchestration/adapters/life.py` 가 answer·citations·quality 로 축소한다 (O-9). 이 직접 API 의
유일한 소비자는 관리자 콘솔의 검색 점검 탭이라, 전문이 없으면 그 탭이 볼 것이 없어진다. 그래서
A4(#176)가 경로를 `/life/ask` 로 바꾸면서도 **응답은 그대로 뒀다.**

줄인다면 걷어낼 후보가 `score`·`chunk_id`·`rank`·`ungrounded` 다 — 특히 `score` 는 코사인 유사도지
신뢰도가 아닌데 사용자는 `0.62` 를 확신도로 읽는다
(검문소③ Q6 이 그 반례다: 광견병 질문에 **혈청검사 수수료**가 상위로 왔다).

도메인 타입(`rag.stages.search.Hit` · `rag.stages.generate.Answer`)을 그대로 내보내지 않는 이유는
`dto/walk.py` 와 같다 — RAG-003(하이브리드·리랭커)이 `Hit` 에 필드를 더하는 것은 도메인의 자유여야
하고, 그 자유가 클라이언트로 새면 안 된다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HitOut(BaseModel):
    """근거 청크 하나. `Hit` 의 필드를 지금은 **전부** 옮긴다 (RAG-028 ②)."""

    rank: int
    score: float                      # 코사인 유사도. **신뢰도가 아니다** — 위 주석 참조
    chunk_id: str                     # 골든셋 라벨과 같은 주소라 눈으로 대조된다 (RAG-022 ⑥B)
    citation: str                     # KPI 의 절반 — "조항 번호 인용"
    citation_url: str | None = None   # KPI 의 나머지 절반 — "출처 링크"
    section: str | None = None
    document_title: str
    content: str                      # 자르지 않는다 (RAG-028 ②)
    part: str | None = None           # "supplementary" = 부칙


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    # `None` 은 "서빙 기본값을 쓴다"는 뜻이다. 기본값 자체는 여기가 아니라 `services/ask.py` 가
    # 갖는다 — RAG-026 ①이 *"검사 도구와 서빙이 같은 기본값을 쓸 이유가 없다"* 며 비워 둔 자리다.
    k: int | None = Field(default=None, ge=1, le=20)

    # 반려견 프로필 (로드맵 B4). **여기서는 손으로 넣는 값이다.** 사용자 경로에서는 앱이
    # `active_dog_id` 만 보내고 `daengs_backend` 가 pets 를 읽어 채운다 — 이 직접 API 는 pets 를
    # 모르고, 알아야 할 이유도 없다(파트① 은 Dog Profile 과 독립이다). 콘솔 점검 탭이 프로필이
    # 붙은 답과 안 붙은 답을 나란히 보려면 이 자리가 필요하다.
    breed: str | None = Field(default=None, max_length=60)
    age_months: int | None = Field(default=None, ge=0, le=360)


class AskOut(BaseModel):
    question: str
    answer: str
    hits: list[HitOut]
    cited: list[str]                  # 답변에 등장한 조항 번호
    ungrounded: list[str]             # 그중 컨텍스트에 없는 것 = 검문소④가 세는 수
    model: str                        # 답을 만든 Gemini 모델
    embedding_model: str              # 검색에 쓴 임베딩 모델 — 둘 다 있어야 랩 비교가 성립한다


__all__ = ["AskIn", "AskOut", "HitOut"]
