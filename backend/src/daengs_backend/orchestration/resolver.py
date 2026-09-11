"""현재 발화를 앞 요청에 잇는 자리 (#416).

**이력 원문은 이 파일에서 멈춥니다.** 아래로 내려가는 것은 `ConversationContext` 뿐이고,
라우터도 capability 도 후보 turn 을 못 봅니다 — 관계 판정이 프롬프트 안에 묻히면 실패가
관측되지 않기 때문입니다(스펙 §2).

`emergency.py` · `semantic.py` 와 같은 층의 형제입니다. 순서는 **응급 → 결정론 → 여기 →
시맨틱 라우터**이고, 응급이 앞인 것은 의도입니다: 응급 경계는 현재 사용자 원문을 직접
검사해야 하고 이 파일의 결과가 그것을 약하게 만들 수 없습니다(스펙 ⑦-4).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from functools import lru_cache
from typing import Any

from langsmith import traceable
from pydantic import BaseModel, Field, ValidationError, model_validator

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    ContractModel,
    ConversationContext,
    ObservationAxis,
    TurnRelation,
)
from daengs_backend.orchestration.semantic import ROUTER_CANDIDATE_COUNT, ROUTER_TEMPERATURE

__all__ = [
    "MAX_ASSISTANT_CHARS",
    "MAX_CANDIDATE_BLOCK_CHARS",
    "MAX_CANDIDATE_PAIRS",
    "RESOLUTION_CONFIDENCE_FLOOR",
    "TURN_RESOLVER_MODEL_ID",
    "TURN_RESOLVER_PROMPT_VERSION",
    "ConversationContext",
    "GeminiTurnResolver",
    "PendingClarification",
    "PriorTurn",
    "ResolvedTurn",
    "TurnRelation",
    "TurnResolutionError",
    "build_candidate_block",
    "build_turn_resolver_prompt",
    "conversation_context_of",
    "fit_candidates",
    "needs_resolution",
    "new_turn",
    "truncate_assistant",
    "validate_resolved_turn",
]

#: 후보로 쓰는 완료 turn 쌍의 수. 가장 긴 수용 케이스가 요구하는 최소가 3이다.
#: Task 2 의 `fit_candidates` 가 이 개수 상한과 문자 예산(`MAX_CANDIDATE_BLOCK_CHARS`)을
#: 함께 적용한다 — 여기서는 상수만 정의하고 아직 아무도 안 부른다.
MAX_CANDIDATE_PAIRS = 3
#: 후보의 assistant 원문 상한. DB 상한이 8,000자라 안 자르면 통째로 프롬프트에 온다.
MAX_ASSISTANT_CHARS = 400
#: 후보 블록 전체 상한. 한 쌍의 최대치(2,000+400)가 이 아래라 최신 쌍은 늘 남는다.
MAX_CANDIDATE_BLOCK_CHARS = 3_000
#: 이 아래면 잇지 않고 되묻는다 (수용 케이스 9).
RESOLUTION_CONFIDENCE_FLOOR = 0.6

# `TurnRelation` 과 `ConversationContext` 는 `contracts.py` 에 산다 (#416) — `ConversationContext`
# 가 (나중에) `contracts.GeneralPayload` 안에 실리므로 그 필드 타입인 `TurnRelation` 도
# 같이 그 모듈에 있어야 순환 임포트가 안 생긴다. 여기서는 재수출만 한다 — 기존 코드와
# 테스트가 `from daengs_backend.orchestration.resolver import TurnRelation` 을 쓰기 때문이다.


class PriorTurn(ContractModel):
    """후보 한 쌍. `turn_id` 가 있어야 모델이 지목한 것을 행으로 되돌릴 수 있다."""

    turn_id: uuid.UUID
    user: str
    assistant: str


class PendingClarification(ContractModel):
    """대기 중인 되묻기 — 가장 최근 완료 turn 이 `CLARIFY` 일 때만 있다.

    **되묻기는 두 부류다** (PR 본문 ④):

    | 부류 | `missing_axes` | 후속 답변을 어디에 묶나 |
    | --- | --- | --- |
    | 관찰 되묻기 | `["APPETITE", …]` | 그 축에 묶는다 |
    | 맥락 되묻기 | `[]` | 축이 아니라 **가리킨 대상**을 물은 것이다 |

    `missing` 으로는 못 가른다 — General ask 는 늘 `["observation"]` 이다
    (`GENERAL_ASK_MISSING` 하드코딩). 가르는 것은 `missing_axes` 의 비어 있음뿐이고,
    비어 있으면 **"축을 모른다"** 이지 "물은 것이 없다" 가 아니다 — 그래서
    `is_observation_ask` 가 거짓일 때 **축을 지어내면 안 된다.**

    `missing` 을 그래도 들고 다니는 것은 좌표 게이트(`["location.lat"]`)와 General ask 를
    가리기 위해서다 — 그 둘은 계획 시점과 집계 시점이라 출처가 다르다.
    """

    turn_id: uuid.UUID
    question: str
    missing: list[str] = Field(default_factory=list)
    missing_axes: list[ObservationAxis] = Field(default_factory=list)

    @property
    def is_observation_ask(self) -> bool:
        """축이 있으면 관찰 되묻기. 없으면 맥락 되묻기이거나 축을 모르는 것이고, 둘 다
        후속 답변을 축에 묶으면 안 된다."""
        return bool(self.missing_axes)


class ResolvedTurn(ContractModel):
    """판정 결과. `current_query` 는 **절대 대체하지 않는다** — 원문과 출처를 늘 같이 든다."""

    relation: TurnRelation
    current_query: str = Field(min_length=1)
    referenced_turn_id: uuid.UUID | None = None
    pending_clarification_id: uuid.UUID | None = None
    referenced_original_request: str | None = None
    pending_missing_axes: list[ObservationAxis] = Field(default_factory=list)
    #: 모델이 만든 추론용 표현. **사실이 아니다** — 반려견 기록으로 저장하지 않는다.
    standalone_query: str | None = None
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: str | None = None
    context_used: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_anchor_at_most(self) -> ResolvedTurn:
        if self.referenced_turn_id is not None and self.pending_clarification_id is not None:
            raise ValueError("referenced_turn_id and pending_clarification_id are exclusive")
        return self


#: 맥락 의존 신호. 없으면 모델을 안 태운다 — `resolve_emergency_route` ·
#: `resolve_deterministic_route` 가 이미 모델 앞에서 하는 것과 같은 규칙 기반 선별이다.
#: **정밀도는 일부러 안 다듬었다** (#416 fix round 1). `다시` · `방금` · `아까` 는 넓게
#: 걸리지만 실패 방향이 안전하다 — 잘못 걸려도 모델을 한 번 더 태우는 것으로 끝나지,
#: 이어야 할 맥락을 놓치지 않는다. 제대로 튜닝하려면 이 플랜 뒤쪽에 오는 평가랩
#: 데이터가 있어야 해서, 그때까지는 넓게 두는 것이 의도한 선택이다. `말고`(→
#: `말고기` 오탐)와 `또`(→ `또띠아`/"또 토했어" 오탐)는 예외 — 그 둘은 흔한 새 주제
#: 문장을 오염시키는 실제 결함이라 fix round 1 에서 좁혔다/뺐다.
_CONTEXT_MARKERS = re.compile(
    r"그거|그걸|그것|저거|저걸|걔|아까|방금|말한\s*거|"          # 지시어
    r"아니(?![요라])|말고(?![가-힣])|가\s*아니라|이\s*아니라|"     # 정정
    r"그러니까|그니까|다시|했잖아|물어봤|"                         # 반복
    r"물어봐야|왜\s*안|안\s*물어"                                   # 메타
)


def needs_resolution(
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> bool:
    """모델을 태울 값이 있나.

    대기 중인 되묻기가 있으면 표지와 무관하게 참이다 — `"밥은 먹는데 계속 누워 있어"` 에는
    지시어가 없지만 그것이 앞 질문의 답이기 때문이다(수용 케이스 5).
    """
    if pending is not None:
        return True
    if not candidates:
        return False
    return _CONTEXT_MARKERS.search(query) is not None


def conversation_context_of(resolved: ResolvedTurn | None) -> ConversationContext | None:
    """`ResolvedTurn` → `ConversationContext` (#416 Task 5). 두 소비자가 같은 변환을 쓴다 —
    `planner._payload_for` 가 `GeneralPayload.conversation` 을 채울 때, 그리고
    `service._plan_and_execute` 가 시맨틱 라우터의 (아직 미사용인) `resolved` 인자에
    넘길 때. 변환이 여기 하나면 둘이 서로 다른 모양을 만들 수 없다.

    `pending_question` 은 늘 `None` 이다 — `ResolvedTurn` 은 `pending_clarification_id`
    만 들고 있고 되묻기 문장 자체는 호출자의 `PendingClarification` 객체에 있어서, 그
    텍스트를 잇는 것은 이 변환의 몫이 아니다.
    """
    if resolved is None:
        return None
    return ConversationContext(
        relation=resolved.relation,
        referenced_original_request=resolved.referenced_original_request,
        standalone_query=resolved.standalone_query,
        pending_missing_axes=resolved.pending_missing_axes,
    )


def new_turn(query: str) -> ResolvedTurn:
    """fast path 의 결과. 아무것도 안 잇고, 아무것도 안 넘긴다."""
    return ResolvedTurn(
        relation=TurnRelation.NEW, current_query=query, resolution_confidence=1.0
    )


def truncate_assistant(text: str) -> str:
    """`MAX_ASSISTANT_CHARS` 에서 자르고 `…` 를 붙인다. DB 상한은 8,000자다."""
    if len(text) <= MAX_ASSISTANT_CHARS:
        return text
    return text[:MAX_ASSISTANT_CHARS] + "…"


def fit_candidates(turns: Sequence[PriorTurn]) -> list[PriorTurn]:
    """블록 상한과 pair 개수 상한에 맞게 **오래된 쌍부터** 버린다. 최신 쌍은 무조건 남는다.

    한 쌍의 최대치가 user 2,000자 + assistant 400자 = 2,400자로 `MAX_CANDIDATE_BLOCK_CHARS`
    아래이므로 이 규칙은 늘 만족 가능하다. 세 숫자는 같이 움직여야 한다.
    """
    kept: list[PriorTurn] = []
    total = 0
    for turn in reversed(list(turns)):
        # 개수 상한에 이미 도달했으면 그만
        if len(kept) >= MAX_CANDIDATE_PAIRS:
            break
        size = len(turn.user) + len(truncate_assistant(turn.assistant))
        # 문자 상한을 초과하면 이 쌍을 버리고 그만
        if kept and total + size > MAX_CANDIDATE_BLOCK_CHARS:
            break
        kept.append(turn)
        total += size
    return list(reversed(kept))


def build_candidate_block(turns: Sequence[PriorTurn]) -> str:
    """오래된 것부터, 한 줄에 한 발화, 번호를 붙여서.

    **번호가 `referenced_turn_id` 의 근거다** — 모델이 "U1 을 가리킨다" 고 말할 수 있어야
    서버가 그것을 실제 turn id 로 되돌린다. JSON 이 아닌 것은 따옴표·이스케이프로 토큰이
    늘기 때문이다.
    """
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        lines.append(f"U{index}: {turn.user}")
        lines.append(f"A{index}: {truncate_assistant(turn.assistant)}")
    return "\n".join(lines)


#: 모델을 바꾸면 프롬프트도 같이 재검증해야 하므로 하나로 묶어 둔다.
TURN_RESOLVER_MODEL_ID = "gemini-3.1-flash-lite"
#: 프롬프트 문구를 바꾸면 올린다 — 로그·eval 이 이 값으로 프롬프트 버전을 구분한다.
TURN_RESOLVER_PROMPT_VERSION = "turn-resolver-ko-v1"

_POLICY = """You classify how the owner's current message relates to the conversation so far. You do not answer it.

Return exactly one JSON object conforming to the supplied schema.

relation is one of:
- NEW: the message stands on its own. Choose this whenever the earlier turns are about a different topic. An unrelated earlier turn must not drag the current message toward it.
- FOLLOW_UP: the message continues an earlier request, or answers PENDING_CLARIFICATION.
- CORRECTION: the owner is correcting the premise of an earlier request ("아니, 산책 말고 밥").
- REPEAT: the owner is asking again for something an earlier turn failed to deliver.
- META: the message is about this conversation itself — a complaint, or telling you what you should have asked. It is not off-topic.

referenced_index names the candidate pair the message attaches to, as the number in U1/A1. Use null when relation is NEW or when the message answers PENDING_CLARIFICATION instead.

standalone_query is the current message rewritten so it can be read alone. It is a working restatement, never a fact about the owner or the dog. Leave it null when relation is NEW.

resolution_confidence is how sure you are of the attachment, 0.0 to 1.0. When the message points at something you cannot identify among the candidates, set a low confidence and say what is unclear in ambiguity. Do not guess an attachment.

PENDING_CLARIFICATION.asked_axes are the items the assistant asked about. They are NOT observations about the dog and NOT facts. An empty list means the axes are unknown, not that nothing was asked."""


class _RawResolution(BaseModel):
    """모델이 돌려주는 원본 JSON 의 스키마. 검증 통과 전에는 신뢰하지 않는다."""

    relation: TurnRelation
    referenced_index: int | None = None
    standalone_query: str | None = None
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: str | None = None


def build_turn_resolver_prompt(
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> str:
    """`CURRENT_QUERY:` 가 **맨 뒤**다 — Place 실측(2026-09-10, 102건)이 그 배치를 요구한다.

    문맥을 최신 질의 뒤에 두면 모델이 최신 요청 대신 이전 요청에 답하는 퇴행이 관측됐고,
    맨 뒤로 옮기자 9/9 사례가 고쳐졌다. `semantic.build_semantic_router_prompt` ·
    `adapters/general.build_general_prompt` 도 같은 이유로 `USER_QUERY:` 를 맨 뒤에 둔다.
    """
    if not query.strip():
        raise ValueError("query must not be blank")
    schema = json.dumps(_RawResolution.model_json_schema(), ensure_ascii=False, sort_keys=True)
    parts = [
        f"PROMPT_VERSION: {TURN_RESOLVER_PROMPT_VERSION}",
        _POLICY,
        f"RESOLUTION_JSON_SCHEMA:\n{schema}",
    ]
    block = build_candidate_block(candidates)
    if block:
        parts.append(f"CANDIDATE_TURNS:\n{block}")
    if pending is not None:
        asked = json.dumps(
            {"question": pending.question, "asked_axes": [str(a) for a in pending.missing_axes]},
            ensure_ascii=False,
            sort_keys=True,
        )
        parts.append(f"PENDING_CLARIFICATION: {asked}")
    parts.append(f"CURRENT_QUERY: {query}")
    return "\n\n".join(parts) + "\n"


def validate_resolved_turn(
    raw: object,
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> ResolvedTurn | None:
    """스키마 검증 + **후보 밖 지목 거부**. 잘못된 원본 출력은 밖으로 안 내보낸다 — `None`.

    `current_query` 는 늘 인자로 받은 `query` 원문이다. 모델이 만든 `standalone_query` 로
    바꿔치지 않는다 — 사용자 원문은 대체 대상이 아니다.
    """
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    try:
        decision = _RawResolution.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None

    if decision.relation is TurnRelation.NEW and decision.referenced_index is not None:
        # NEW 인데 후보를 지목하는 것은 모순된 출력이다 — 조용히 인덱스를 버리지 않고
        # 통째로 신뢰하지 않는다(R9).
        return None

    referenced: PriorTurn | None = None
    if decision.referenced_index is not None:
        index = decision.referenced_index
        if not 1 <= index <= len(candidates):
            return None
        referenced = candidates[index - 1]

    # 대기 중인 되묻기에 실제로 묶이는지 — relation 검사까지 한 번에 넣어 두 필드가
    # 서로 다른 조건으로 갈라지지 않게 한다(R8). `relation == NEW` 이면 이 turn 은
    # 새 turn 으로 읽혀야 하므로 pending 관련 필드는 전부 비운다.
    anchored_to_pending = (
        pending is not None
        and referenced is None
        and decision.relation is not TurnRelation.NEW
    )
    return ResolvedTurn(
        relation=decision.relation,
        current_query=query,
        referenced_turn_id=referenced.turn_id if referenced else None,
        pending_clarification_id=pending.turn_id if anchored_to_pending else None,
        referenced_original_request=referenced.user if referenced else None,
        pending_missing_axes=(
            list(pending.missing_axes) if anchored_to_pending else []
        ),
        standalone_query=decision.standalone_query,
        resolution_confidence=decision.resolution_confidence,
        ambiguity=decision.ambiguity,
        context_used=[referenced.turn_id] if referenced else [],
    )


class TurnResolutionError(Exception):
    """제공자 실패 또는 스키마 실패. 원본 출력은 절대 밖으로 안 나간다."""


@lru_cache(maxsize=1)
def _gemini_client() -> Any:
    # google-genai stays a function-local import so importing the resolver never
    # pulls provider machinery (mirrors semantic.py's lazy-import rule).
    from google import genai
    from google.genai import types

    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        raise TurnResolutionError("GEMINI_API_KEY is required for the turn resolver")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
    )


def _turn_resolver_generation_config() -> Any:
    """The one generation config of the turn resolver.

    Temperature and candidate count are imported from `semantic.py` rather than
    re-typed here — two implementations spelling the same numbers separately would
    stop proving they were measured under the same settings.
    """
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        response_mime_type="application/json",
        response_json_schema=_RawResolution.model_json_schema(),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        response = _gemini_client().models.generate_content(
            model=TURN_RESOLVER_MODEL_ID,
            contents=prompt,
            config=_turn_resolver_generation_config(),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


def _trace_resolver_output(raw: object) -> dict[str, Any]:
    """트레이스에 실을 원답. 스키마 검증 **전**의 값이라 그대로 싣는다.

    `semantic._trace_router_output` 과 같은 가드다 — langsmith 는 트레이싱이 꺼져
    있어도 `process_outputs` 를 부른다. 사용자 발화·이력 원문은 여기 실리지 않는다 —
    모델의 원 JSON 판정만 싣는다(D-037, D-048).
    """
    from langsmith import utils as ls_utils

    if not ls_utils.tracing_is_enabled():
        return {}
    return {"raw": raw}


@traceable(
    run_type="llm",
    name="turn_resolver",
    process_inputs=lambda inputs: {"prompt": inputs.get("prompt")},
    process_outputs=_trace_resolver_output,
    metadata={"model": TURN_RESOLVER_MODEL_ID, "prompt_version": TURN_RESOLVER_PROMPT_VERSION},
)
async def _traced_generate(generate: Callable[[str], Awaitable[object]], prompt: str) -> object:
    """리졸버의 모델 호출 한 번 = LLM 런 하나. `semantic._traced_generate` 와 같은 이유로
    주입된 `generate` 를 감싼다 — 테스트가 가짜를 주입해도 런이 같은 자리에 남는다."""
    return await generate(prompt)


class GeminiTurnResolver:
    """앞 요청과의 관계 판정. fast path 는 모델을 안 부르고, 실패는 하나의 계약 오류로 좁힌다."""

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def resolve(
        self,
        *,
        query: str,
        candidates: Sequence[PriorTurn],
        pending: PendingClarification | None,
    ) -> ResolvedTurn:
        fitted = fit_candidates(candidates)
        if not needs_resolution(query=query, candidates=fitted, pending=pending):
            return new_turn(query)
        prompt = build_turn_resolver_prompt(query=query, candidates=fitted, pending=pending)
        try:
            raw = await _traced_generate(self._generate, prompt)
        except TurnResolutionError:
            raise
        # 여기서는 로그를 안 남긴다 — request_id 를 들고 있는 것은 호출자(service.py)뿐이고
        # D-037 이 질의 원문 로깅을 막는 이상 request_id 없는 로그는 트레이스에 못 묶여
        # 잡음만 남긴다. 실패 로그는 상위 계층의 몫이다 (semantic.py 의 router 실패도
        # 같은 이유로 여기서 로깅하지 않고 service.py 가 request_id 와 함께 남긴다).
        except Exception as exc:  # 제공자 예외를 하나의 계약 오류로 좁힌다
            raise TurnResolutionError("turn resolution provider failed") from exc
        resolved = validate_resolved_turn(raw, query=query, candidates=fitted, pending=pending)
        if resolved is None:
            raise TurnResolutionError("turn resolution output failed schema validation")
        return resolved
