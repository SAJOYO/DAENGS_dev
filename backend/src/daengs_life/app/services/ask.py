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
from daengs_life.rag.stages import generate, score
from daengs_life.rag.stages.search import Hit

# ---------------------------------------------------------------- 서빙 정책 (RAG-026 ①이 비워 둔 자리)
# **CLI 기본값과 갈라질 수 있는 자리다.** RAG-026 ①은 검문소③(사람이 눈으로 보는 자리)의 기본값만
# 정하면서 *"9단계 서빙의 기본값은 그때 따로 정한다"* 고 미뤄 뒀다.
SERVING_K = 5                       # 검문소③·RAG-024 ②의 판정 k 와 같은 수. 다르면 인상이 어긋난다
SERVING_SUPPLEMENTARY = True        # 부칙 포함. 1랩 실측에서 부칙을 빼도 결과가 안 바뀌었다(RAG-026 ①)

# 약한 근거에서 기권할지 정하는 정책 (RAG-055). **이름으로 고른다** — 실물은
# `rag.stages.score.ABSTAIN_POLICIES` 에 있고 `score-laps` 가 랩을 그 함수들로 채점한다.
# 서빙이 자기 판정을 따로 적으면 **검문소가 재는 것과 서빙이 하는 것이 갈린다** — RAG-026 ②가
# 8단계에서, RAG-028 ③이 9단계에서 막은 것과 같은 병리다. 여기 있는 것은 이름 하나뿐이다.
#
# `covered+selfreport` 를 고른 근거는 lap16·lap17 이다. 모델이 스스로 낸 `covered` 가 주력이고
# — 문장을 읽는 후보가 전부 놓치던 B2(조용한 재해석)를 잡는 유일한 신호다 — 자기보고는
# `covered` 가 한 랩에서 놓친 자리를 메우는 보조다. 두 랩 모두 이 조합만 놓친 기권 0 이었다.
#
# ⚠️ **오기권 셋(Q3 · S3 · B1)은 이 정책의 과잉이 아니다.** 셋 다 정답 청크가 DB 에 있는데
# top-5 에 안 오는 자리라, 지금 서빙은 그 질문에 **무관한 조항을 나열한 틀린 답**을 낸다.
# 기권이 그보다 낫다. 순위를 고치는 것은 검색 쪽 일이다 (A0 §3-1 이 남긴 D5).
SERVING_ABSTAIN_POLICY = "covered+selfreport"


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


