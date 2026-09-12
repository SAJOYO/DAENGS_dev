"""General-answer fallback: a bounded Gemini generation behind a safety prompt (#279).

This is the capability the planner assembles when the router selected nothing — the
questions that until now ended as "실행하거나 안내할 수 있는 기능이 없습니다". It answers
without evidence, so the whole design is about what it is **not allowed** to say:

- No diagnosis, no drug name recommendations, no dosages, no administration instructions, no
  side-effect information or judgment, no emergency handling beyond "go to a vet now" — how
  long or how often an existing medication is given is answerable (D-071).
- No claims about laws, fees, deadlines, or numbers — those need evidence, and the
  assistant's institutional-information capability (Life) is where evidence lives.
- Questions unrelated to dogs are politely declined.

The model returns one JSON object: ``kind`` is ``answer``, ``ask``, or ``refuse``. A refusal
carries a **reason category**, and the user-facing redirect for each category is fixed here,
not written by the model — a refusal is the one place model prose must not leak through,
because "무엇에 물어보라 / 수의사" is a product sentence (#278), not a generation. An
adapter that only ever returns OK gives the answer-quality judge (#277) nothing to catch;
``REFUSED`` is what makes the fallback measurable.

``ask`` is #415 (D-068): a question the owner has not specified enough to answer gets one
short question back instead of a refusal. **The question is the model's prose, unlike a
refusal** — a refusal names a product boundary and must read the same every time, while a
question has to follow what the owner actually wrote. The prompt bounds it instead: one
question, observations only, no disease list, no diagnostic checklist. The adapter carries
it as a ``ClarifyRequest`` in ``data["ask"]`` and ``aggregate`` turns that into the existing
``CLARIFY`` — **no status enum grows, and ``RoutePlan.clarify`` is never written after the
fact**, so the exclusivity invariant that CLARIFY has always had stays true.

Provider and output failures are contained as ``ERROR``/``TIMEOUT`` exactly like the other
adapters: the fallback failing must look like a capability failing, never like a crash.

The Gemini client is the semantic router's (``semantic._gemini_client``): same key, same
timeout, one lazily-built client per process. Generation settings reuse the router's
constants where they apply; only the output budget is wider, because a short answer plus
its JSON envelope does not fit in a routing decision's 256 tokens.

``GeneralAnswer.unmeasured`` (D-072, Task 6) is a **marker**, not the fixed sentence itself
— the model sets the flag, the adapter's OK path appends
``redirects.DISTANCE_FROM_RECORDED_WALKS_ONLY`` after ``answer.text`` (#278: a rejection or
a boundary sentence is product copy, code writes it, never the model). A marker was chosen
over checking ``payload.walk_activity is None`` directly because the model already knows,
from the prompt's rule, *whether the question was actually about distance/time* — the
payload alone cannot tell "no activity today" from "not asked about activity at all", and
gluing the sentence onto every answer when records are simply absent would be wrong far
more often than right. ``_WALK_ACTIVITY_RULE`` (D-072, Task 5) is what actually instructs
the model to raise the flag, and it only ever rides in the ``-walk`` prompt body — that
is, when ``payload.walk_activity is not None``. So ``unmeasured`` can come back ``True``
starting with this task, but only for requests that carry a walk-activity summary at all;
a request with no ``walk_activity`` never sees the rule and the field stays unused for it,
exactly as before this task. **To revert this task**: delete ``_WALK_ACTIVITY_RULE`` and
the three call sites that add it (``rule_blocks``, ``context_lines``, the ``-walk`` suffix
in ``general_prompt_version``) — the marker field and the adapter's OK-path branch predate
this task and stay.
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
    ClarifyRequest,
    ErrorDetail,
    GeneralPayload,
    ObservationAxis,
    OutcomeDetail,
)
from daengs_backend.orchestration.redirects import (
    DISTANCE_FROM_RECORDED_WALKS_ONLY,
    SCOPED_REDIRECT_MESSAGES,
    RefusalReason,
)
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    _gemini_client,
    render_conversation_context,
)

# v2 (D-057 ③ⓐ): v1 은 통상 돌봄 기준(급여량 · 음수량)을 institutional · diagnosis 로 사양했다 —
# #277 실측에서 general_care 15건 중 7건이 과잉 거절이었다. v2 는 통상 기준을 "개체차를 단서로
# 범위를 답한다" 로 명시하고, institutional 은 출처 문서가 있어야 하는 사실로, diagnosis 는
# 병명 · 원인 판정 · 검사 해석을 명시적으로 묻는 것으로 좁혔다.
# v3: 같은 규칙을 **영문**으로 옮겼다 — 의미 라우터의 `_POLICY` 와 같은 언어로 두라는 사람 결정.
# 출력 언어(한국어)와 어조는 지시문 안에서 정한다. `ko` 는 출력 언어다.
# v6 (#415 · D-068): 되묻기 규칙 한 문단이 붙고, diagnosis · emergency 두 거절이 좁아졌다.
# **네 조합의 버전이 한꺼번에 올라간 이유는 따로 있다** — `build_general_prompt` 가
# `GeneralAnswer.model_json_schema()` 를 네 가지 전부에 끼워 넣으므로, `kind` 에 `ask` 를
# 더한 순간 v3 · v4 · v5 본문의 **글자가 이미 달라졌다.** 버전을 안 올리면 D-057 ③ 이 84건
# 쌍대 비교로 승인한 이름이 다른 물건을 가리키게 된다. v4 · v5 번호는 건너뛴 것이 아니라
# 그 계보가 v6 으로 함께 올라간 것이다.
# v7 (D-068 후속, 2026-09-12): 되묻기가 멈추지 않던 것을 고쳤다 — 증상 둘 이상이 모이면
# 되묻지 않고 답으로 닫고, 되묻기는 빈 `text` 를 계약으로 막는다. 같은 판본에 원인 단정
# 금지 문장(초록 구토 예시)도 더해졌다 — "일반 기전은 설명, 이 아이 원인 단정은 금지".
# v8 (D-071, 2026-09-12 개정): medication 거절이 좁아졌다 — 약의 기간·투여 간격(이미
# 복용 중인지와 무관하게, 질문이 "언제까지/얼마나 자주"인지로 가른다)은 husbandry norm 과
# 같은 결로 답하고, 이름·용량·복용 방법·새로 시작할지 여부·부작용(정보·판단 모두)은 그대로
# 막는다. 초판은 "이미 복용 중"을 확인해야만 답하게 했다가, 그 확인은 모델이 할 수 없어서
# #446 의 동기 사례 자체가 거절로 되돌아가는 것을 리뷰가 잡았다 — 질문의 형태(기간이냐
# 시작이냐)로 가르는 지금 형태로 고쳤다. 본문 글자가 달라졌으니, 그 글자를 승인한 버전
# 이름도 같이 올린다.
# v9 (D-072 Task 6, 2026-09-12): `_SAFETY_PROMPT` 의 문장은 **한 글자도** 안 바뀌었다 —
# 바뀐 것은 `GENERAL_ANSWER_JSON_SCHEMA` 뿐이다. `GeneralAnswer` 에 `unmeasured` 칸이
# 늘면서 `build_general_prompt` 가 끼워 넣는 `GeneralAnswer.model_json_schema()` 가
# 네 가지 프롬프트 몸 전부에서 달라졌다 — `kind` 에 `ask` 를 더했던 v6 때와 같은 이유고
# 같은 결로 네 상수를 한꺼번에 올린다(위 v6 주석). Task 6 시점에는 모델에게 언제
# `unmeasured` 를 세우라고 시키는 문단이 아직 없었다 — 스키마에 칸만 늘고 모델이 그
# 칸을 세울 이유가 없었다.
# v9-walk (D-072 Task 5, 같은 날 — 순서가 계획서와 뒤집혀 Task 6 뒤에 왔다): 그 문단
# (`_WALK_ACTIVITY_RULE`)이 여기서 붙는다. `payload.walk_activity is not None` 인
# 요청, 즉 `general_prompt_version` 이 `-walk` 접미사를 붙이는 판본에서만 실린다 —
# 그 요청에서는 `unmeasured` 가 실제로 `True` 로 돌아올 수 있다. `walk_activity` 가
# 없는 요청(위 네 상수의 판본)은 이 문단을 안 보므로 지금도 칸은 있지만 세워질 이유가
# 없다.
# D-057 ③ 의 84건 쌍대 비교 승인은 **지시문 텍스트**(`_SAFETY_PROMPT` 등 규칙 문단)에 걸린
# 것이지 스키마 블록에 걸린 것이 아니다 — v6 · v9 처럼 지시문이 안 바뀌고 스키마만 바뀌어
# 버전이 오르는 것은 그 계보를 끊지 않는다. 계보가 끊기는 것은 규칙 문단의 글자가 바뀔 때뿐이다.
GENERAL_PROMPT_VERSION = "general-answer-ko-v9"
# -carelog (#344): 기본 본문에 CARE_LOG_TODAY 규칙 한 문단과 블록 한 줄이 **더해진** 판본.
# 오늘 케어 로그가 payload 에 있을 때만 이 판본이 나가고, 없으면 기본 본문이 글자까지 그대로
# 나간다 — 기본 본문은 D-057 ③ 에서 84건 쌍대 비교 뒤 승인된 계보라, 그 84건(로그 없음)의
# 프롬프트에 로그 규칙이 새지 않게 하려는 분기다. 버전 문자열이 갈리는 이유는 프롬프트
# 텍스트가 다르기 때문이다.
GENERAL_CARE_LOG_PROMPT_VERSION = "general-answer-ko-v9-carelog"
# -vetspend / -carelog-vetspend (#353 Task 7): confirmed vet-visit spend joins the same
# fallback, same rule as care log — a rule paragraph plus a context line, added only when
# the payload carries it. Four combinations of {care_log, vet_spend} exist; the two that
# predate that card (absent/absent, present/absent) are reproduced by the same literal
# strings as before, not reconstructed, so the approved base body cannot drift by refactor.
# The other two get their own version strings because their prompt text differs from both —
# `build_general_prompt` picks the version from exactly which of the two optional blocks
# are present.
GENERAL_VET_PROMPT_VERSION = "general-answer-ko-v9-vetspend"
GENERAL_CARE_LOG_VET_PROMPT_VERSION = "general-answer-ko-v9-carelog-vetspend"
# `-conv` (#416 Task 6): 대화 맥락이 실릴 때 위 네 상수 각각에 붙는 **다섯 번째 갈래**다.
# 새 상수를 또 네 개 두지 않고 접미사로 만드는 이유 — 네 조합은 이미 서로 다른 프롬프트
# 몸을 가리키는데, 맥락 블록은 그 넷 중 어느 것에도 본문을 안 바꾸고 `USER_QUERY:` 앞에
# 한 블록만 얹는다(있으면). 접미사가 "이 몸에 그 블록이 더해졌다" 를 그대로 읽히게 한다.
# `general_prompt_version` 이 이 규칙을 한 곳에서 계산한다.
#: `ClarifyRequest.missing` 이 말하는 것은 **되묻기의 종류**다 — 좌표 게이트의 `location.lat`
#: 과 같은 목록에 관찰 어휘를 섞지 않으려고 값을 하나로 둔다. **무엇을 물었는지는
#: `ClarifyRequest.missing_axes` 가 따로 갖는다** (#416 의 결합 지점).
GENERAL_ASK_MISSING = "observation"
GENERAL_MODEL_ID = ROUTER_MODEL_ID
# 답 문장 3~5개 + JSON 봉투. 라우터의 256 은 분류 한 줄을 위한 예산이라 여기엔 좁다.
GENERAL_MAX_OUTPUT_TOKENS = 512

# 거절 사유별 안내 문구는 [daengs_backend.orchestration.redirects] 에 있다 — `refusal.message`
# 로 그대로 나가고, `refusal.code` 는 사유 범주다. `aggregate.py` 의 빈 선택 FAILED 문구와
# 한 곳에서 관리한다 (#278).


class GeneralAnswer(BaseModel):
    """The complete output surface of the fallback model."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "ask", "refuse"]
    # **필수 필드다.** 기본값 "" 을 두면 JSON 스키마에서 선택 필드가 되고, 제약 디코딩은 그것을
    # 그대로 허용한다 — 실측에서 모델이 `{"kind": "answer", "reason": null}` 로 text 를 통째로
    # 빼고 답해 `general_invalid_output` 이 됐다 (#279 라이브 확인). 거절일 때는 "" 을 낸다.
    text: str = Field(max_length=1_000)
    #: 되묻기의 **질문 한 문장**. `text` 와 나누어 두는 이유는 둘이 가는 곳이 달라서다 —
    #: `text` 는 기록으로 지금 말할 수 있는 것이고, 이 칸만 `ClarifyRequest.question` 이
    #: 된다. 한 칸에 뭉치면 되묻기를 따로 렌더하려는 클라이언트가 요약까지 질문 자리에
    #: 그린다. 길이 제한(500자)은 여기 말고 어댑터가 `ClarifyRequest` 로 조립할 때 건다 —
    #: 검사하는 곳이 하나여야 "여기선 통과했는데 저기서 터진다" 가 안 생긴다.
    question: str | None = None
    #: 그 질문이 **실제로 물은 관찰 항목** 1~2개. 되묻기일 때만 쓴다.
    #: **코드가 채우지 않는다** — 모델이 고른 것만 그대로 나간다. 사용자에게 보이는 문장은
    #: `question` 이고, 이 칸은 `#416` 이 후속 답변을 앞 질문에 묶을 때 읽는 기계용 흔적이다.
    axes: list[ObservationAxis] | None = Field(default=None, max_length=2)
    reason: RefusalReason | None = None
    #: 이 아이의 이동량(거리·시간)을 물었는데 기록으로 못 답하는 경우 (D-072).
    #: **모델은 이 칸만 세우고, 사용자에게 나가는 문장은 어댑터가 코드에서 붙인다** —
    #: `axes` 와 같은 규칙이고 이유는 #278 이다. 답변일 때만 쓴다.
    unmeasured: Literal[True] | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> GeneralAnswer:
        # `kind == "ask"` 의 이른 `return self` 보다 반드시 앞에 둔다 — 안 그러면
        # 되묻기에 붙은 마커가 검사를 통째로 빠져나간다.
        if self.unmeasured is not None and self.kind != "answer":
            raise ValueError(f"a {self.kind} carries no unmeasured marker")
        if self.kind == "ask":
            # 되묻기를 되묻기로 만드는 것은 `question` 이다. `text` 는 비어도 된다 —
            # 기록이 하나도 없으면 먼저 말할 것이 없는 자리가 실제로 있다. `axes` 도
            # 비어도 된다: 모델이 안 골랐다는 사실을 코드가 메우지 않는다.
            if self.question is None or not self.question.strip():
                raise ValueError("an ask needs a question")
            # 2026-09-12 실사용: 질문 한 문장만 나가는 되묻기가 루프를 만들었다. 물음표를
            # 단 막다른 길이지 답이 아니다 — 프롬프트로 시키는 것과 계약으로 막는 것은
            # 다르므로 여기서 떨어뜨린다. 빈 문자열이 허용돼 있던 것이 그 구멍이었다.
            if not self.text.strip():
                raise ValueError("an ask must say something before it asks")
            if self.reason is not None:
                raise ValueError("an ask carries no refusal reason")
            return self
        if self.question is not None:
            raise ValueError(f"a {self.kind} carries no question")
        if self.axes is not None:
            raise ValueError(f"a {self.kind} carries no observation axes")
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

