"""General-answer fallback: a bounded Gemini generation behind a safety prompt (#279).

This is the capability the planner assembles when the router selected nothing — the
questions that until now ended as "실행하거나 안내할 수 있는 기능이 없습니다". It answers
without evidence, so the whole design is about what it is **not allowed** to say:

- No diagnosis, no medication or dosage, no emergency handling beyond "go to a vet now".
- No claims about laws, fees, deadlines, or numbers — those need evidence, and the
  assistant's institutional-information capability (Life) is where evidence lives.
- Questions unrelated to dogs are politely declined.

The model returns one JSON object: ``kind`` is ``answer`` or ``refuse``. A refusal carries
a **reason category**, and the user-facing redirect for each category is fixed here, not
written by the model — a refusal is the one place model prose must not leak through,
because "무엇에 물어보라 / 수의사" is a product sentence (#278), not a generation. An
adapter that only ever returns OK gives the answer-quality judge (#277) nothing to catch;
``REFUSED`` is what makes the fallback measurable.

Provider and output failures are contained as ``ERROR``/``TIMEOUT`` exactly like the other
adapters: the fallback failing must look like a capability failing, never like a crash.

The Gemini client is the semantic router's (``semantic._gemini_client``): same key, same
timeout, one lazily-built client per process. Generation settings reuse the router's
constants where they apply; only the output budget is wider, because a short answer plus
its JSON envelope does not fit in a routing decision's 256 tokens.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    GeneralPayload,
    OutcomeDetail,
)
from daengs_backend.orchestration.redirects import SCOPED_REDIRECT_MESSAGES, RefusalReason
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    _gemini_client,
)

# v2 (D-057 ③ⓐ): v1 은 통상 돌봄 기준(급여량 · 음수량)을 institutional · diagnosis 로 사양했다 —
# #277 실측에서 general_care 15건 중 7건이 과잉 거절이었다. v2 는 통상 기준을 "개체차를 단서로
# 범위를 답한다" 로 명시하고, institutional 은 출처 문서가 있어야 하는 사실로, diagnosis 는
# 병명 · 원인 판정 · 검사 해석을 명시적으로 묻는 것으로 좁혔다.
# v3: 같은 규칙을 **영문**으로 옮겼다 — 의미 라우터의 `_POLICY` 와 같은 언어로 두라는 사람 결정.
# 출력 언어(한국어)와 어조는 지시문 안에서 정한다. `ko` 는 출력 언어다.
GENERAL_PROMPT_VERSION = "general-answer-ko-v3"
# v4-carelog (#344): v3 본문에 CARE_LOG_TODAY 규칙 한 문단과 블록 한 줄이 **더해진** 판본.
# 오늘 케어 로그가 payload 에 있을 때만 이 판본이 나가고, 없으면 v3 가 글자까지 그대로 나간다 —
# v3 는 D-057 ③ 에서 84건 쌍대 비교 뒤 승인된 본문이라, 그 84건(로그 없음)의 프롬프트를 이
# 카드가 바꾸지 않게 하려는 분기다. 버전 문자열이 갈리는 이유는 프롬프트 텍스트가 다르기 때문이다.
GENERAL_CARE_LOG_PROMPT_VERSION = "general-answer-ko-v4-carelog"
# v5-vetspend / v5-carelog-vetspend (#353 Task 7): confirmed vet-visit spend joins the same
# fallback, same rule as care log — a rule paragraph plus a context line, added only when
# the payload carries it. Four combinations of {care_log, vet_spend} now exist; the two
# that existed before this card (absent/absent → v3, present/absent → v4-carelog) are
# reproduced by the same literal strings as before, not reconstructed, so D-057 ③'s
# approved v3 body cannot drift by refactor. The new two get their own version strings
# because their prompt text differs from both — `build_general_prompt` picks the version
# from exactly which of the two optional blocks are present.
GENERAL_VET_PROMPT_VERSION = "general-answer-ko-v5-vetspend"
GENERAL_CARE_LOG_VET_PROMPT_VERSION = "general-answer-ko-v5-carelog-vetspend"
GENERAL_MODEL_ID = ROUTER_MODEL_ID
# 답 문장 3~5개 + JSON 봉투. 라우터의 256 은 분류 한 줄을 위한 예산이라 여기엔 좁다.
GENERAL_MAX_OUTPUT_TOKENS = 512

# 거절 사유별 안내 문구는 [daengs_backend.orchestration.redirects] 에 있다 — `refusal.message`
# 로 그대로 나가고, `refusal.code` 는 사유 범주다. `aggregate.py` 의 빈 선택 FAILED 문구와
# 한 곳에서 관리한다 (#278).


class GeneralAnswer(BaseModel):
    """The complete output surface of the fallback model."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "refuse"]
    # **필수 필드다.** 기본값 "" 을 두면 JSON 스키마에서 선택 필드가 되고, 제약 디코딩은 그것을
    # 그대로 허용한다 — 실측에서 모델이 `{"kind": "answer", "reason": null}` 로 text 를 통째로
    # 빼고 답해 `general_invalid_output` 이 됐다 (#279 라이브 확인). 거절일 때는 "" 을 낸다.
    text: str = Field(max_length=1_000)
    reason: RefusalReason | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> GeneralAnswer:
        if self.kind == "answer":
            if not self.text.strip():
                raise ValueError("an answer needs text")
            if self.reason is not None:
                raise ValueError("an answer carries no refusal reason")
        elif self.reason is None:
            raise ValueError("a refusal needs a reason category")
        return self


