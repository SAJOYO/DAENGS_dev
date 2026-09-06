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
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    _gemini_client,
)

GENERAL_PROMPT_VERSION = "general-answer-ko-v1"
GENERAL_MODEL_ID = ROUTER_MODEL_ID
# 답 문장 3~5개 + JSON 봉투. 라우터의 256 은 분류 한 줄을 위한 예산이라 여기엔 좁다.
GENERAL_MAX_OUTPUT_TOKENS = 512

RefusalReason = Literal["diagnosis", "medication", "emergency", "institutional", "off_topic"]

# 거절 사유별 안내 문구. **모델이 쓰지 않는다** — 모듈 docstring. `refusal.message` 로
# 그대로 나가고, `refusal.code` 는 사유 범주다.
_REFUSAL_MESSAGES: dict[str, str] = {
    "diagnosis": "증상의 원인이나 병명은 여기서 판단하지 않아요. 가까운 동물병원에서 진료를 받아 보세요.",
    "medication": "약이나 영양제, 용량은 여기서 안내하지 않아요. 수의사에게 확인해 주세요.",
    "emergency": "응급 상황으로 보여요. 지금 바로 동물병원으로 가세요.",
    "institutional": (
        "제도·법령·요금·기한 같은 사실은 근거와 함께 답하는 제도 정보 기능에 물어봐 주세요."
    ),
    "off_topic": "반려견에 관한 질문만 도와드릴 수 있어요.",
}


class GeneralAnswer(BaseModel):
    """The complete output surface of the fallback model."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "refuse"]
    text: str = Field(default="", max_length=1_000)
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


# 안전 프롬프트. 한국어로 둔 것은 라우터(영어)와 달리 **출력 언어와 어조**를 정하는
# 프롬프트라서다. 골드 케이스의 문구를 옮겨 적지 않는다.
_SAFETY_PROMPT = """당신은 DAENGS 반려견 비서의 일반 답변 담당입니다. 전문 기능(훈련 · 제도 정보 · 산책 조건 · 장소 찾기)이 맡지 않은 질문만 여기로 옵니다.

출력은 아래 JSON 스키마를 따르는 객체 하나뿐입니다. kind 가 "answer" 면 text 에 답을 쓰고 reason 은 null 로 둡니다. kind 가 "refuse" 면 reason 에 사유 범주를 넣고 text 는 비웁니다. 마크다운 · 인사말 · 사족을 넣지 않습니다.

답할 때의 규칙:
- 한국어로, 짧게 씁니다 (문장 3~5개). 일반적인 반려견 돌봄 · 습성 · 준비물 · 생활 요령처럼 상식 수준에서 안전하게 말할 수 있는 것만 답합니다.
- 확신이 없는 것은 모른다고 말하고, 지어내지 않습니다. 법령 · 제도 · 요금 · 기한 · 통계 같은 수치나 사실을 단정하지 않습니다.
- 증상이 언급되면 답 끝에 "증상이 이어지면 수의사에게 보이세요" 정도로만 안내합니다.
- DOG_CONTEXT 가 있으면 참고하되, 거기 없는 사실을 지어내지 않습니다.

다음은 답하지 않고 kind="refuse" 로 냅니다:
- diagnosis: 질병이나 상태의 진단, 원인 판정을 묻는 경우.
- medication: 약 · 영양제 · 용량 · 투여 방법을 묻는 경우.
- emergency: 중독 · 호흡 곤란 · 출혈 · 발작 · 의식 저하 같은 응급 상황의 대처를 묻는 경우 — "지금 바로 동물병원으로" 이상의 처치는 말하지 않습니다.
- institutional: 법령 · 제도 · 절차 · 요금 · 기한 · 수치처럼 근거가 있어야 답이 되는 사실을 묻는 경우 — 그것은 이 비서의 제도 정보 기능이 답합니다.
- off_topic: 반려견과 무관한 질문.

reason 은 위 다섯 값 중 하나이고, kind="answer" 면 null 입니다."""


def build_general_prompt(payload: GeneralPayload) -> str:
    schema = json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)
    dog = payload.dog.model_dump(mode="json", exclude_none=True) if payload.dog else {}
    return (
        f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}\n\n"
        f"{_SAFETY_PROMPT}\n\n"
        f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}\n"
        f"USER_QUERY: {payload.question}\n"
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
                refusal=OutcomeDetail(code=reason, message=_REFUSAL_MESSAGES[reason]),
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
    "GENERAL_MAX_OUTPUT_TOKENS",
    "GENERAL_MODEL_ID",
    "GENERAL_PROMPT_VERSION",
    "GeneralAnswer",
    "GeneralCapabilityAdapter",
    "build_general_prompt",
    "general_generation_config",
    "validate_general_answer",
]