Output exactly one JSON object conforming to the supplied schema. If kind is "answer", write the answer in text and set reason to null. If kind is "ask", put the one question in question, put in text only what you may report first, and set reason to null. If kind is "refuse", set reason to one of the reason categories and leave text empty. No Markdown, no greetings, no filler.

Rules when answering:
- Write in Korean, briefly (3 to 5 sentences). Answer what can be said safely at a common-sense level: general dog care, habits, gear, and everyday routines.
- Ordinary husbandry norms ARE answerable: feeding frequency and a rough amount range, daily water intake, bathing / brushing / nail-trimming frequency, walking gear, socialization timing, sleep duration. Give the typical range, state that individual variation is large, and add that the feeding table on the food package or the veterinarian is the authority for exact values. These ordinary norms are NOT institutional.
- The duration or dosing interval of a medication is answerable the same way: give the typical length or interval for that kind of medication, state that individual variation is large, and add that the prescribing veterinarian or the package insert is the authority for exact values. This covers duration and dosing interval only — which drug to give, whether to start one, the dosage, how to give it (timing, with food, splitting a pill), and side effects stay refused as medication.
- A question of the form "is this okay / is this normal" about a behavior or an intake amount is answered with the normal range plus a note to see a veterinarian if it persists or changes sharply. Do not refuse it. This rule is about husbandry, not medication — the same question about a drug ("두 배로 줘도 돼?", "밥이랑 같이 먹여도 돼?") is refused as medication, not answered here.
- Say you do not know when unsure; never invent. Do not assert facts that require a source document (laws, regulations, procedures, fees, deadlines, official programs, statistics).
- **A general mechanism may be explained; this dog's cause may not be named.** "초록색 구토는 담즙이 섞여 있을 때 나타날 수 있습니다" is allowed — it is a hedged statement about the sign in general. "초록색 구토는 담즙 역류 때문입니다" is not: it attributes THIS dog's symptom to a cause, which is the determination the owner has to go to a veterinarian for. Keep every cause sentence hedged (…일 수 있습니다 / …인 경우가 많습니다) and never write 때문입니다 · 원인은 ~입니다 · ~로 인한 것입니다 about the dog in front of you. This rule is about symptoms, not drugs — how a medication works is refused as medication, not explained as a general mechanism.
- A symptom the owner mentions is NOT a request for a diagnosis. Say what can safely be said about it at a general level — what to watch for, what to adjust at home — and add a short note to see a veterinarian if it persists or worsens. Do not close the conversation by sending them to a hospital when the sign is not an emergency.
- Use DOG_CONTEXT only when the owner asked about it, or when it changes what is safe to advise. Do not sprinkle the breed, the age, or a health condition into an answer to make it look personalized. Never infer how the dog is today from the profile, and never invent facts that are not in it.

