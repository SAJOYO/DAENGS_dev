"""보행 **변화 관찰** 해설 서브에이전트 (D-080).

보호자가 비교 결과 화면에서 이어 묻는 질문("지난번이랑 뭐가 달라?" · "왼쪽이 왜 변했다는
거야?" · "왜 비교가 안 돼?")에, **이미 계산된 비교**를 사용자 말로 풀고 다음 행동을 고른다.
영상도 관절 좌표도 여기 오지 않고, 오는 것은 `GaitCompareContext` 의 갈래 · 다리 · 잰 수 ·
간격 · 신뢰도 · 버전 일치 여부뿐이다.

**피부 해설(D-079)과 배선은 같지만 목적이 다르다.** 저쪽은 판정 한 건의 해설이고 이쪽은
같은 아이의 **시간 변화 관찰**이다 — 이 서비스는 진단이 아니라 같은 개체의 변화를 보는
도구다 (D-058). 그 차이가 세 곳에서 드러난다:

1. **행동 집합에 진료 권유가 없다.** `same_condition_retake` · `keep_observing` ·
   `check_conditions` 셋뿐이다. 걸음 비교에서 병원을 권하기 시작하면 관찰이 판정으로
   되돌아간다. 병원이 필요한 질문은 행동이 아니라 **거절**로 가고, 그때
   `SCOPED_REDIRECT_MESSAGES["diagnosis"]` 의 둘째 문장이 진료를 안내한다.
2. **방향을 말하지 않는다.** 좋아졌다 · 나빠졌다 · 호전 · 악화는 어느 경로로도 안 나간다.
   판정 계산 자체가 방향을 말하지 않는 것과 같은 이유다 (`compare.direction_note` — 표본이
   작을 때 관절별 비율이 크게 흩어진다). 피부의 "병변 이름 금지" 에 해당하는 자리다.
3. **비교를 못 했을 때도 답한다.** 사용자가 비교 화면에서 눌러 들어왔으므로, 조용히 다른
   능력으로 넘기지 않고 이유 범주별 고정 문구로 닫는다 (`ABSTAINED`). 그 경로에서는 모델을
   아예 안 태운다.

막는 방법은 피부와 같은 네 겹이다: payload 에 칸이 없고(수치 · 관절 이름 · 방향), 행동은
닫힌 집합이며, 행동 순서는 코드가 정하고(`plan_gait_actions`), 해설 문장도 한 번 거른다
(`speaks_beyond_change`).

프로바이더 · 출력 실패는 다른 어댑터와 같이 `ERROR`/`TIMEOUT` 이다. 다른 능력으로 조용히
폴백하지 않는다 (O-10). 클라이언트는 의미 라우터의 것을 같이 쓴다.
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
    GaitCompareContext,
    GaitComparePayload,
    OutcomeDetail,
)
from daengs_backend.orchestration.redirects import (
    GAIT_ACTION_MESSAGES,
    GAIT_CHANGE_SUMMARY,
    GAIT_EXPERT_ADVISORY,
    GAIT_REFERENCE_NOTICE,
    GAIT_UNAVAILABLE_MESSAGES,
    GAIT_VERSION_WARNING,
    SCOPED_REDIRECT_MESSAGES,
    GaitAction,
    RefusalReason,
)
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    _gemini_client,
)

# v1 (D-080): 첫 판본. 규칙 문장이나 `GaitGuidance` 스키마가 한 글자라도 바뀌면 올린다 —
# 스키마가 프롬프트 본문에 그대로 들어가므로 칸 하나가 늘어도 본문이 달라진다.
#: v2 (#576): 보호자가 **이미 받은** 진단을 말하는 경우를 규칙 8 로 갈라냈다. v1 실측
#: (`evals/gait_change/report_gc_v1.md`)에서 그 질문이 **24/24 전부 거절**이었다 — 규칙 3 이
#: "무슨 병인지 물으면 거절" 이라 질문 본문에 병명이 들어오는 순간 함께 밀렸다.
#: v3 (#582): `expert_advisory` 설명 한 줄이 "여섯 지점 전부" 에서 "잰 지점 전부" 로 바뀌었다.
#: 조건 자체는 서버(`services/gait_context._expert_advisory`)가 계산하고 모델은 결과만 받지만,
#: **본문이 바뀌면 버전을 올린다** — 그러지 않으면 새 결과가 옛 셀에 섞인다.
#: **평가 메타가 이 값을 고정한다.**
GAIT_PROMPT_VERSION = "gait-change-ko-v3"
GAIT_MODEL_ID = ROUTER_MODEL_ID
GAIT_MAX_OUTPUT_TOKENS = 512

#: 해설에 나오면 안 되는 **방향** 어휘. 이 능력의 핵심 금지다 — 비교가 방향을 말하지 않는데
#: 해설이 말하면, 계산이 하지 않은 판단을 문장이 해 버린다.
#:
#: ⚠️ **활용형을 어간으로 잡는다.** 낱말을 그대로 나열하면 "나빠졌어요" 는 걸리는데
#:    "나빠진 것 같아요" 는 안 걸린다 — `나빠지` 가 `나빠진` 의 부분문자열이 아니기
#:    때문이다(지 ≠ 진). 실제로 처음 구현이 그렇게 뚫렸고 테스트가 잡았다.
_DIRECTION = re.compile(
    r"좋아[지져졌진질]|나빠[지져졌진질]|나아[지져졌진질]|심해[지져졌진질]"
    r"|진행[되돼됐된될]|호전|악화|개선|회복|완화"
)
#: 진단 · 병명 어휘. **관절 이름(고관절 · 무릎 · 뒷발)은 여기 없다** — 앱 표가 이미 그 말로
#: 줄을 그리므로 해설이 같은 말을 쓰는 것은 문제가 아니다. 막는 것은 병명과 증상 판단이다.
_DIAGNOSIS_TERMS = (
    "절뚝",
    "파행",
    "관절염",
    "슬개골",
    "탈구",
    "디스크",
    "십자인대",
    "이형성",
    "골절",
    "염좌",
    "마비",
    "통증",
    "염증",
    "진단",
    "질환",
    "병명",
)
#: 진료 권유. **행동 집합에서 뺀 것을 문장으로 우회하지 못하게 막는다** — 병원이 필요한
#: 질문은 거절(`diagnosis`)로 가고, 그 고정 문구가 진료를 안내한다.
_VET_TERMS = ("수의사", "동물병원", "병원", "진료", "내원")
#: 수치. 이 능력은 관절 이동범위를 받지 않으므로 단위가 붙은 숫자는 전부 지어낸 것이다.
#: 잰 관절 수(`3개 중 2개`)까지 막지 않으려고 **단위가 붙은 것만** 잡는다.
_MEASUREMENT = re.compile(r"\d+(?:\.\d+)?\s*(?:px|픽셀|%|퍼센트|프로|mm|cm|도)|이동범위")
#: 방향을 **말하지 않는다고 밝히는** 표현 (#576). 이 말 **앞**에 있는 방향어는 주장이 아니다.
#: 목록을 넓히면 진짜 방향 주장이 새므로, 방향을 부인하는 꼴로만 쓰이는 말만 넣는다.
_DIRECTION_DISCLAIMER = re.compile(
    r"의미는\s*아니|뜻은\s*아니|것은\s*아니|것이\s*아니|말하는\s*것은\s*아니"
    r"|말할\s*수\s*(?:는\s*)?없|알\s*수\s*(?:는\s*)?없"
    # `판단하지 않아요` 꼴. **`-지 않` 을 통째로 잡지 않는다** — "좋아지지 않았어요" 가
    # 그것으로 면제되는데 그건 방향 주장이다. 방향을 **부인하는 동사**만 골라 적는다.
    r"|(?:판단|말|의미|나타내|뜻|보여주)하?지\s*(?:는\s*)?않"
)
#: 문장 경계. 마침표가 없는 한 줄짜리 해설도 한 문장으로 다룬다.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_POLICY = (
    "You are the gait-change guide of a Korean dog-care app. The owner has just seen a "
    "comparison between two gait videos of the same dog and asks a follow-up question. "
    "Explain in plain words what the comparison showed and choose what the owner should do next.\n\n"
    "This feature observes change in one dog over time. It is NOT a diagnosis and never was.\n\n"
    "The only facts you have are in COMPARISON:\n"
    "- change_kind no_change: the measured joints moved about the same in both videos.\n"
    "- change_kind one_side: on one hind leg several joints differed together.\n"
    "- change_kind both_sides: both hind legs differed, which more often means the two "
    "videos were filmed differently than that the dog changed.\n"
    "- change_kind not_enough: too few joints could be measured in both videos to say "
    "anything about change. This is not reassurance.\n"
    "- flagged_sides names which hind leg(s) differed: left, right, or none.\n"
    "- left_measured/left_joints and right_measured/right_joints: how many joints could "
    "actually be measured out of those compared on that leg.\n"
    "- days_between: days between the two recordings.\n"
    "- reliability: which video had too little usable walking (recent, past, both, or ok).\n"
    "- version_mismatch: the two records were analysed by different versions.\n"
    "- expert_advisory: every joint point that could be measured differed, there were "
    "enough of them, and the comparison was reliable enough to say so. It does NOT mean "
    "the change is severe, worsening, or a disease. "
    "When it is true the app appends one fixed sentence saying the cause cannot be known "
    "from video and that an expert opinion is worth considering if this repeats — do not "
    "write that yourself and do not escalate it.\n\n"
    "You do NOT know which direction anything moved, by how much, what the dog's condition "
    "is, or whether it is getting better or worse.\n\n"
    "Hard rules:\n"
    "1. Never say the dog improved, worsened, recovered or progressed, and never compare "
    "the two videos as better/worse. The comparison deliberately does not measure direction.\n"
    "2. Never name or guess a disease, injury or symptom (limping, arthritis, luxation, "
    "pain, inflammation and the like), and never give numbers with units. Do not repeat "
    "such words from the owner's question either.\n"
    "3. Never tell the owner to see a vet, and do not mention vets, clinics or treatment. "
    "If the owner asks whether to see a vet, what illness this is, what caused it, or how "
    'serious it is: kind "refuse", reason "diagnosis".\n'
    '4. Medicine, supplements, doses or treatment: kind "refuse", reason "medication".\n'
    "5. Signs of an emergency (collapse, seizure, heavy bleeding, trouble breathing, a leg "
    'that cannot bear weight at all): kind "refuse", reason "emergency".\n'
    '6. Not about dogs: kind "refuse", reason "off_topic".\n'
    "7. Do not reassure the owner that the dog is fine, and do not alarm them.\n"
    '8. If the owner reports a diagnosis they have ALREADY been given ("the vet said it is '
    'X", "we were told at the clinic that ..."), that is NOT a request to diagnose: do NOT '
    "refuse. Take it as context and answer their question about the comparison. Within that: "
    "do not confirm or deny the diagnosis, do not repeat the name of the condition, give no "
    "advice specific to it, and do NOT link it to this comparison as its cause, its evidence "
    "or its confirmation. The comparison only shows whether movement differed between two "
    "videos; it says nothing about why. Say that positively — describe only what the "
    "comparison shows. Do NOT write words for illness, condition, disease or diagnosis at "
    'all, not even to deny a link: "이 비교는 움직임의 차이만 보여줘요" is the right shape, '
    '"질환과는 관련이 없어요" is not.\n\n'
    "Output:\n"
    '- kind "guide": text is 2-3 short Korean sentences that answer the owner within these '
    "rules.\n"
    '  Write every sentence in 해요체, ending in "-요" (for example "-예요", "-어요", '
    '"-세요"). Never end a sentence in "-습니다" or "-입니다".\n'
    "  text explains what the comparison means for the owner's question. It must NOT tell "
    "the owner how to film the next video, what to watch for, or add a disclaimer: the app "
    "appends fixed sentences for the chosen actions and a disclaimer after text.\n"
    '  actions lists 1-3 of "same_condition_retake" (film again under the same conditions), '
    '"keep_observing" (film again next time and watch the trend), "check_conditions" (the '
    "two videos may have been filmed differently), most important first.\n"
    '- kind "refuse": text is "", actions is [], and reason is set.\n'
    "Return exactly one JSON object that matches GAIT_GUIDANCE_JSON_SCHEMA."
)


class GaitGuidance(BaseModel):
    """모델 출력의 전부. 여기 없는 칸은 모델이 쓸 수 없다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["guide", "refuse"]
    # **필수 필드다** — 기본값을 두면 스키마에서 선택 필드가 되고 제약 디코딩이 칸을 통째로
    # 뺀다 (`SkinGuidance.text` 와 같은 이유, #279 라이브 확인). 거절일 때는 "" 이다.
    text: str = Field(max_length=600)
    #: 모델이 고른 순서. **그대로 나가지 않는다** — `plan_gait_actions` 가 갈래별 규칙으로 고친다.
    actions: list[GaitAction] = Field(max_length=3)
    reason: RefusalReason | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> GaitGuidance:
        if self.kind == "guide":
            if not self.text.strip():
                raise ValueError("a guide needs text")
            if self.reason is not None:
                raise ValueError("a guide carries no refusal reason")
        elif self.reason is None:
            raise ValueError("a refusal needs a reason")
        return self