# 안전 프롬프트. 의미 라우터의 `_POLICY` 와 같이 **영문**으로 둔다 (사람 결정, v3). 출력 언어와
# 어조는 지시문이 정한다 — 답은 한국어다. 골드·평가 세트의 문구를 옮겨 적지 않는다.
_SAFETY_PROMPT = """You are the general-answer component of the DAENGS dog-care assistant. Only questions that none of the specialized capabilities (training, institutional information, walking conditions, place search) handle arrive here.

Output exactly one JSON object conforming to the supplied schema. If kind is "answer", write the answer in text and set reason to null. If kind is "refuse", set reason to one of the reason categories and leave text empty. No Markdown, no greetings, no filler.

Rules when answering:
- Write in Korean, briefly (3 to 5 sentences). Answer what can be said safely at a common-sense level: general dog care, habits, gear, and everyday routines.
- Ordinary husbandry norms ARE answerable: feeding frequency and a rough amount range, daily water intake, bathing / brushing / nail-trimming frequency, walking gear, socialization timing, sleep duration. Give the typical range, state that individual variation is large, and add that the feeding table on the food package or the veterinarian is the authority for exact values. These ordinary norms are NOT institutional.
- A question of the form "is this okay / is this normal" about a behavior or an intake amount is answered with the normal range plus a note to see a veterinarian if it persists or changes sharply. Do not refuse it.
- Say you do not know when unsure; never invent. Do not assert facts that require a source document (laws, regulations, procedures, fees, deadlines, official programs, statistics).
- If a symptom is mentioned, end with a short note such as "if the symptom persists, have a veterinarian look at it" and nothing more.
- Use DOG_CONTEXT when present, but never invent facts that are not in it.

Refuse (kind="refuse") only in these cases:
- diagnosis: the user explicitly asks for a disease name, asks to determine the cause of a symptom, or asks to interpret test results. "Is this okay / is this normal" is NOT diagnosis.
- medication: questions about drugs, supplements, dosages, or administration.
- emergency: questions about handling an emergency such as poisoning, breathing difficulty, bleeding, seizures, or loss of consciousness. Say nothing beyond "go to a veterinary hospital right now".
- institutional: facts that require a source document, such as laws, regulations, administrative procedures, fees, deadlines, or official support programs. The assistant's institutional-information capability answers those. Ordinary husbandry numbers do NOT belong here.
- off_topic: the question is not about dogs. Check this first: a request that is not about dogs at all is off_topic even when it mentions money, schedules, or procedures.

reason is one of the five values above, and null when kind="answer"."""


# 로그가 있을 때만 붙는 규칙 (#344). 로그가 무엇인지, 무엇을 해도 되고 무엇은 안 되는지.
# "did I / has it been done today" 류에 쓰라는 것과, 로그에 없는 용량·일정을 지어내지 말라는 것.
# 약 이름은 로그에도 DOG_CONTEXT 에도 없으므로 v3 의 medication 거절은 그대로 선다.
_CARE_LOG_RULE = """CARE_LOG_TODAY, when present, is what the owner has already logged for this dog today: counts per kind (meal, medication, snack, walk) and the last time each was logged, as HH:MM in Seoul time. Treat it as fact for questions like "did I feed / medicate / walk today", "has the morning medication been given", or "how many meals so far". You may say what was logged and when, and note plainly when a kind has no entry today. Never infer a dose, a schedule, or whether more is needed from it — the log records what happened, not what should happen. If the question is not about today's care, ignore the log. When CARE_LOG_TODAY is absent, say nothing about a log."""

# 진료비가 있을 때만 붙는 규칙 (#353 Task 7). VET_RECENT 가 무엇인지, 무엇을 해도 되고
# 무엇은 안 되는지 — `_CARE_LOG_RULE` 과 같은 결. 진단·처치를 권하지 말라는 것과, 기록에
# 없는 사유·금액을 지어내지 말라는 것.
_VET_SPEND_RULE = """VET_RECENT, when present, is what the owner has confirmed about this dog's vet visits: this month's total spend, the visit count in the last 30 days, the most recent visit (date, reason, amount, and the hospital's name/phone if known), and total spend per reason over the last 12 months. Treat it as fact for questions like "how much have I spent on skin issues this year" or "what was that hospital's phone number". Use only the reasons and numbers present; never invent a visit, a reason, or an amount that is not there. Never diagnose, recommend treatment, or judge whether spending is high or normal from it — it is a spending record, not a medical opinion. If the question is not about vet visits or spending, ignore it. When VET_RECENT is absent, say nothing about vet spending or visit history."""


