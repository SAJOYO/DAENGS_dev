"""`POST /ask` (RAG-028 · RAG-027 의 C 층).

**로직이 없다.** 질문을 받고, 서비스를 부르고, 그대로 돌려준다. 검색도 프롬프트도 판정도 여기
없다 — 그것이 RAG-027 의 유일한 강제 규칙이고, `controllers/walk.py` 가 세워 둔 본보기 그대로다.

`def` 인 것은 결정이다 (RAG-028 ④) — psycopg3 도 임베딩 인코딩도 sync 라 `async def` 안에 넣으면
이벤트 루프가 멈춘다. FastAPI 가 `def` 를 스레드풀에서 돌리는 것이 지금 이 코드에 맞는 모양이다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from daengs_life.app.deps import Encoder, get_conn, get_encoder
from daengs_life.app.dto.ask import AskIn, AskOut
from daengs_life.app.services import ask as service

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskOut,
             summary="제도·문서형 질의응답 — 답변 + 근거 (출처 링크·조항 번호)")
def post_ask(
    body: AskIn,
    encoder: Encoder = Depends(get_encoder),
    conn=Depends(get_conn),
) -> AskOut:
    """`{"question": "목줄 안 하면 과태료 얼마"}`.

    응답에 **근거를 통째로** 싣는다. 1랩의 이 엔드포인트는 제품이 아니라 검문소④가 읽는 관찰
    도구이고, 무엇을 컨텍스트로 줬는지 말하지 않으면 *"인용한 조항이 실재했나"* 를 셀 수 없다
    (RAG-028 ②). 프론트가 붙을 때 줄인다.
    """
    return service.ask(body.question, k=body.k, encoder=encoder, conn=conn)


__all__ = ["router"]