def build_gait_prompt(payload: GaitComparePayload) -> str:
    """규칙 · 스키마 · 비교 · 원문 순서.

    비교가 없는 요청(`unavailable`)은 여기 오지 않는다 — 어댑터가 그 전에 고정 문구로 닫는다.
    """
    if payload.compare is None:  # pragma: no cover - 어댑터가 먼저 막는다
        raise ValueError("a gait prompt needs a comparison")
    schema = json.dumps(GaitGuidance.model_json_schema(), ensure_ascii=False, sort_keys=True)
    comparison = json.dumps(
        payload.compare.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    # 인접 리터럴의 암묵적 연결에 기대지 않는다 — `build_skin_prompt` 와 같은 이유.
    return (
        f"PROMPT_VERSION: {GAIT_PROMPT_VERSION}\n\n"
        + _POLICY
        + "\n\n"
        + f"GAIT_GUIDANCE_JSON_SCHEMA:\n{schema}\n\n"
        + f"COMPARISON: {comparison}\n"
        + f"USER_QUERY: {payload.question}\n"
    )


def gait_generation_config() -> Any:
    """Lazy for the same reason as ``semantic.router_generation_config``."""
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=GAIT_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_json_schema=GaitGuidance.model_json_schema(),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        response = _gemini_client().models.generate_content(
            model=GAIT_MODEL_ID,
            contents=prompt,
            config=gait_generation_config(),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


def validate_gait_guidance(raw: object) -> GaitGuidance | None:
    """Schema-validate provider output; invalid raw output is never surfaced."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, GaitGuidance):
        return parsed
    try:
        return GaitGuidance.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


def plan_gait_actions(compare: GaitCompareContext, chosen: list[GaitAction]) -> list[GaitAction]:
    """모델이 고른 행동을 비교 갈래별 규칙으로 고친다. **이 함수가 이 능력의 안전 규칙이다.**

    앞자리는 코드가 정한다 (겹치면 아래 순서대로 둘 다 앞에 선다):

    - `not_enough`: `same_condition_retake` 가 맨 앞, `keep_observing` 은 뺀다. 못 잰 비교를
      "다음에 또 찍어 흐름을 보자" 로 닫으면, 잴 수 없었다는 사실이 관찰 계획으로 덮인다.
    - `version_mismatch` 또는 `both_sides`: `check_conditions` 가 앞. 둘 다 "걸음이 달라진 것"
      보다 **찍은 조건이 달랐을 가능성**을 먼저 보라는 자리다 (`compare_v4` 의 both_sides
      문구와 같은 판단).

    나머지는 모델이 고른 순서를 지킨다. 같은 행동이 두 번 나오면 첫 자리만 남긴다.
    """
    unique: list[GaitAction] = []
    for action in chosen:
        if action not in unique:
            unique.append(action)

    leading: list[GaitAction] = []
    dropped: set[GaitAction] = set()
    if compare.change_kind == "not_enough":
        leading.append("same_condition_retake")
        dropped.add("keep_observing")
    if compare.version_mismatch or compare.change_kind == "both_sides":
        leading.append("check_conditions")

    rest = [a for a in unique if a not in leading and a not in dropped]
    return [*leading, *rest] or ["keep_observing"]


def _claims_direction(text: str) -> bool:
    """방향을 **주장**했나. 방향을 말하지 않는다고 밝힌 문장은 주장이 아니다 (#576).

    v1 실측에서 가드가 이 문장을 통째로 갈아 치웠다:

        이 결과는 움직임의 변화를 나타낼 뿐, 상태가 좋아지거나 나빠졌다는 의미는 아니에요.

    방향을 말하지 않는다고 **명시한** 문장인데 `좋아지`·`나빠졌` 만 보고 반응했다. 사용자에게
    더 나은 문장이 사라지고 일반 요약으로 대체됐다 — 가드가 좁아서가 아니라 넓어서 생긴 일이다.

    그래서 문장 단위로 보되 **면제 조건을 좁게** 둔다: 그 문장의 방향어가 **전부** 부정 표현
    앞에 있어야 한다. 부정 뒤에 방향어가 또 나오면 그것은 진짜 주장이다 —
    "좋아졌는지 말할 수는 없지만, 확실히 나아졌어요" 가 그 모양이고, 문장에 부정이 있다는
    것만으로 봐주면 이런 문장이 그대로 나간다.

    ⚠️ **이 면제는 방향어에만 준다.** 병명 · 진료 · 수치는 부정해도 해가 남는다 —
    "관절염은 아니에요" 는 여전히 병명 판단이고, "병원 갈 필요 없어요" 는 여전히 진료
    조언이며, 부정된 수치도 수치다. 실측으로 확인된 오탐도 방향어뿐이었다.
    """
    for sentence in _SENTENCE_SPLIT.split(text):
        hits = list(_DIRECTION.finditer(sentence))
        if not hits:
            continue
        disclaimer = _DIRECTION_DISCLAIMER.search(sentence)
        if disclaimer is None or hits[-1].start() > disclaimer.start():
            return True
    return False


def speaks_beyond_change(text: str) -> bool:
    """해설이 비교가 말하지 않은 것(방향 · 병명 · 진료 권유 · 수치)을 말했나."""
    return (
        _claims_direction(text)
        or any(term in text for term in _DIAGNOSIS_TERMS)
        or any(term in text for term in _VET_TERMS)
        or _MEASUREMENT.search(text) is not None
    )


def render_guidance(
    text: str,
    actions: list[GaitAction],
    *,
    version_mismatch: bool,
    expert_advisory: bool = False,
) -> str:
    """해설 → 다음 행동(고정 문장) → 전문가 의견 한 줄 → 버전 경고 → 고지(고정 문장).

    버전 경고는 **조건이 맞으면 무조건** 실린다 — 앱도 같은 상황에서 서버 `version_warning`
    을 띄우므로, 같은 사실이 두 화면에서 같은 무게로 보여야 한다.

    전문가 의견 줄도 **모델이 쓰지 않는다.** 제품 문장이라 코드가 쓴다 (#278) — 그래야 조건이
    맞을 때 빠짐없이 나가고, 아닐 때 새어 나오지 않는다. 기본 고지(`GAIT_REFERENCE_NOTICE`)
    는 그대로 맨 끝이다: 이 줄이 그것을 대체하지 않는다.
    """
    steps = "\n".join(f"· {GAIT_ACTION_MESSAGES[action]}" for action in actions)
    notes = [GAIT_EXPERT_ADVISORY] if expert_advisory else []
    if version_mismatch:
        notes.append(GAIT_VERSION_WARNING)
    tail = "".join(f"{note}\n\n" for note in notes)
    return f"{text}\n\n{steps}\n\n{tail}{GAIT_REFERENCE_NOTICE}"


class GaitCapabilityAdapter:
    capability = CapabilityName.GAIT

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # nothing downstream consumes trace context here
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, GaitComparePayload):
            return self._error(started, "invalid_payload", "보행 해설 요청이 올바르지 않습니다.")

        if payload.unavailable is not None:
            # **모델을 안 태운다.** 비교가 없으면 해설할 것도 없고, 이유는 이미 서버가 안다.
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                abstention=OutcomeDetail(
                    code=payload.unavailable,
                    message=GAIT_UNAVAILABLE_MESSAGES[payload.unavailable],
                ),
                elapsed_ms=_elapsed_ms(started),
            )

        compare = payload.compare
        if compare is None:  # pragma: no cover - 계약이 먼저 막는다
            return self._error(started, "invalid_payload", "보행 해설 요청이 올바르지 않습니다.")

        try:
            raw = await self._generate(build_gait_prompt(payload))
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="gait_timeout", detail="보행 해설 응답 시간이 초과됐습니다."
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception:  # noqa: BLE001 - contain the provider like every other adapter
            return self._error(
                started, "gait_provider_failure", "보행 해설 기능 실행에 실패했습니다."
            )

        guidance = validate_gait_guidance(raw)
        if guidance is None:
            return self._error(
                started, "gait_invalid_output", "보행 해설 결과를 해석할 수 없습니다."
            )
        if guidance.kind == "refuse":
            reason = guidance.reason or "diagnosis"
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.REFUSED,
                refusal=OutcomeDetail(code=reason, message=SCOPED_REDIRECT_MESSAGES[reason]),
                elapsed_ms=_elapsed_ms(started),
            )

        actions = plan_gait_actions(compare, guidance.actions)
        text = guidance.text.strip()
        guarded = speaks_beyond_change(text)
        if guarded:
            # 문장을 고치지 않고 통째로 바꾼다 — 어느 부분이 문제인지 코드가 오려 내면 남은
            # 문장이 무슨 뜻이 될지 아무도 보증하지 못한다 (`adapters/skin.py` 와 같은 판단).
            text = GAIT_CHANGE_SUMMARY[compare.change_kind]
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": render_guidance(
                    text,
                    actions,
                    version_mismatch=compare.version_mismatch,
                    expert_advisory=compare.expert_advisory,
                ),
                # 앱이 버튼으로 그릴 수 있게 기계용으로도 싣는다. 문장은 `answer` 에 이미 있다.
                "actions": list(actions),
                "change_kind": compare.change_kind,
                # 앱이 이 줄을 따로 그리고 싶을 때를 위해 기계용으로도 싣는다. 문장은
                # `answer` 에 이미 있고, 이 값이 켜져도 `actions` 는 바뀌지 않는다.
                "expert_advisory": compare.expert_advisory,
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
    "GAIT_MAX_OUTPUT_TOKENS",
    "GAIT_MODEL_ID",
    "GAIT_PROMPT_VERSION",
    "GaitCapabilityAdapter",
    "GaitGuidance",
    "build_gait_prompt",
    "gait_generation_config",
    "plan_gait_actions",
    "render_guidance",
    "speaks_beyond_change",
    "validate_gait_guidance",
]