Ask back (kind="ask") in this case:
- ask: the message is about how the dog is doing, or about a symptom, and what the owner actually observed is still missing or too thin to act on. Put the question in question, in Korean, and **never leave text empty — a question with nothing before it is a dead end wearing a question mark.** What goes in text depends on why you are asking. **When the message names a symptom, text is what you can already say about that symptom** at a general level — what it commonly relates to, what to watch, when it needs a veterinarian — and only then the question. **When the message is about how the dog is doing or about a symptom**: when a rule below hands you the owner's records, report those in text; when no rule below hands you any, say in text that there is nothing recorded for today so the records alone cannot tell how the dog is, and add one plain sentence inviting the owner to log meals, walks and medication so that next time you can look at that record together with what they tell you. **Say only what logging actually gives**: the record is something you can read back and take into account. Never promise that a log will let you judge the dog's health, name a condition, or say whether the dog is fine — logging changes what you can see, not what you are allowed to conclude. Keep it to one sentence and do not phrase it as a second question. **When you are asking for any other reason** — the message points at something earlier in the conversation you cannot see, or it is simply unclear — say plainly in text what you are missing and **bring up nothing else**: an unresolved 그거 or 아까 말한 거 is not a question about how the dog is doing, so say only that you cannot see what it refers to.
- **Stop asking and answer instead when any of these is true.** Asking again costs the owner time they may not have:
  (a) The conversation so far names **two or more of 구토 · 설사 · 식욕 부진 · 기력 저하 · 파행** — count what CONVERSATION carries as well as this message, not this message alone. Then do not ask anything back: say briefly what those signs together commonly mean at a general level, what to note down for the visit, and that a veterinarian should see the dog. **This is an answer, not an emergency refusal.**
  (b) CONVERSATION shows you already asked about an axis and the owner has now spoken to it. Never put an axis from `pending_axes_the_assistant_asked_about_not_dog_observations` into `axes` again — repeating a question you already asked is the worst thing you can do here.
  (c) You have asked once already in this conversation and the owner answered at all. One question, then answer with what you have.
