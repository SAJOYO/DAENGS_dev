"""피부 판정 해설 서브에이전트 (D-079).

보호자가 스크리닝 결과 화면에서 이어 묻는 질문("이거 병원 가야 해?", "다시 찍으라는 게 무슨
뜻이야?")에, **이미 끝난 판정**이 무슨 뜻인지 풀고 다음 행동을 고른다. 판정을 새로 내지 않는다 —
사진도 모델 출력도 여기 오지 않고, 오는 것은 `SkinPayload` 의 판정 종류 · 경과일 · 이전 판정뿐이다.

설계의 중심은 General(`general.py`)과 같이 **무엇을 말하면 안 되는가**다. 다만 막는 방법이 한 겹
더 있다:

1. **모르는 것은 말할 수 없다.** payload 에 병변 이름 · 확률 · 통제 문구의 칸이 없다 (불변식 15,
   D-023 — 2단계 병변명 holdout 오답 56.6%, `stage1` 보정 전). 프롬프트 금지보다 앞선 방어다.
2. **행동은 닫힌 집합이고 문장은 코드가 쓴다.** 모델은 `retake` · `vet_visit` · `observe` 중에서
   고르기만 하고, 사용자에게 나가는 행동 문장 · 고지 · 거절 문구는 `redirects.py` 의 고정 문장이다
   (#278 — 제품 문장은 생성물이 아니다).
3. **안전 규칙은 모델이 아니라 코드가 지킨다** (`plan_actions`). `abnormal` 이면 모델이 무엇을
   골랐든 `vet_visit` 이 맨 앞이고 `observe` 는 빠진다 — 놓친 이상이 과잉 권유보다 비싸다(비대칭
   오류 비용). `retake` 이면 `retake` 가 맨 앞이고 `observe` 는 빠진다 — "판정 못 함" 은 "괜찮음"
   이 아니다.
4. **해설 문장도 한 번 거른다** (`speaks_beyond_screening`). 모델이 보호자 질문에서 병변 이름을
   따라 쓰거나 확률을 지어내면, 그 문장을 버리고 판정 요약 고정 문장으로 바꾼다. 요청을 실패시키지
   않는다 — 행동과 고지는 여전히 맞기 때문이다. 바뀌었다는 사실은 `data["guarded"]` 로 남긴다.

이력(`history`)은 "이전 기록이 있고 그때 이렇게 나왔다" 까지만이다. 좋아졌다 · 나빠졌다는 두
사진의 차이가 강아지의 변화인지 모델의 잡음인지 모르므로 계산하지 않는다 (`ScreeningHistory`).

프로바이더 · 출력 실패는 다른 어댑터와 같이 `ERROR`/`TIMEOUT` 이다. 다른 능력으로 조용히 폴백하지
않는다 (O-10). 클라이언트는 의미 라우터의 것(`semantic._gemini_client`)을 같이 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import re
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
    OutcomeDetail,
    SkinPayload,
)
from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    SKIN_ACTION_MESSAGES,
    SKIN_REFERENCE_NOTICE,
    SKIN_VERDICT_SUMMARY,
    RefusalReason,
    SkinAction,
)
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    _gemini_client,
)

# v1 (D-079): 첫 판본. 규칙 문장이나 `SkinGuidance` 스키마가 한 글자라도 바뀌면 올린다 —
# 스키마가 프롬프트 본문에 그대로 들어가므로 칸 하나가 늘어도 본문이 달라진다.
SKIN_PROMPT_VERSION = "skin-guide-ko-v1"
SKIN_MODEL_ID = ROUTER_MODEL_ID
SKIN_MAX_OUTPUT_TOKENS = 512

#: 해설 문장에 나오면 안 되는 병변 어휘. **`daengs_screening.config.CLASS_KO` 의 사본이다** —
#: 오케스트레이션이 스크리닝 패키지를 import 하면 도메인 어휘에 묶인다(`aggregate._SCREENING_VERDICTS`
#: 와 같은 이유). 사본끼리는 `tests/test_orchestration_skin_agent.py` 가 대조한다 (#269 와 같은 장치).
#: 뒤의 여섯은 모델 클래스에는 없지만 보호자가 흔히 묻고 모델이 따라 쓰기 쉬운 원인 · 병명이다.
_LESION_TERMS = (
    "구진",
    "플라크",
    "비듬",
    "각질",
    "상피성잔고리",
    "태선화",
    "과다색소침착",
    "농포",
    "여드름",
    "미란",
    "궤양",
    "결절",
    "종괴",
    "피부염",
    "아토피",
    "습진",
    "곰팡이",
    "모낭충",
    "종양",
)
#: 확률 · 수치 판단. 이 능력은 확률을 받지 않으므로 숫자가 붙은 퍼센트는 전부 지어낸 것이다.
_PROBABILITY = re.compile(r"\d+(?:\.\d+)?\s*(?:%|퍼센트|프로)|확률")

_POLICY = (
    "You are the skin-screening guide of a Korean dog-care app. The owner has just seen a skin "
    "screening result for their dog on screen and asks a follow-up question. Explain in plain "
    "words what the result means and choose what the owner should do next.\n\n"
    "The only facts you have are in SCREENING: verdict and days_ago.\n"
    "- normal: the photo showed nothing notable. It does not guarantee the skin is healthy.\n"
    "- abnormal: the screening flagged something worth showing a vet. It is not a diagnosis "
    "and says nothing about how serious it is.\n"
    "- retake: the photo could not be judged. That is neither a good nor a bad result.\n"
    "You do NOT know what lesion it is, where it is, how likely it is, or how serious it is.\n\n"
    "Hard rules:\n"
    "1. Never name, guess, or list any skin disease, lesion type, cause, parasite, allergy, or "
    "infection, and never give a probability or a percentage. Do not repeat such words from the "
    "owner's question either.\n"
    '2. If the owner asks what disease it is, what caused it, or how serious it is: kind "refuse", '
    'reason "diagnosis".\n'
    "3. Medicine, ointment, shampoo or supplement names, doses, or how to treat it: "
    'kind "refuse", reason "medication".\n'
    "4. Signs of an emergency (trouble breathing, collapse, seizure, heavy bleeding, sudden facial "
    'swelling): kind "refuse", reason "emergency".\n'
    '5. Not about dogs: kind "refuse", reason "off_topic".\n'
    "6. HISTORY lists earlier screenings of the same dog, newest first. You may say that earlier "
    "records exist and what each concluded. Never say the skin improved, got worse or progressed, "
    "and never compare records: every photo is different.\n"
    "7. Do not reassure the owner that the dog is fine, and do not alarm them.\n\n"
    "Output:\n"
    '- kind "guide": text is 2-3 short Korean sentences that answer the owner '
    "within these rules.\n"
    '  Write every sentence in 해요체, ending in "-요" (for example "-예요", '
    '"-어요", "-세요"). Never end a sentence in "-습니다" or "-입니다".\n'
    "  text explains what the verdict means for the owner's question. It must NOT tell "
    "the owner how to take the photo, when to see a vet, or what to watch for, and must "
    "NOT add a disclaimer: the app appends fixed sentences for the chosen actions and a "
    "disclaimer after text.\n"
    '  actions lists 1-3 of "retake" (take the photo again), "vet_visit" (see a vet), '
    '"observe" (watch for a few days), most important first.\n'
    '- kind "refuse": text is "", actions is [], and reason is set.\n'
    "Return exactly one JSON object that matches SKIN_GUIDANCE_JSON_SCHEMA."
)


class SkinGuidance(BaseModel):
    """모델 출력의 전부. 여기 없는 칸은 모델이 쓸 수 없다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["guide", "refuse"]
    # **필수 필드다** — `GeneralAnswer.text` 와 같은 이유(기본값을 두면 스키마에서 선택 필드가
    # 되고 제약 디코딩이 칸을 통째로 뺀다, #279 라이브 확인). 거절일 때는 "" 이다.
    text: str = Field(max_length=600)
    #: 모델이 고른 순서. **그대로 나가지 않는다** — `plan_actions` 가 판정별 규칙으로 고친다.
    actions: list[SkinAction] = Field(max_length=3)
    reason: RefusalReason | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> SkinGuidance:
        if self.kind == "guide":
            if not self.text.strip():
                raise ValueError("a guide needs text")
            if self.reason is not None:
                raise ValueError("a guide carries no refusal reason")
        elif self.reason is None:
            raise ValueError("a refusal needs a reason")
        return self