def ask(question: str, *, k: int | None = None, encoder=None, conn=None, client=None,
        breed: str | None = None, age_months: int | None = None,
        screening_verdict: str | None = None,
        screening_days_ago: int | None = None,
        screening_history: tuple[tuple[str, int], ...] = ()) -> AskOut:
    """질문 하나 → 응답 하나.

    `encoder`·`conn`·`client` 는 **받아서 그대로 넘긴다** — 만들지도 닫지도 않는다(RAG-028 ①).
    수명을 아는 것은 이 층이 아니라 `deps.py` 와 lifespan 이다.

    `breed`·`age_months` 는 로드맵 B4 다. **원시값으로 받는다** — 부르는 쪽(어댑터)의 타입을
    여기서 알면 `daengs_life` 가 오케스트레이션을 의존하게 된다. 둘 다 `None` 이면 프롬프트가
    B4 이전과 한 글자도 다르지 않고, 그래서 프로필 없는 요청의 답은 그대로다.

    `screening_verdict`·`screening_days_ago` 는 #283 이고 규칙이 같다 — 원시값, 둘 다 `None`
    이면 프롬프트가 한 글자도 안 바뀐다. **판정 기록에서 이어 온 질문에만 채워진다.**
    병명·확률·통제 문구가 여기 없는 것은 빠뜨린 것이 아니라 상류 계약이 안 싣기 때문이다
    (D-023, `orchestration/contracts.py` 의 `ScreeningContext`).

    `screening_history` 는 #79 3번이고 같은 아이의 **이전** 판정들이다 — `(판정, 경과일)`
    쌍의 튜플이라 여기도 원시값이고, 비어 있으면 이력 블록이 아예 안 붙는다. 위 두 값과
    **따로 온다**: 첫 기록은 이력이 없고, 이번 판정이 실패한 자리에는 이력만 있다.
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
            dog=generate.DogProfile(breed=breed, age_months=age_months),
            screening=generate.ScreeningNote(
                verdict=screening_verdict,
                days_ago=screening_days_ago,
                history=tuple(screening_history),
            ),
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

    # **거절이 기권보다 앞이다** (RAG-055). 응급 질문에 근거가 0건이면 둘 다 성립하는데,
    # 그때 사용자에게 필요한 것은 "자료에 없다"가 아니라 "지금 병원에"다
    if answer.boundary != "none":
        # 422 인 것은 **요청이 잘못돼서가 아니라 답할 수 없는 요청이어서**다. 4xx 중 이 뜻에
        # 가장 가깝고, 상류가 죽은 5xx 와 갈라야 어댑터가 REFUSED 와 ERROR 를 안 뭉갠다.
        # `message` 는 생성이 만든 문장 그대로다 — 여기서 고정 문구를 끼우면 어댑터가 지킬
        # 무손실(불변식 3)이 이미 여기서 깨진다.
        # **근거도 같이 간다** (RAG-077). 경계 답변이 `[1][3]` 을 달고 오는데 본문만 보내면
        # 그 번호가 가리킬 곳이 사라진다 — #318 실측의 거절 15건 중 14건이 그랬다
        raise HTTPException(status_code=422, detail=refusal_detail(answer))

    if not answer.hits:
        # **근거가 0건이면 답을 만들지 않는다.** 컨텍스트가 빈 채로 Gemini 에 넘기면 그건 검색
        # 결과 위의 답이 아니라 모델의 기억이고, KPI(출처 링크 + 조항 번호)가 성립할 수 없다.
        # 하이브리드 검색이 늘 상위 k 를 돌려주므로 **이 자리는 빈 코퍼스에서나 난다** (A0 §3-2)
        raise HTTPException(status_code=404, detail="근거를 찾지 못했다")

    if score.ABSTAIN_POLICIES[SERVING_ABSTAIN_POLICY](_as_row(answer)):
        # **근거는 있는데 물은 것에 못 닿는 경우.** RAG-029 가 미뤄 두고 D-035 가 v1 한계로
        # 수용한 자리이고, A0 이 실물로 둘 남겨 이 카드가 열렸다. 코드는 404 그대로다 —
        # 어댑터가 이미 ABSTAINED 로 옮기고 있고, 기권의 종류가 늘어난 것이지 뜻이 바뀐 게 아니다
        raise HTTPException(status_code=404, detail={
            "code": "no_evidence", "message": answer.text})

    return to_dto(answer)


def _as_row(answer: generate.Answer) -> dict:
    """`Answer` → 정책이 보는 모양. **저장된 랩의 행과 같은 칸 이름**이라야 한다 (RAG-055).

    `score.ABSTAIN_POLICIES` 는 소급 채점용으로 dict 를 받게 돼 있고, 서빙이 그 함수를 그대로
    쓰는 것이 이 파일이 자기 판정을 따로 적지 않는 방법이다. 칸 이름이 어긋나면 정책이 조용히
    기본값(`covered=True`)을 읽어 **기권이 영영 안 난다** — 그래서 테스트가 이 함수를 붙잡는다.
    """
    return {"covered": answer.covered, "cited": answer.cited, "text": answer.text,
            "hits": [{"score": h.score} for h in answer.hits]}


def refusal_detail(answer: generate.Answer) -> dict:
    """경계 거절의 422 `detail` (RAG-055 · RAG-077).

    `code`·`message` 는 RAG-055 그대로이고, `hits`·`cited`·`ungrounded` 가 RAG-077 이다 — 모양은
    200 응답(`AskOut`)의 같은 칸과 **같다.** 어댑터가 OK 를 줄이는 코드로 REFUSED 도 줄이라고
    같은 모양을 쓴다.

    **`hits` 를 답변이 지목한 것만으로 추리지 않는다.** `[3]` 은 컨텍스트의 세 번째 자리라는
    뜻이라, 목록에서 빠지는 것이 있으면 번호가 어긋나고 그것을 맞추려면 본문을 고쳐 써야
    한다 — 불변식 3 이 막는 일이다. 200 응답도 지목 여부와 무관하게 컨텍스트 전부를 싣는다.
    근거가 0건인 거절(빈 코퍼스의 응급 질문)은 `hits` 가 비고, 그때는 본문에도 가리킬 번호가
    없어야 정상이다 — 어댑터 테스트가 그 성질을 붙잡는다.
    """
    return {
        "code": f"{answer.boundary}_boundary",
        "message": answer.text,
        "hits": [_hit(h).model_dump() for h in answer.hits],
        "cited": list(answer.cited),
        "ungrounded": list(answer.ungrounded),
    }


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


__all__ = ["ask", "refusal_detail", "to_dto", "SERVING_K", "SERVING_SUPPLEMENTARY"]