def build_general_prompt(payload: GeneralPayload) -> str:
    """Assemble the fallback prompt from optional blocks.

    **The two combinations that predate this card are reproduced by the exact same
    literal strings as before** (D-057 ③ / #344) — the ``care_log is None and vet_spend
    is None`` branch below is untouched code, not a block reconstruction, so v3's
    84-pairwise-approved body cannot drift through this refactor. The v4-carelog case
    *is* now assembled from blocks (below), but the assembly is byte-for-byte the same
    string the old dedicated branch produced — see the block order comment.
    """
    schema = json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)
    dog = payload.dog.model_dump(mode="json", exclude_none=True) if payload.dog else {}

    if payload.care_log is None and payload.vet_spend is None:
        return (
            f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}\n\n"
            f"{_SAFETY_PROMPT}\n\n"
            f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
            f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}\n"
            f"USER_QUERY: {payload.question}\n"
        )

    if payload.care_log is not None and payload.vet_spend is not None:
        version = GENERAL_CARE_LOG_VET_PROMPT_VERSION
    elif payload.care_log is not None:
        version = GENERAL_CARE_LOG_PROMPT_VERSION
    else:
        version = GENERAL_VET_PROMPT_VERSION

    # Rule paragraphs: safety always, care-log rule before vet-spend rule — that order is
    # what keeps the care-log-only prompt identical to the pre-vet-spend v4-carelog body.
    rule_blocks = [_SAFETY_PROMPT]
    if payload.care_log is not None:
        rule_blocks.append(_CARE_LOG_RULE)
    if payload.vet_spend is not None:
        rule_blocks.append(_VET_SPEND_RULE)

    # Context lines: DOG_CONTEXT always, CARE_LOG_TODAY before VET_RECENT — same reason.
    context_lines = [f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}"]
    if payload.care_log is not None:
        care_log = payload.care_log.model_dump(mode="json", exclude_none=True)
        context_lines.append(
            f"CARE_LOG_TODAY: {json.dumps(care_log, ensure_ascii=False, sort_keys=True)}"
        )
    if payload.vet_spend is not None:
        vet_spend = payload.vet_spend.model_dump(mode="json", exclude_none=True)
        context_lines.append(
            f"VET_RECENT: {json.dumps(vet_spend, ensure_ascii=False, sort_keys=True)}"
        )

    # 인접 리터럴의 암묵적 연결에 기대지 않는다 — `+` 로만 잇는다. 이유는 이 파일이
    # 존재하는 이유와 같다: 나중에 이 두 조각 사이에 표현식 하나가 끼어들면, 암묵적
    # 연결은 구분자를 조용히 빠뜨리지만 명시적 `+` 는 그 자리에서 문법 오류로 걸린다.
    return (
        f"PROMPT_VERSION: {version}\n\n"
        + "\n\n".join(rule_blocks)
        + "\n\n"
        + f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        + "\n".join(context_lines)
        + "\n"
        + f"USER_QUERY: {payload.question}\n"
    )


def general_generation_config() -> Any:
    """Lazy for the same reason as ``semantic.router_generation_config``."""
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=GENERAL_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_json_schema=GeneralAnswer.model_json_schema(),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        response = _gemini_client().models.generate_content(
            model=GENERAL_MODEL_ID,
            contents=prompt,
            config=general_generation_config(),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


def validate_general_answer(raw: object) -> GeneralAnswer | None:
    """Schema-validate provider output; invalid raw output is never surfaced."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, GeneralAnswer):
        return parsed
    try:
        return GeneralAnswer.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


class GeneralCapabilityAdapter:
    capability = CapabilityName.GENERAL

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # nothing downstream consumes trace context here
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, GeneralPayload):
            return self._error(started, "invalid_payload", "일반 답변 요청이 올바르지 않습니다.")

        try:
            raw = await self._generate(build_general_prompt(payload))
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="general_timeout", detail="일반 답변 응답 시간이 초과됐습니다."
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception:  # noqa: BLE001 - contain the provider like every other adapter
            return self._error(
                started, "general_provider_failure", "일반 답변 기능 실행에 실패했습니다."
            )

        answer = validate_general_answer(raw)
        if answer is None:
            return self._error(
                started, "general_invalid_output", "일반 답변 결과를 해석할 수 없습니다."
            )
        if answer.kind == "refuse":
            reason = answer.reason or "off_topic"
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.REFUSED,
                refusal=OutcomeDetail(code=reason, message=SCOPED_REDIRECT_MESSAGES[reason]),
                elapsed_ms=_elapsed_ms(started),
            )
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": answer.text.strip()},
            elapsed_ms=_elapsed_ms(started),
        )

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = [
    "GENERAL_CARE_LOG_PROMPT_VERSION",
    "GENERAL_CARE_LOG_VET_PROMPT_VERSION",
    "GENERAL_MAX_OUTPUT_TOKENS",
    "GENERAL_MODEL_ID",
    "GENERAL_PROMPT_VERSION",
    "GENERAL_VET_PROMPT_VERSION",
    "GeneralAnswer",
    "GeneralCapabilityAdapter",
    "build_general_prompt",
    "general_generation_config",
    "validate_general_answer",
]
