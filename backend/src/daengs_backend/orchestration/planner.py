"""Deterministic route planning around the semantic decision (D-041, Card 2B).

Two responsibilities, both deterministic:

1. Resolve the approved machine-readable routing signal (`requested_capability`,
   routing doc §1) before any LLM call. It is a routing signal, never
   authorization (D-036), and no new deterministic signals are invented here.
2. Assemble the real Card 1 RoutePlan from a SemanticRoutingDecision using only
   the trusted query/context: Training/Life carry the exact original query, Walk
   and Place coordinates come only from context.location, handoff reasons are
   fixed, and missing coordinates produce an exclusive CLARIFY.

**Payloads are built by an exhaustive per-capability branch, never by a fallback**
(D-051). Until v7 the loop read `if capability in {"training","life"}: … else:
{lat, lon}`, so every capability that was not Training or Life silently received a
WalkPayload shape. That was correct only while Walk was the sole coordinate
capability; the moment `place` became selectable it would have handed Place a
payload with no `query`, failing PlacePayload validation and surfacing as a
top-level FAILED on exactly the queries Place was added to answer. The `else`
below therefore raises: a new ExecuteName must state its payload here or stop the
request loudly, never inherit another capability's shape.

**The general-answer fallback reaches a plan two ways, both behind one flag** (D-057).
(1) A planner rule: when the semantic decision selects nothing at all — no capability,
no handoff — and `general_fallback` is on, the plan becomes exactly one `general`
request carrying the same trusted payload Life gets (question + resolved dog facts).
(2) Since `semantic-router-ko-v9` the router may also select `general` *in addition to*
a specialized destination, so a care or health worry mixed into a weather/venue/
institution utterance is not silently dropped (#277 measured exactly that loss). The
router never uses it to replace Training/Life/Walk/Place. With the flag off the planner
strips `general` from the decision, so production builds the plans it built before.
`general` orders last, never needs coordinates, and the explicit
`requested_capability` signal is untouched — `general` is not a resolvable signal.

**`vet_contact` skips this module's semantic path entirely.** `resolve_emergency_route`
runs before any LLM call (deterministic lexicon gate or explicit signal), builds an
exclusive single-request plan itself, and never lets `vet_contact` reach the shared
`assemble_route_plan`/`_payload_for` machinery — see `resolve_emergency_route`'s
docstring and D-051 ②.

**`care_log` 도 같은 이유로 같은 길을 간다 — 그런데 이쪽은 쓴다** (#331 후속, D-075).
이 모듈에서 DB 에 행을 남기는 계획을 만드는 함수는 `resolve_care_log_write` 하나이고, 그것은
**사용자가 앞 턴의 제안에 승낙했을 때만** 계획을 낸다. 앞 턴의 제안을 만드는
`resolve_care_log_route` 는 아무것도 안 쓴다 (확인 되묻기, 아니면 기록 화면 HANDOFF).
둘 다 모델을 안 태우고, 기록될 값은 전부 신뢰된 context 와 서버 시계에서 온다 — 쓰기가
붙어도 D-051 의 "모델은 payload 를 한 글자도 쓰지 않는다" 가 그대로인 이유다.

**`skin` 도 공유 조립기를 안 지난다** (D-078). `resolve_skin_route` 가 명시 신호
`requested_capability="skin"` 에 **서버가 해소한 판정 기록**(`context["screening"]`)이 붙었을 때만
배타 단일 요청을 직접 만든다. 새 신호를 발명한 것이 아니다 — 같은 신호가 기록 없이 오면
`resolve_deterministic_route` 가 예전처럼 skin HANDOFF 를 낸다. 기록이 붙은 요청만 가로챈다.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from daengs_backend.orchestration import care_log
from daengs_backend.orchestration.contracts import (
    SCREENING_HISTORY_LIMIT,
    CareLogProposal,
    ConversationContext,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.emergency import is_emergency
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    SemanticRoutingDecision,
)

# Requests are emitted in this order regardless of the order the router listed them.
# The model's list order is not stable — a live v7 probe returned both ["place","walk"]
# and ["walk","place"] for the same shape of query — and that order is user-visible,
# because `aggregate_results` builds the "[산책] … [장소] …" sections from it. Two
# identical questions should not produce two differently-ordered answers. The frozen
# router benchmark is unaffected either way: `_semantic_plan_key` compares requests as
# a multiset. Order follows the `CapabilityName` declaration order.
_GENERAL = "general"
_VET_CONTACT = "vet_contact"
_CARE_LOG = "care_log"
_SKIN = "skin"
_EXECUTION_ORDER = (
    "training",
    "life",
    "walk",
    "place",
    _GENERAL,
    _VET_CONTACT,
    _CARE_LOG,
    _SKIN,
)
# The names the router (and the explicit signal) may select. `general` is executable but
# never selectable — it only ever enters a plan through the fallback rule below, so it is
# excluded here on purpose: `requested_capability="general"` is an unresolved signal.
# `general` 과 `vet_contact` 는 둘 다 `_EXECUTE_NAMES` 밖이지만 이유가 정반대다.
# general 은 명시 신호로도 못 부르고, vet_contact 는 **명시 신호로만** 부른다 —
# 그 신호는 `resolve_emergency_route` 가 라우터보다 앞에서 소비한다.
# `care_log` 가 여기서 빠지는 이유는 `vet_contact` 와 같다 — 라우터도 명시 신호도 이 **쓰기**
# 능력을 못 부른다. 여는 길은 `resolve_care_log_write` 하나뿐이고, 그것은 사용자가 앞 턴의
# 제안에 승낙했을 때만 열린다. 대신 같은 이름의 HANDOFF 는 `_HANDOFF_REASONS` 에 있어서
# 명시 신호로 부를 수 있다 — 그쪽은 아무것도 안 쓰고 기록 화면으로 보낼 뿐이다.
# `skin` (D-078) 이 빠지는 이유는 **여기 넣으면 신호의 뜻이 바뀌어서**다. `skin` 은 이미
# `_HANDOFF_REASONS` 의 명시 신호이고, `resolve_deterministic_route` 는 이 집합을 먼저 본다 —
# 넣는 순간 기록 없는 `skin` 신호가 HANDOFF 대신 payload 규칙 없는 EXECUTE 가 되어 500 이 난다.
# 판정 기록이 붙은 `skin` 은 `resolve_skin_route` 가 그보다 앞에서 소비한다.
_EXECUTE_NAMES = frozenset(
    name for name in _EXECUTION_ORDER if name not in {_GENERAL, _VET_CONTACT, _CARE_LOG, _SKIN}
)
#: `SkinPayload.question` 의 한도. 넘으면 계획을 안 열고 HANDOFF 로 떨어진다 — 계약 검증이
#: 500 을 내는 것보다 예전 동작이 낫다. `AssistantQueryRequest.query` 에는 공통 상한이 없다.
_SKIN_QUESTION_LIMIT = 1_000
_EXECUTION_INDEX = {name: index for index, name in enumerate(_EXECUTION_ORDER)}
# Capabilities whose payload carries trusted coordinates. Missing coordinates make
# the whole plan a CLARIFY, so this set is what the coordinate gate reads.
_NEEDS_COORDINATES = frozenset({"walk", "place"})
_QUESTION_CAPABILITIES = frozenset({"training", "life"})
# The verdicts `ScreeningContext` allows. Kept as a literal set rather than read off the
# contract so a widened contract cannot silently widen what the planner copies (#283).
_SCREENING_VERDICTS = frozenset({"normal", "abnormal", "retake"})
# **의미 라우터가 고를 수 있는 handoff target 의 목록이기도 하다.** 키가 곧
# `semantic.HandoffName` 의 값이어야 한다 — `resolve_deterministic_route` 가 명시 신호를
# 여기서 찾아 `SemanticRoutingDecision(handoffs=[...])` 으로 넘기므로, 라우터 스키마에 없는
# 이름을 여기 넣으면 그 신호가 500 이 된다 (실제로 한 번 그렇게 넣고 테스트가 잡았다).
_HANDOFF_REASONS = {
    "skin": "image_upload_required",
    "gait": "video_upload_required",
}
#: 케어 기록 화면으로 (#331 후속, D-075). **위 표에 안 넣는 것이 의도다** — 라우터가 못 고르고
#: 명시 신호로도 못 부른다. 들어오는 길은 `resolve_care_log_route` 의 결정론 게이트 하나뿐이다.
#: "새 결정론 신호를 여기서 발명하지 않는다" (모듈 머리말 1번)는 규칙을 지키는 쪽이기도 하다 —
#: 앱이 "기록" 버튼을 누르는 것은 어차피 `/app/care-events` POST 이고, 비서를 거칠 일이 없다.
_CARE_LOG_HANDOFF_REASON = "care_log_entry_required"
# The assistant contract's South Korea box. Place's own service accepts a wider box
# (lat 32~40 · lng 123~133); the public boundary deliberately stays the stricter one
# so Place cannot loosen validation for everyone else (discovery-migration.md §5).
_COORDINATE_BOUNDS = (("lat", 33.0, 39.0), ("lon", 124.0, 132.0))
# 확인 문장에 찍는 시각의 시간대. `services/care_event.DAY_TIMEZONE` 과 **같은 값이어야**
# 한다 — 하루의 경계를 서울로 자르는 쪽과 사용자에게 시각을 보여 주는 쪽이 다르면,
# 자정 전후에 "23:50에 기록할까요?" 라고 묻고 어제 칸에 넣는 일이 생긴다.
_CARE_LOG_TZ = ZoneInfo("Asia/Seoul")


def resolve_emergency_route(
    *,
    query: str,
    context: dict[str, Any],
    requested_capability: str | None,
    at_night: bool,
) -> RoutePlan | None:
    """응급이면 `vet_contact` 하나짜리 계획을, 아니면 None 을 낸다.

    **의미 라우터보다 앞에 선다.** 그래서 응급 경로에는 모델 호출이 0회다.

    **배타다.** 응급 답에 산책 조건이나 훈련 요령이 섞이면 보호자의 인지 부하만 늘린다.

    **좌표가 없어도 CLARIFY 를 내지 않는다.** `vet_contact` 는 `_NEEDS_COORDINATES` 에
    없고, 좌표는 있으면 싣고 없으면 None 으로 간다 — 응급에 "위도를 알려주세요" 로
    되묻는 것이 최악이기 때문이다. 없는 좌표의 처리는 adapter 가 ABSTAINED 로 한다.

    두 진입점(어휘 게이트 · 명시 신호)이 **이 함수 하나**를 지난다. 계획이 한 곳에서
    만들어져야 두 경로가 서로 다른 답을 낼 수 없다 (D-051 ② 와 같은 이유).
    """
    if requested_capability != _VET_CONTACT and not is_emergency(query):
        return None

    location = _trusted_location(context)
    payload: dict[str, Any] = {"at_night": at_night}
    if location is not None:
        payload["lat"] = location["lat"]
        payload["lon"] = location["lon"]

    return RoutePlan.model_validate(
        {
            "requests": [{"capability": _VET_CONTACT, "payload": payload, "timeout_ms": None}],
            "handoffs": [],
            "clarify": None,
            "router": RouterKind.DETERMINISTIC,
            "model": None,
            "prompt_version": None,
        }
    )


def resolve_skin_route(
    *,
    query: str,
    context: dict[str, Any],
    requested_capability: str | None,
    enabled: bool,
) -> RoutePlan | None:
    """판정 기록이 붙은 `skin` 신호면 피부 해설 하나짜리 계획을, 아니면 None 을 낸다 (D-078).

    **셋이 다 맞아야 연다** — 명시 신호가 `skin` 이고, 킬 스위치(`settings.skin_agent`)가 켜져
    있고, `routers/assistant._with_screening_context` 가 소유를 확인해 해소한 판정이
    `context["screening"]` 에 있을 것. 하나라도 아니면 None 이고, 같은 신호는 뒤의
    `resolve_deterministic_route` 에서 오늘과 같은 skin HANDOFF 가 된다. 그래서 이 함수는
    그것보다 **앞**에 선다 (`service._plan_and_execute`). 응급은 이것보다 앞이다.

    **판정은 본문이 아니라 서버가 읽은 기록에서만 온다.** 여기서 읽는 것은 `_screening_context`
    · `screening_history` 두 화이트리스트를 지난 판정 종류와 경과일뿐이다 (불변식 15). 병변
    이름과 확률은 payload 에 칸이 없다.

    **배타다.** 판정 해설에 산책 조건이나 제도 정보가 섞이면 결과 화면에서 이어 물은 질문의
    답이 흐려진다. 좌표도 안 싣는다.
    """
    if not enabled or requested_capability != _SKIN:
        return None
    screening = _screening_context(context)
    if screening is None or len(query) > _SKIN_QUESTION_LIMIT:
        return None
    payload: dict[str, Any] = {"question": query, "screening": screening}
    history = screening_history(context)
    if history is not None:
        payload["history"] = history

    return RoutePlan.model_validate(
        {
            "requests": [{"capability": _SKIN, "payload": payload, "timeout_ms": None}],
            "handoffs": [],
            "clarify": None,
            "router": RouterKind.DETERMINISTIC,
            "model": None,
            "prompt_version": None,
        }
    )


def resolve_care_log_write(
    *,
    query: str,
    pending: CareLogProposal | None,
    now: datetime,
) -> RoutePlan | None:
    """앞 턴의 제안에 **승낙했을 때만** `care_log` 하나짜리 계획을, 아니면 None (#331 후속).

    **쓰기가 열리는 유일한 자리다.** 라우터도, 명시 신호도, 다른 어떤 경로도 `care_log`
    능력을 계획에 넣지 못한다 (`_EXECUTE_NAMES` 에서 빠진 이유) — 그래서 "무엇이 DB 에 쓸 수
    있나" 의 답이 이 함수 하나이고, 감사하려면 이 호출자만 보면 된다.

    **payload 를 만들지 않는다. 옮긴다.** `pending` 은 지난 턴에 사용자가 문장으로 보고
    승낙한 바로 그 제안이고(`chat_turns.public_response` 에서 되읽은 값), 여기서 종류·시각·
    강아지·멱등키 중 어느 것도 새로 고르지 않는다. 확인 단계의 약속이 "보여 준 것만
    들어간다" 라서다 — 그래서 제안과 payload 가 같은 타입이다 (`CareLogProposal`).

    **거절과 무관한 발화는 None 이다.** 셋을 가르는 것은 `care_log.confirmation_of` 이고,
    이 함수는 `"affirm"` 만 계획으로 바꾼다. 거절 문구는 서비스가 고정 응답으로 내고
    (`care_log.build_care_log_declined_response`), 무관한 발화는 제안을 흘리고 평소대로
    라우팅된다.

    **묵은 제안은 안 쓴다** (`care_log.PROPOSAL_TTL`). 대기는 한 턴짜리지만 그 한 턴이 며칠
    전일 수 있어서다 — 이유는 그 상수의 주석에 있다. 묵었으면 None 이라, 사용자의 "네" 는
    아무 일도 일으키지 않고 평소 라우팅으로 떨어진다.
    """
    if pending is None:
        return None
    if care_log.confirmation_of(query) != "affirm":
        return None
    if not care_log.is_proposal_fresh(pending, now=now):
        return None
    return RoutePlan.model_validate(
        {
            "requests": [{"capability": _CARE_LOG, "payload": pending, "timeout_ms": None}],
            "handoffs": [],
            "clarify": None,
            "router": RouterKind.DETERMINISTIC,
            "model": None,
            "prompt_version": None,
        }
    )


def resolve_care_log_route(
    *,
    query: str,
    context: dict[str, Any],
    now: datetime,
    care_log_write: bool,
) -> RoutePlan | None:
    """"방금 밥 먹였어" 를 받는 자리 — 확인 되묻기, 아니면 기록 화면 HANDOFF (#331 후속).

    **아무것도 안 쓴다.** 이 함수가 내는 가장 센 것은 "이렇게 기록할까요?" 라는 질문이다.
    쓰기는 다음 턴의 `resolve_care_log_write` 가 하고, 그 사이에 사용자의 승낙이 있다.

    갈림은 하나뿐이다 — **제안에 필요한 것이 다 있나.** 셋이 필요하다:

    | 필요한 것 | 없으면 |
    | --- | --- |
    | 쓰기 플래그(`DAENGS_CARE_LOG_WRITE`)와 쓸 수 있는 요청(`care_log_writable`) | HANDOFF |
    | 신뢰된 `active_dog_id` | HANDOFF |
    | 어휘로 읽히는 **한** 종류 (`care_log.kind_of`) | HANDOFF |

    **없을 때 HANDOFF 인 것이 이 설계의 기본값이다.** 추측해서 쓰지 않고, 사람이 화면에서
    적게 보낸다 — `docs/care-events.md` 가 적어 둔 순서("로그·화면 → 채팅에서 기록 화면으로
    HANDOFF → 확인 단계 있는 자동 쓰기")의 가운데 칸이 바로 이 갈래이고, 플래그가 꺼진
    운영에서는 **이쪽만** 돈다.

    `occurred_at` 은 `now` 다 — 서버 시계이고, 사용자는 그 값을 확인 문장에서 보고 승낙한다.
    `proposal_id` 는 서버가 만든 UUID 로 `care_events.client_event_id` 가 된다 (그 칸이 원래
    앱 것인데 왜 여기서 만드는지는 `CareLogProposal` 독스트링).
    """
    if not care_log.is_care_log_statement(query):
        return None

    kind = care_log.kind_of(query)
    pet_id = _trusted_pet_id(context)
    writable = care_log_write and context.get("care_log_writable") is True
    if kind is None or pet_id is None or not writable:
        return RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [{"target": _CARE_LOG, "reason": _CARE_LOG_HANDOFF_REASON}],
                "clarify": None,
                "router": RouterKind.DETERMINISTIC,
                "model": None,
                "prompt_version": None,
            }
        )

    proposal = CareLogProposal(
        kind=kind, pet_id=pet_id, occurred_at=now, proposal_id=uuid.uuid4()
    )
    return RoutePlan.model_validate(
        {
            "requests": [],
            "handoffs": [],
            "clarify": {
                "question": care_log.proposal_question(
                    proposal, clock=now.astimezone(_CARE_LOG_TZ).strftime("%H:%M")
                ),
                # 좌표 게이트의 `location.lat` · General 되묻기의 `observation` 과 같은 자리의
                # 어휘다. 되묻기를 종류별로 렌더하는 클라이언트가 이 값으로 가른다.
                "missing": ["care_log_confirmation"],
                "care_log": proposal,
            },
            "router": RouterKind.DETERMINISTIC,
            "model": None,
            "prompt_version": None,
        }
    )


def _trusted_pet_id(context: dict[str, Any]) -> uuid.UUID | None:
    """`context["active_dog_id"]` → UUID. 모양이 틀리면 None.

    **소유권은 여기서 안 본다.** 이 값은 이미 HTTP 경계가 세운 것이고(저장 경로는 대화의
    `pet_id` 로 덮어쓴다), 실제로 쓸 때 `services/care_event.record` 가 `get_accessible` 로
    다시 묶는다 — 남의 강아지면 그 자리에서 404 감이다. 계획 단계에서 DB 를 열면
    `planner` 가 세션을 알게 된다.
    """
    raw = context.get("active_dog_id")
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _trusted_location(context: dict[str, Any]) -> dict[str, float] | None:
    """검증된 좌표만 돌려준다. 상자를 벗어나면 **없는 것으로 친다** (D-051 ③).

    `_missing_coordinates` 와 같은 판정을 쓰되, 여기서는 없다고 해서 요청을 세우지 않는다.
    """
    if _missing_coordinates(context):
        return None
    location = context["location"]
    return {"lat": location["lat"], "lon": location["lon"]}


def resolve_deterministic_route(
    *, requested_capability: str | None, query: str, context: dict[str, Any]
) -> RoutePlan | None:
    """Return a deterministic RoutePlan when the approved signal resolves it, else None.

    Since v7, `place` is an ordinary member of `_EXECUTE_NAMES`, so the explicit signal
    and the semantic path build the identical plan through `assemble_route_plan` —
    PR #196 needed a separate Place branch here only because the shared assembler had
    no Place payload rule yet. One code path is the point: a Place request assembled
    two different ways is a Place request that can drift.
    """
    if requested_capability is None:
        return None
    if requested_capability in _EXECUTE_NAMES:
        decision = SemanticRoutingDecision(execute=[requested_capability], handoffs=[])
    elif requested_capability in _HANDOFF_REASONS:
        decision = SemanticRoutingDecision(execute=[], handoffs=[requested_capability])
    else:
        # An unresolved signal does not fail the request; semantic routing decides.
        return None
    return assemble_route_plan(
        decision,
        query=query,
        context=context,
        router=RouterKind.DETERMINISTIC,
        model=None,
        prompt_version=None,
    )


def assemble_route_plan(
    decision: SemanticRoutingDecision,
    *,
    query: str,
    context: dict[str, Any],
    router: RouterKind,
    model: str | None = ROUTER_MODEL_ID,
    prompt_version: str | None = PROMPT_VERSION,
    general_fallback: bool = False,
    resolved: ConversationContext | None = None,
) -> RoutePlan:
    """Build the real Card 1 RoutePlan using only trusted query/context values.

    `model`/`prompt_version` describe how the selection was reached, not what was selected.
    The deterministic caller above passes None for both because it calls no model at all —
    they are not "unknown", they are "there was none", and the console renders that
    difference (#238).

    `general_fallback` defaults to off so that every existing caller — including the
    frozen router-benchmark runners, which score the *router's* decision — keeps
    building exactly the plan it built before (#279). Production passes
    `settings.general_fallback`.

    `resolved` (#416 Task 5, R14) is the **already-converted** `ConversationContext` — the
    caller (`service._plan_and_execute`) is the only layer that holds both the Turn
    Resolver's `ResolvedTurn` and the `PendingClarification` it may anchor to, so it builds
    this value once (`resolver.conversation_context_of`) and passes the same object here and
    to the semantic router. This function does no conversion; it only threads the value to
    `_payload_for`, which puts it on `GeneralPayload.conversation` and nowhere else.
    """
    needs_coordinates = _NEEDS_COORDINATES.intersection(decision.execute)
    if "place" in needs_coordinates and (
        "facility_session_id" in context or "facility_location" in context
    ):
        needs_coordinates = needs_coordinates - {"place"}
    missing = _missing_coordinates(context) if needs_coordinates else []
    if missing:
        # CLARIFY is exclusive (O-8): nothing executes and nothing hands off first.
        # One gate for the whole selection — a mixed Place+Walk turn with no
        # coordinates must not run half of itself.
        return RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [],
                "clarify": {
                    "question": _clarify_question(missing, needs=needs_coordinates),
                    "missing": missing,
                },
                "router": router,
                "model": model,
                "prompt_version": prompt_version,
            }
        )

    selected: list[str] = list(decision.execute)
    if not general_fallback:
        # Flag off: the router may name `general` (v9 destination, D-057), but production
        # builds exactly the plan it built before the fallback existed — strip it. A
        # `general`-only decision therefore becomes the old empty plan (FAILED), not an answer.
        selected = [name for name in selected if name != _GENERAL]
    elif (
        not selected
        and not decision.handoffs
        and decision.social_intent is None  # never reaches here in practice; belt and braces
    ):
        # The fallback rule (module docstring). One request, and only when the router
        # chose nothing: a specialized selection is never padded with `general` by rule —
        # the router adds it explicitly when a care intent is mixed in (D-057 ①).
        selected = [_GENERAL]

    requests: list[dict[str, Any]] = []
    # An unrecognized name sorts last rather than raising here, so the precise
    # "no payload rule" error below is what surfaces instead of an index error.
    for capability in sorted(
        selected, key=lambda name: _EXECUTION_INDEX.get(name, len(_EXECUTION_ORDER))
    ):
        payload = _payload_for(capability, query=query, context=context, resolved=resolved)
        requests.append({"capability": capability, "payload": payload, "timeout_ms": None})

    return RoutePlan.model_validate(
        {
            "requests": requests,
            "handoffs": [
                {"target": target, "reason": _HANDOFF_REASONS[target]}
                for target in decision.handoffs
            ],
            "clarify": None,
            "router": router,
            "model": model,
            "prompt_version": prompt_version,
        }
    )


def _payload_for(
    capability: str,
    *,
    query: str,
    context: dict[str, Any],
    resolved: ConversationContext | None = None,
) -> dict[str, Any]:
    """The payload for one capability. Exhaustive by design — see the module docstring.

    Every branch reads only the original query text and the already-validated
    `context.location`. Nothing here is derived from model output, and Place gets the
    user's exact words: `PlacePayload` does not strip whitespace because the Place
    service grounds its own interpretation in literal spans of the original query.

    `resolved` (#416 Task 5, R14) only ever reaches the `general` branch — every other
    capability payload has no `conversation` field to put it on, and General is the
    only answerer whose prompt is meant to carry an unresolvable-reference notice. It
    arrives here already built as a `ConversationContext` (`service._plan_and_execute`
    converts once, via `resolver.conversation_context_of`); this function assigns it
    through and does no conversion of its own.
    """
    if capability in _QUESTION_CAPABILITIES:
        payload: dict[str, Any] = {"question": query}
        if capability == "life":
            dog = _dog_context(context)
            if dog is not None:
                payload["dog"] = dog
            screening = _screening_context(context)
            if screening is not None:
                payload["screening"] = screening
            history = screening_history(context)
            if history is not None:
                payload["screening_history"] = history
        return payload
    if capability == _GENERAL:
        # Same rule as Life: the exact question plus the trusted dog facts, never a
        # coordinate — the fallback is not allowed to answer Walk's or Place's question.
        #
        # **The screening verdict deliberately stops here.** `general` answers without
        # retrieved evidence (D-057), and its own refusal codes already send symptoms and
        # diagnosis away (`adapters/general.py` `diagnosis`). Handing an ungrounded answerer
        # the fact that a skin check came back `abnormal` invites exactly the sentence that
        # refusal exists to prevent. Life gets it because Life answers from ordinances and
        # subsidy documents, and those are what "이런 경우 지원이 있어요" is made of.
        payload = {"question": query}
        dog = _dog_context(context)
        if dog is not None:
            payload["dog"] = dog
        # Today's care log goes to the fallback and nowhere else (#344): "did I give the
        # medication this morning" is a general question, and Life's documents do not care.
        care_log = _care_log_context(context)
        if care_log is not None:
            payload["care_log"] = care_log
        # Same rule again for confirmed vet spend (#353 Task 7): "피부로 1년간 얼마 썼지"
        # is a general question too, and Life's ordinances do not carry that answer either.
        vet_spend = _vet_spend_context(context)
        if vet_spend is not None:
            payload["vet_spend"] = vet_spend
        # 오늘 기록된 산책도 폴백에만 간다 (D-073): "얼마나 걸었어" 는 일반 질문이고,
        # Life 의 조례는 그 답을 안 들고 있다.
        walk_activity = _walk_activity_context(context)
        if walk_activity is not None:
            payload["walk_activity"] = walk_activity
        if resolved is not None:
            payload["conversation"] = resolved
        return payload
    if capability == "walk":
        location = context["location"]
        return {"lat": location["lat"], "lon": location["lon"]}
    if capability == "place":
        if "facility_session_id" in context:
            return {"query": query, "facility_session_id": context["facility_session_id"]}
        location = context.get("facility_location", context.get("location"))
        return {"query": query, "lat": location["lat"], "lon": location["lon"]}
    if capability == _VET_CONTACT:
        # 이 경로로는 오지 않는다 — `resolve_emergency_route` 가 payload 를 직접 만든다.
        # 그래도 규칙을 적어 두는 이유는 D-051 ② 다: 새 ExecuteName 은 자기 payload 를
        # 적거나 요청을 소리 나게 세우거나 둘 중 하나다.
        raise ValueError(
            "vet_contact payloads are built by resolve_emergency_route, not by the shared assembler"
        )
    # A destination the router can now emit but the planner has no payload rule for.
    # Failing here is the point: the alternative is silently sending some other
    # capability's payload shape (D-051).
    raise ValueError(f"no payload rule for capability {capability!r}")


def _dog_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the trusted dog facts, dropping anything the caller did not resolve.

    Same rule as ``context.location``: only the caller's structured values reach a payload,
    never model output. A malformed or empty entry yields None rather than an error, because
    a missing profile must not turn an answerable question into a failed request — Life
    answers without it exactly as it did before B4.
    """
    dog = context.get("dog")
    if not isinstance(dog, Mapping):
        return None
    resolved: dict[str, Any] = {}
    breed = dog.get("breed")
    if isinstance(breed, str) and breed.strip():
        resolved["breed"] = breed
    age_months = dog.get("age_months")
    if isinstance(age_months, int) and not isinstance(age_months, bool) and age_months >= 0:
        resolved["age_months"] = age_months
    # Care facts (#331): the same whitelist rule, one field at a time — a malformed care
    # value drops that field, not the breed next to it. ``on_medication`` passes only as
    # ``True``; ``False`` would claim a fact the profile cannot state (blank = unknown).
    feeding_style = dog.get("feeding_style")
    if feeding_style in ("free", "scheduled"):
        resolved["feeding_style"] = feeding_style
    health_conditions = dog.get("health_conditions")
    if isinstance(health_conditions, str) and health_conditions.strip():
        resolved["health_conditions"] = health_conditions.strip()[:200]
    if dog.get("on_medication") is True:
        resolved["on_medication"] = True
    return resolved or None


_CARE_LOG_COUNTS = ("meal", "medication", "snack", "walk")
_CARE_LOG_LAST = ("last_meal_at", "last_medication_at", "last_snack_at")
_CARE_LOG_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CARE_LOG_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _care_log_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read today's care summary, dropping anything the caller did not resolve (#344).

    Same rule as ``_dog_context``: only the caller's structured values reach a payload,
    never model output, and a malformed field drops that field rather than the request.
    The caller is ``routers/assistant.py`` `_with_dog_context`, which already proved ownership
    and reduced the day to counts and last times (``services/care_log_context``).

    **This whitelist knows no ``note`` and no event list.** ``CareLogContext`` would reject
    them downstream, but the reason is upstream of the type: the owner's free text must not
    sit next to the prompt's instructions, and a summary is all the answer needs. A summary
    without a single count is no summary — it yields None, not an empty block.
    """
    care_log = context.get("care_log")
    if not isinstance(care_log, Mapping):
        return None
    day = care_log.get("day")
    if not isinstance(day, str) or not _CARE_LOG_DAY.match(day):
        return None
    resolved: dict[str, Any] = {"day": day}
    for kind in _CARE_LOG_COUNTS:
        count = care_log.get(kind)
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 200:
            resolved[kind] = count
    for key in _CARE_LOG_LAST:
        clock = care_log.get(key)
        if isinstance(clock, str) and _CARE_LOG_CLOCK.match(clock):
            resolved[key] = clock
    if not any(kind in resolved for kind in _CARE_LOG_COUNTS):
        return None
    return resolved


def _walk_activity_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """오늘 기록된 산책 합계를 읽는다 (D-073). `_care_log_context` 와 같은 규칙 — 모양이
    틀린 칸은 그 칸만 버리고 요청은 안 버린다. 단, **``last_started_at`` 만 그 규칙을
    받는다.** ``day``·``walk_count``·``measured_walk_count``·``distance_m``·``moving_s``
    는 `WalkActivityContext` 에서 전부 필수라 하나라도 모양이 틀리면(또는
    ``measured_walk_count`` 가 ``walk_count`` 를 넘으면) 그 칸만 빼는 것이 아니라 블록
    전체를 버린다 — 안 그러면 `WalkActivityContext` 의 검증기가 런타임에 터진다.

    캐어 로그와 같은 이유로 좌표는 여기서도 안 읽는다: `WalkActivityContext` 자체가 좌표
    칸을 안 갖고 있어(위 계약 독스트링), 있어도 버려질 값이라 아예 옮기지 않는다.

    caller 는 `routers/assistant.py` `_with_dog_context` 이고, 이미 소유권을 확인해 읽고
    하루를 합계로 좁혔다 (`services/walk_activity_context`).
    """
    walk_activity = context.get("walk_activity")
    if not isinstance(walk_activity, Mapping):
        return None
    day = walk_activity.get("day")
    if not isinstance(day, str) or not _CARE_LOG_DAY.match(day):
        return None
    walk_count = walk_activity.get("walk_count")
    measured_walk_count = walk_activity.get("measured_walk_count")
    distance_m = walk_activity.get("distance_m")
    moving_s = walk_activity.get("moving_s")
    if (
        not isinstance(walk_count, int)
        or isinstance(walk_count, bool)
        or not 0 <= walk_count <= 200
        or not isinstance(measured_walk_count, int)
        or isinstance(measured_walk_count, bool)
        or not 0 <= measured_walk_count <= 200
        or measured_walk_count > walk_count
        or not isinstance(distance_m, int)
        or isinstance(distance_m, bool)
        or not 0 <= distance_m <= 500_000
        or not isinstance(moving_s, int)
        or isinstance(moving_s, bool)
        or not 0 <= moving_s <= 86_400
    ):
        return None
    resolved: dict[str, Any] = {
        "day": day,
        "walk_count": walk_count,
        "measured_walk_count": measured_walk_count,
        "distance_m": distance_m,
        "moving_s": moving_s,
    }
    last_started_at = walk_activity.get("last_started_at")
    if isinstance(last_started_at, str) and _CARE_LOG_CLOCK.match(last_started_at):
        resolved["last_started_at"] = last_started_at
    return resolved


_VET_SPEND_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _vet_spend_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the trusted recent vet-spend summary, dropping anything the caller did not
    resolve (#353 Task 7). Same whitelist rule as ``_care_log_context``: only the caller's
    structured values reach a payload, never model output, and a malformed field drops
    that field (or, for ``last_visit``, the whole block — a partial last visit is not a
    fact worth stating) rather than the request.

    The caller is ``routers/assistant.py`` `_with_dog_context`, which already proved
    ownership and narrowed confirmed ``vet_visits`` rows to this shape
    (``services/vet_spend_context``).

    **This whitelist knows no ``reason_detail``, no ``raw_ocr_items``, and no
    ``hospital_address``.** ``VetSpendContext`` would reject the first as an unknown field
    downstream, but the reason is upstream of the type — the same reason
    ``_care_log_context`` never learns ``note``.
    """
    vet_spend = context.get("vet_spend")
    if not isinstance(vet_spend, Mapping):
        return None
    month_total = vet_spend.get("month_total_krw")
    visit_count = vet_spend.get("visit_count_30d")
    last_visit = vet_spend.get("last_visit")
    if (
        not isinstance(month_total, int)
        or isinstance(month_total, bool)
        or month_total < 0
        or not isinstance(visit_count, int)
        or isinstance(visit_count, bool)
        or visit_count < 0
        or not isinstance(last_visit, Mapping)
    ):
        return None
    resolved_last = _vet_last_visit(last_visit)
    if resolved_last is None:
        return None
    resolved: dict[str, Any] = {
        "month_total_krw": month_total,
        "visit_count_30d": visit_count,
        "last_visit": resolved_last,
    }
    by_reason = vet_spend.get("by_reason_12m")
    if isinstance(by_reason, Mapping):
        filtered = {
            key: value
            for key, value in by_reason.items()
            if isinstance(key, str)
            and key.strip()
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if filtered:
            resolved["by_reason_12m"] = filtered
    return resolved


def _vet_last_visit(last_visit: Mapping[str, Any]) -> dict[str, Any] | None:
    """The three required facts plus the two optional contact fields, or None if any
    required fact is missing or malformed."""
    date_value = last_visit.get("date")
    reason = last_visit.get("reason")
    total_krw = last_visit.get("total_krw")
    if (
        not isinstance(date_value, str)
        or not _VET_SPEND_DATE.match(date_value)
        or not isinstance(reason, str)
        or not reason.strip()
        or not isinstance(total_krw, int)
        or isinstance(total_krw, bool)
        or total_krw < 0
    ):
        return None
    resolved: dict[str, Any] = {"date": date_value, "reason": reason, "total_krw": total_krw}
    hospital = last_visit.get("hospital")
    if isinstance(hospital, str) and hospital.strip():
        resolved["hospital"] = hospital
    phone = last_visit.get("phone")
    if isinstance(phone, str) and phone.strip():
        resolved["phone"] = phone
    return resolved


def _screening_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the recorded screening verdict, dropping anything the caller did not resolve.

    Same rule as ``_dog_context``: only the caller's structured values reach a payload,
    never model output, and a malformed entry yields None rather than an error. The caller
    is ``routers/assistant.py`` `_with_screening_context`, which already proved ownership
    and narrowed the record to two fields (#307).

    **This function must never learn a third field.** The lesion name is wrong 56.6% of the
    time on holdout (D-023) and `stage1` is uncalibrated, so what keeps that defence standing
    is that no code path speaks either — invariant 15 in `docs/orchestration/contracts.md`.
    Widening the whitelist here would not raise; it would just quietly put a wrong lesion
    name in a prompt.
    """
    screening = context.get("screening")
    if not isinstance(screening, Mapping):
        return None
    verdict = screening.get("verdict")
    if verdict not in _SCREENING_VERDICTS:
        return None
    days_ago = screening.get("days_ago")
    if not isinstance(days_ago, int) or isinstance(days_ago, bool) or days_ago < 0:
        return None
    return {"verdict": verdict, "days_ago": days_ago}


def screening_history(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the earlier screenings of the same dog, entry by entry (#79 3번).

    The same whitelist as ``_screening_context``, applied per entry, and for the same reason:
    a list of the narrow thing is only narrow while every element goes through the filter.
    Invariant 15 does not weaken with count — widening this loop would not raise either.

    **Bad entries are dropped, not raised on, and the list is truncated rather than refused.**
    The caller already capped it (``services/screening_context.py``), so an over-long list
    here means a bug upstream — but failing the request would turn an answerable question
    into an error over a defect the user cannot see or fix. Truncating keeps the contract's
    promise (`ScreeningHistory` would reject the long list downstream) without that cost.

    **공개인 것은 `aggregate` 도 같은 좁힘을 지나야 하기 때문입니다.** 답변에 붙는 이력 절이
    payload 와 다른 경로로 컨텍스트를 읽으면 좁힘이 두 벌이 되고, 한쪽만 넓어져도 아무것도
    안 깨집니다 — 능력 이름 사본이 셋이던 자리(#269)가 만든 습관입니다.
    """
    history = context.get("screening_history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return None
    entries: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        verdict = entry.get("verdict")
        if verdict not in _SCREENING_VERDICTS:
            continue
        days_ago = entry.get("days_ago")
        if not isinstance(days_ago, int) or isinstance(days_ago, bool) or days_ago < 0:
            continue
        entries.append({"verdict": verdict, "days_ago": days_ago})
        if len(entries) == SCREENING_HISTORY_LIMIT:
            break
    return {"entries": entries} if entries else None


def _missing_coordinates(context: dict[str, Any]) -> list[str]:
    """Which trusted coordinate keys are absent or outside the assistant's box.

    Out-of-range is treated as missing rather than clamped: a coordinate we do not
    trust is not a coordinate, and silently moving the user somewhere inside the box
    would answer confidently about the wrong place.
    """
    location = context.get("location")
    if not isinstance(location, Mapping):
        return ["location.lat", "location.lon"]
    missing = []
    for key, low, high in _COORDINATE_BOUNDS:
        value = location.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not low <= value <= high
        ):
            missing.append(f"location.{key}")
    return missing


def _clarify_question(missing: list[str], *, needs: frozenset[str] | set[str]) -> str:
    """The CLARIFY sentence, chosen by what the missing coordinates were for.

    Only the wording varies — `clarify.missing` carries the same keys either way, and
    the frozen router benchmark compares that list, never this text.
    """
    if "walk" in needs and "place" in needs:
        if len(missing) == 2:
            return "지금 위치의 위도와 경도를 알려주시면 산책 조건과 주변 장소를 함께 찾아볼게요."
        if missing == ["location.lat"]:
            return "지금 위치의 위도를 알려주세요."
        return "지금 위치의 경도를 알려주세요."
    if "place" in needs:
        if len(missing) == 2:
            return "장소를 찾을 위치의 위도와 경도를 알려주세요."
        if missing == ["location.lat"]:
            return "장소를 찾을 위치의 위도를 알려주세요."
        return "장소를 찾을 위치의 경도를 알려주세요."
    if len(missing) == 2:
        return "산책할 위치의 위도와 경도를 알려주세요."
    if missing == ["location.lat"]:
        return "현재 위치의 위도를 알려주세요."
    return "현재 위치의 경도를 알려주세요."


__all__ = [
    "assemble_route_plan",
    "resolve_care_log_route",
    "resolve_care_log_write",
    "resolve_deterministic_route",
    "resolve_emergency_route",
    "resolve_skin_route",
]