- One question per turn. You MAY name several related things inside that one sentence — 식욕 · 활력 · 배변 · 구토/설사 · 호흡 — but ask the owner to start with whatever stands out most. Never demand that they answer every item, and never spread the items across several turns as an intake interview.
- Ask for what the owner can observe. Never list candidate diseases, and never say the dog is healthy, fine, normal, or lacking anything. Do not recite the observation items when the question is not about the dog's condition.
- axes belongs to a question about the dog's condition. In axes, name the one or two axes you most need answered, from this closed list: APPETITE, ENERGY, STOOL, VOMIT, BREATHING, MOBILITY, OTHER. The sentence in question may invite more than these; axes is what you are actually waiting on. **Leave axes out entirely when you are not asking about the dog's condition** — an unresolved reference or an unclear request has no axis, and OTHER is not the place to put it. Use OTHER only for an observation that is genuinely none of the six. Nothing downstream will guess an axis for you.
- If the owner has already said enough to answer, answer instead of asking. If the message describes an emergency sign, refuse with emergency instead of asking — an emergency is answered at once, never asked back.

Refuse (kind="refuse") only in these cases:
- diagnosis: the user explicitly asks for a disease name, asks to determine the cause of a symptom, or asks to interpret test results. "Is this okay / is this normal" is NOT diagnosis. Mentioning a symptom is NOT asking for a diagnosis either — only an explicit request for a disease name, for the cause, or for a test reading is. A question about how the dog is doing that names no observation is an ask, not a diagnosis refusal.
- medication: questions about which drug or supplement to give, whether to start one, dosages, how to give it (timing, with food, splitting a pill), or side effects — what they are in general, or whether something the owner describes is one. A question about how long or how often an existing medication is given is NOT medication — see the answerable rule above; a question about whether to begin one is whether to start one, and refused.
- emergency: questions about handling an emergency such as poisoning, breathing difficulty, bleeding, seizures, or loss of consciousness. Say nothing beyond "go to a veterinary hospital right now". Vomiting, diarrhea, limping, and appetite loss are not on this list — they are ask or answer.
- institutional: facts that require a source document, such as laws, regulations, administrative procedures, fees, deadlines, or official support programs. The assistant's institutional-information capability answers those. Ordinary husbandry numbers do NOT belong here.
- off_topic: the question is not about dogs. Check this first: a request that is not about dogs at all is off_topic even when it mentions money, schedules, or procedures. A message about this conversation itself — a complaint, a correction, or the owner telling you to ask them something — is NOT off_topic: ask what they want to know about their dog.

