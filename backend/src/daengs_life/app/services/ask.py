"""`POST /ask` 유스케이스 — 서빙 정책 + 에러 매핑 + 계약으로 옮기기 (RAG-027 의 service 층).

**조립은 여기 없다.** `검색 → 프롬프트 → Gemini` 순서는 `rag.stages.generate.ask()` 가 소유한다
(RAG-028 ③). 이유는 층 취향이 아니라 검문소다 — `rag` 는 `app` 을 import 할 수 없어서(RAG-014)
조립이 이 파일에 있으면 **`rag generate --questions`(검문소④)가 서빙과 다른 코드를 검사**하게 되고,
그것이 RAG-026 ②가 8단계에서 막은 상태 그대로다. 파트②가 `services/walk.py` 에 조립을 둔 것과
갈리는 것은 **그쪽 검문소가 전부 API 레벨이라 조립이 곧 서빙 코드**이기 때문이다.

`services/walk.py` 의 규칙을 그대로 물려받는다 — **생성은 한 줄도 여기 없다.** 프롬프트를 이 층이
손대기 시작하면 RAG-029(거부 전략)가 두 곳에 살게 된다.

그래서 여기 남는 셋은 **여기 말고 갈 데가 없는 것들**이다: 서빙 정책 기본값 · 에러 매핑 · DTO.
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from daengs_life.app.dto.ask import AskOut, HitOut
from daengs_life.rag.core import config
from daengs_life.rag.stages import generate
from daengs_life.rag.stages.search import Hit

# ---------------------------------------------------------------- 서빙 정책 (RAG-026 ①이 비워 둔 자리)
# **CLI 기본값과 갈라질 수 있는 자리다.** RAG-026 ①은 검문소③(사람이 눈으로 보는 자리)의 기본값만
# 정하면서 *"9단계 서빙의 기본값은 그때 따로 정한다"* 고 미뤄 뒀다.
SERVING_K = 5                       # 검문소③·RAG-024 ②의 판정 k 와 같은 수. 다르면 인상이 어긋난다
SERVING_SUPPLEMENTARY = True        # 부칙 포함. 1랩 실측에서 부칙을 빼도 결과가 안 바뀌었다(RAG-026 ①)


# ---------------------------------------------------------------- 에러 매핑에 쓰는 타임아웃 타입
# **한 타입으로 못 적는다.** google-genai 는 전송에 `httpx` 를 쓰는데 `_api_client` 가 `httpx2`
# 경로도 함께 갖고 있어서, 저것이 깔려 있으면 올라오는 예외가 저쪽 타입이 된다. 지금은 안 깔려
# 있지만 starlette 가 이미 `httpx2` 를 권하고 있어 어느 날 딸려 들어올 수 있고, 그때 조용히
# 502 로 새는 것보다 여기서 둘 다 잡는 편이 싸다.
_TIMEOUTS: tuple[type[BaseException], ...] = (httpx.TimeoutException,)
try:
    import httpx2                               # noqa: F401 — 있으면 타입을 하나 더 잡는다
except ImportError:
    pass
else:
    _TIMEOUTS += (httpx2.TimeoutException,)


def ask(question: str, *, k: int | None = None, encoder=None, conn=None, client=None) -> AskOut:
    """질문 하나 → 응답 하나.

    `encoder`·`conn`·`client` 는 **받아서 그대로 넘긴다** — 만들지도 닫지도 않는다(RAG-028 ①).
    수명을 아는 것은 이 층이 아니라 `deps.py` 와 lifespan 이다.
    """
    try:
        answer = generate.ask(
            question,
            k=k or SERVING_K,
            include_supplementary=SERVING_SUPPLEMENTARY,
            model_key=encoder.key if encoder else None,
            st=encoder.st if encoder else None,
            conn=conn,
            client=client,
        )
    except RuntimeError as e:
        # `_client()` 가 키 없음으로 죽는 경우 — 설정 문제지 요청 문제가 아니다
        raise HTTPException(status_code=503, detail=str(e)) from e
    except _TIMEOUTS as e:
        # **아래 502 보다 위여야 한다.** 순서가 뒤집히면 타임아웃이 502 에 먹혀,
        # "상류가 느린 것"과 "상류가 죽은 것"이 한 코드로 뭉개진다.
        # 검문소(그리고 나중의 게이트웨이)가 그 둘을 갈라 읽는다 — 메모 ⑪ 의 대가 셋 중 하나다.
        raise HTTPException(
            status_code=504,
            detail=f"Gemini 응답이 {config.settings.gemini_timeout_ms / 1000:g}초 안에 안 왔다",
        ) from e
    except Exception as e:
        # 검색(DB)이든 생성(Gemini)이든 상류가 죽은 것이다. 어느 쪽인지는 메시지로 남긴다 —
        # 502 로 뭉뚱그리면 "DB 가 죽었나 Gemini 가 죽었나"를 로그 없이는 못 가른다
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e

    if not answer.hits:
        # **근거가 0건이면 답을 만들지 않는다.** 컨텍스트가 빈 채로 Gemini 에 넘기면 그건 검색
        # 결과 위의 답이 아니라 모델의 기억이고, KPI(출처 링크 + 조항 번호)가 성립할 수 없다.
        # ⚠️ 이것은 RAG-029(근거가 *약할* 때의 거부 전략)가 아니다 — 근거가 **아예 없는** 경우다
        raise HTTPException(status_code=404, detail="근거를 찾지 못했다")

    return to_dto(answer)


def to_dto(answer: generate.Answer) -> AskOut:
    """도메인 → 계약. **이 함수가 `dto/` 가 존재하는 이유 그 자체다** (RAG-027)."""
    return AskOut(
        question=answer.question,
        answer=answer.text,
        hits=[_hit(h) for h in answer.hits],
        cited=answer.cited,
        ungrounded=answer.ungrounded,
        model=answer.model,
        embedding_model=answer.embedding_model,
    )


def _hit(h: Hit) -> HitOut:
    return HitOut(
        rank=h.rank, score=h.score, chunk_id=h.chunk_id, citation=h.citation,
        citation_url=h.citation_url, section=h.section, document_title=h.document_title,
        content=h.content, part=h.part,
    )


__all__ = ["ask", "to_dto", "SERVING_K", "SERVING_SUPPLEMENTARY"]