def build_skin_prompt(payload: SkinPayload) -> str:
    """규칙 · 스키마 · 판정 · 이력 · 원문 순서. 이력이 없으면 `HISTORY: []` 한 줄이 남는다 —
    "이력 없음" 과 "이력 줄이 빠진 프롬프트" 를 모델이 헷갈리지 않게."""
    schema = json.dumps(SkinGuidance.model_json_schema(), ensure_ascii=False, sort_keys=True)
    screening = json.dumps(
        payload.screening.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    entries = (
        [entry.model_dump(mode="json") for entry in payload.history.entries]
        if payload.history is not None
        else []
    )
    history = json.dumps(entries, ensure_ascii=False, sort_keys=True)
    # 인접 리터럴의 암묵적 연결에 기대지 않는다 — `general.build_general_prompt` 와 같은 이유.
    return (
        f"PROMPT_VERSION: {SKIN_PROMPT_VERSION}\n\n"
        + _POLICY
        + "\n\n"
        + f"SKIN_GUIDANCE_JSON_SCHEMA:\n{schema}\n\n"
        + f"SCREENING: {screening}\n"
        + f"HISTORY: {history}\n"
        + f"USER_QUERY: {payload.question}\n"
    )


def skin_generation_config() -> Any:
    """Lazy for the same reason as ``semantic.router_generation_config``."""
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=SKIN_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_json_schema=SkinGuidance.model_json_schema(),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        response = _gemini_client().models.generate_content(
            model=SKIN_MODEL_ID,
            contents=prompt,
            config=skin_generation_config(),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


def validate_skin_guidance(raw: object) -> SkinGuidance | None:
    """Schema-validate provider output; invalid raw output is never surfaced."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, SkinGuidance):
        return parsed
    try:
        return SkinGuidance.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


def plan_actions(verdict: str, chosen: list[SkinAction]) -> list[SkinAction]:
    """모델이 고른 행동을 판정별 규칙으로 고친다. **이 함수가 이 능력의 안전 규칙이다.**

    - `abnormal`: `vet_visit` 이 맨 앞, `observe` 는 뺀다. 이상 소견에 "지켜보세요" 만 남는 것이
      이 능력이 낼 수 있는 가장 나쁜 답이다.
    - `retake`: `retake` 가 맨 앞, `observe` 는 뺀다. 판정을 못 한 사진은 지켜볼 근거가 아니다.
    - `normal`: 고른 대로 둔다(진료 권유가 섞여도 막지 않는다 — 과잉 권유는 싼 쪽의 오류다).
      아무것도 안 골랐으면 `observe`.

    같은 행동이 두 번 나오면 첫 자리만 남긴다.
    """
    unique: list[SkinAction] = []
    for action in chosen:
        if action not in unique:
            unique.append(action)
    if verdict == "abnormal":
        return ["vet_visit", *(a for a in unique if a not in ("vet_visit", "observe"))]
    if verdict == "retake":
        return ["retake", *(a for a in unique if a not in ("retake", "observe"))]
    return unique or ["observe"]


def speaks_beyond_screening(text: str) -> bool:
    """해설이 판정이 말하지 않은 것(병변 이름 · 원인 · 확률)을 말했나."""
    return any(term in text for term in _LESION_TERMS) or _PROBABILITY.search(text) is not None


def render_guidance(text: str, actions: list[SkinAction]) -> str:
    """해설 → 다음 행동(고정 문장) → 고지(고정 문장). 순서는 "아는 것을 먼저" 의 규칙이다."""
    steps = "\n".join(f"· {SKIN_ACTION_MESSAGES[action]}" for action in actions)
    return f"{text}\n\n{steps}\n\n{SKIN_REFERENCE_NOTICE}"


class SkinCapabilityAdapter:
    capability = CapabilityName.SKIN

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # nothing downstream consumes trace context here
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, SkinPayload):
            return self._error(started, "invalid_payload", "피부 해설 요청이 올바르지 않습니다.")

        try:
            raw = await self._generate(build_skin_prompt(payload))
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="skin_timeout", detail="피부 해설 응답 시간이 초과됐습니다."
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception:  # noqa: BLE001 - contain the provider like every other adapter
            return self._error(
                started, "skin_provider_failure", "피부 해설 기능 실행에 실패했습니다."
            )

        guidance = validate_skin_guidance(raw)
        if guidance is None:
            return self._error(
                started, "skin_invalid_output", "피부 해설 결과를 해석할 수 없습니다."
            )
        if guidance.kind == "refuse":
            reason = guidance.reason or "diagnosis"
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.REFUSED,
                refusal=OutcomeDetail(code=reason, message=SCOPED_REDIRECT_MESSAGES[reason]),
                elapsed_ms=_elapsed_ms(started),
            )

        verdict = payload.screening.verdict
        actions = plan_actions(verdict, guidance.actions)
        text = guidance.text.strip()
        guarded = speaks_beyond_screening(text)
        if guarded:
            # 문장을 고치지 않고 통째로 바꾼다 — 어느 부분이 문제인지 코드가 오려 내면 남은
            # 문장이 무슨 뜻이 될지 아무도 보증하지 못한다.
            text = SKIN_VERDICT_SUMMARY[verdict]
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": render_guidance(text, actions),
                # 앱이 버튼으로 그릴 수 있게 기계용으로도 싣는다. 문장은 `answer` 에 이미 있다.
                "actions": list(actions),
                "verdict": verdict,
                "guarded": guarded,
            },
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
    "SKIN_MAX_OUTPUT_TOKENS",
    "SKIN_MODEL_ID",
    "SKIN_PROMPT_VERSION",
    "SkinCapabilityAdapter",
    "SkinGuidance",
    "build_skin_prompt",
    "plan_actions",
    "render_guidance",
    "skin_generation_config",
    "speaks_beyond_screening",
    "validate_skin_guidance",
]