reason is one of the five values above, and null when kind is "answer" or "ask"."""


# 로그가 있을 때만 붙는 규칙 (#344). 로그가 무엇인지, 무엇을 해도 되고 무엇은 안 되는지.
# "did I / has it been done today" 류에 쓰라는 것과, 로그에 없는 용량·일정을 지어내지 말라는 것.
# 약 이름은 로그에도 DOG_CONTEXT 에도 없으므로 이름 · 용량 · 복용 방법 거절은 그대로 선다.
# D-071 로 연 것은 기간 · 투여 간격뿐인데 로그에는 그 값도 없다 — 로그 규칙이 답할 수 있는
# 폭은 안 늘었다.
_CARE_LOG_RULE = """CARE_LOG_TODAY, when present, is what the owner has already logged for this dog today: counts per kind (meal, medication, snack, walk) and the last time each was logged, as HH:MM in Seoul time. Treat it as fact for questions like "did I feed / medicate / walk today", "has the morning medication been given", or "how many meals so far". You may say what was logged and when, and note plainly when a kind has no entry today.

A question about how the dog is doing today — "오늘 건강 상태는 어때?", "오늘 컨디션 어때?" — is also a question this log speaks to. Report what is recorded and what is not, briefly, always framed as 기록상 / 기록에는 (what the record says), and then ask what the owner observed. **A missing entry means the record has no entry. It does NOT mean the dog did not eat, was not walked, or was not medicated, and it does NOT mean anything is wrong** — say that the record has none, never that it did not happen. Never infer a dose, a schedule, or whether more is needed from it, and never call the dog healthy, fine, normal, unwell, or lacking from it — the log records what happened, not how the dog is. Ignore the log only when the question has nothing to do with this dog's day. When CARE_LOG_TODAY is absent, say nothing about a log."""

# 진료비가 있을 때만 붙는 규칙 (#353 Task 7). VET_RECENT 가 무엇인지, 무엇을 해도 되고
# 무엇은 안 되는지 — `_CARE_LOG_RULE` 과 같은 결. 진단·처치를 권하지 말라는 것과, 기록에
# 없는 사유·금액을 지어내지 말라는 것.
_VET_SPEND_RULE = """VET_RECENT, when present, is what the owner has confirmed about this dog's vet visits: this month's total spend, the visit count in the last 30 days, the most recent visit (date, reason, amount, and the hospital's name/phone if known), and total spend per reason over the last 12 months. Treat it as fact for questions like "how much have I spent on skin issues this year" or "what was that hospital's phone number". Use only the reasons and numbers present; never invent a visit, a reason, or an amount that is not there. Never diagnose, recommend treatment, or judge whether spending is high or normal from it — it is a spending record, not a medical opinion. If the question is not about vet visits or spending, ignore it. When VET_RECENT is absent, say nothing about vet spending or visit history."""

# D-072. `_CARE_LOG_RULE` 과 같은 결이고, 다른 것은 **못 잴 때 무엇을 하느냐** 한 문단이다.
# 고지 문장은 여기 없다 — 어댑터가 `redirects.DISTANCE_FROM_RECORDED_WALKS_ONLY` 를 붙인다.
# 모델이 그 문장을 쓰면 판본이 둘이 되고, 그것이 #278 이 막은 것이다.
_WALK_ACTIVITY_RULE = """WALK_ACTIVITY, when present, is what the app actually recorded for this dog's walks today: how many walks were recorded, how many of those have a finished measurement, the total measured distance in metres, the total measured moving time in seconds, and the clock time the last walk started, as HH:MM in Seoul time. Treat it as fact for questions like "how far did we walk today" or "how long was the walk". Report the distance and the time as they are; round only for readability and never convert a number you were not given. When walk_count is larger than measured_walk_count, say plainly that some recorded walks have no measurement yet and give the total for the ones that do — "3 recorded, 2 measured, 1.2 km" is true and "3 walks, 1.2 km" is not.

Set unmeasured to true when the question asks how far or how long THIS dog moved and WALK_ACTIVITY cannot answer it — it is absent, no walk has a measurement, or the trip the owner is describing is not what was recorded. Never estimate the distance or the time from a route described in words, from place names, from a count of stops, or from how long the owner says the trip took. A sentence explaining why the number is unavailable is added after your answer, so do not write that explanation yourself, do not apologise for it, and do not tell the owner to use a map app. You may still say which parts of a described trip the dog would not have walked at all, such as a stretch travelled by bus or train. When WALK_ACTIVITY is absent, say nothing about a walk record unless you are setting unmeasured."""


def general_prompt_version(payload: GeneralPayload) -> str:
    """Which of the (now five-shaped) prompt bodies ``build_general_prompt`` returns.

    The base of the name comes from exactly which of ``care_log``/``vet_spend`` are
    present — unchanged since #353. ``-walk`` (D-072 Task 5), then ``-conv`` (#416 Task 6),
    are appended on top of whichever base, each only when its own payload field rides
    along — see the constant comments above for why a suffix and not more constants
    (four for `-conv`, and the same reasoning holds for `-walk`: `care_log`/`vet_spend`/
    `walk_activity` combine independently, so a dedicated constant per combination would
    need eight).
    """
    if payload.care_log is not None and payload.vet_spend is not None:
        version = GENERAL_CARE_LOG_VET_PROMPT_VERSION
    elif payload.care_log is not None:
        version = GENERAL_CARE_LOG_PROMPT_VERSION
    elif payload.vet_spend is not None:
        version = GENERAL_VET_PROMPT_VERSION
    else:
        version = GENERAL_PROMPT_VERSION
    if payload.walk_activity is not None:
        version = f"{version}-walk"
    if payload.conversation is not None:
        version = f"{version}-conv"
    return version


def build_general_prompt(payload: GeneralPayload) -> str:
    """Assemble the fallback prompt from optional blocks.

    **The two combinations that predate the vet-spend card are reproduced by the exact
    same literal strings as before** (D-057 ③ / #344) — the ``care_log is None and
    vet_spend is None`` branch below (now also gated on ``walk_activity is None``, D-072
    Task 5, since a walk-only payload must reach the assembled path below it) is untouched
    code, not a block reconstruction, so the 84-pairwise-approved body cannot drift through
    that refactor. The care-log case *is* assembled from blocks (below), but the assembly
    is byte-for-byte the same string the old dedicated branch produced — see the block
    order comment.

    What that identity does **not** protect is the schema line: every branch embeds
    ``GeneralAnswer.model_json_schema()``, so widening ``kind`` changes all four bodies at
    once. That is why #415 moved all four version strings together rather than only the
    one whose rules it edited.

    ``payload.conversation`` (#416 Task 6) changes nothing about the branch above: when it
    is ``None`` — today's every call — both branches return exactly the literals they
    always did. Only when it is present does a ``CONVERSATION:`` block get added,
    immediately before ``USER_QUERY:``, and only then does the version carry ``-conv``.
    """
    schema = json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)
    dog = payload.dog.model_dump(mode="json", exclude_none=True) if payload.dog else {}

    if payload.care_log is None and payload.vet_spend is None and payload.walk_activity is None:
        if payload.conversation is None:
            return (
                f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}\n\n"
                f"{_SAFETY_PROMPT}\n\n"
                f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
                f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}\n"
                f"USER_QUERY: {payload.question}\n"
            )
        return (
            f"PROMPT_VERSION: {general_prompt_version(payload)}\n\n"
            f"{_SAFETY_PROMPT}\n\n"
            f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
            f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}\n"
            f"{render_conversation_context(payload.conversation)}\n"
            f"USER_QUERY: {payload.question}\n"
        )

    # Rule paragraphs: safety always, care-log rule before vet-spend rule — that order is
    # what keeps the care-log-only prompt identical to the pre-vet-spend care-log body.
    rule_blocks = [_SAFETY_PROMPT]
    if payload.care_log is not None:
        rule_blocks.append(_CARE_LOG_RULE)
    if payload.vet_spend is not None:
        rule_blocks.append(_VET_SPEND_RULE)
    if payload.walk_activity is not None:
        rule_blocks.append(_WALK_ACTIVITY_RULE)

    # Context lines: DOG_CONTEXT always, CARE_LOG_TODAY before VET_RECENT before
    # WALK_ACTIVITY — same reason. A CONVERSATION line, when present, goes last: it is
    # the block that must sit immediately before USER_QUERY.
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
    if payload.walk_activity is not None:
        walk_activity = payload.walk_activity.model_dump(mode="json", exclude_none=True)
        context_lines.append(
            f"WALK_ACTIVITY: {json.dumps(walk_activity, ensure_ascii=False, sort_keys=True)}"
        )
    if payload.conversation is not None:
        context_lines.append(render_conversation_context(payload.conversation))

    # 인접 리터럴의 암묵적 연결에 기대지 않는다 — `+` 로만 잇는다. 이유는 이 파일이
    # 존재하는 이유와 같다: 나중에 이 두 조각 사이에 표현식 하나가 끼어들면, 암묵적
    # 연결은 구분자를 조용히 빠뜨리지만 명시적 `+` 는 그 자리에서 문법 오류로 걸린다.
    return (
        f"PROMPT_VERSION: {general_prompt_version(payload)}\n\n"
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
        if answer.kind == "ask":
            try:
                ask = ClarifyRequest(
                    question=(answer.question or "").strip(),
                    missing=[GENERAL_ASK_MISSING],
                    # 모델이 고른 것 그대로. 안 골랐으면 빈 목록이고, 그 빈 자리를
                    # 질문 문장에서 유추해 메우지 않는다 (사람 결정, 2026-09-10).
                    missing_axes=answer.axes or [],
                )
            except ValidationError:
                # `ClarifyRequest.question` 은 500자, `GeneralAnswer.text` 는 1,000자다.
                # 그 사이를 여기서 막아야 집계가 계약 위반으로 터지지 않는다 — 못 담을
                # 되묻기는 잘못된 출력이지 사용자에게 보일 것이 아니다.
                return self._error(
                    started, "general_invalid_output", "일반 답변 결과를 해석할 수 없습니다."
                )
            data: dict[str, Any] = {"ask": ask.model_dump(mode="json")}
            # 기록으로 먼저 말할 수 있는 것이 있으면 같이 싣는다. 없으면 키가 아예 없다 —
            # 빈 문자열을 두면 집계가 "말할 것이 없다" 와 "빈 말을 했다" 를 못 가른다.
            grounded = answer.text.strip()
            if grounded:
                data["answer"] = grounded
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.OK,
                data=data,
                elapsed_ms=_elapsed_ms(started),
            )
        if answer.kind == "refuse":
            reason = answer.reason or "off_topic"
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.REFUSED,
                refusal=OutcomeDetail(code=reason, message=SCOPED_REDIRECT_MESSAGES[reason]),
                elapsed_ms=_elapsed_ms(started),
            )
        text = answer.text.strip()
        if answer.unmeasured:
            # 제품 문장이다 — 모델이 쓰지 않는다 (#278). 본문 뒤에 붙이고 본문은 안 고친다.
            text = f"{text}\n\n{DISTANCE_FROM_RECORDED_WALKS_ONLY}"
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": text},
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
    "GENERAL_ASK_MISSING",
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
    "general_prompt_version",
    "validate_general_answer",
]
