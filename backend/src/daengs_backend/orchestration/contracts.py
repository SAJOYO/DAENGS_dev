"""Approved orchestration contracts from D-033 and D-034 as Python types."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CapabilityName(StrEnum):
    TRAINING = "training"
    LIFE = "life"
    WALK = "walk"
    PLACE = "place"
    #: The general-answer fallback (#279). An executable capability with an adapter, but
    #: NOT a router destination: `semantic.ExecuteName` deliberately omits it. The planner
    #: assembles it by a deterministic rule when the router selected nothing, so the model
    #: can never trade an evidence-backed capability for an ungrounded answer.
    GENERAL = "general"
    #: 응급 발화의 병원 연락 능력. GENERAL 과 마찬가지로 `semantic.ExecuteName` 에 없지만
    #: 이유가 정반대다 — GENERAL 은 모델이 근거 있는 능력과 바꿔치기하지 못하게 뺐고,
    #: 이것은 **모델을 아예 안 태우려고** 뺐다. 결정론적 어휘 게이트와 명시 신호로만 들어온다.
    VET_CONTACT = "vet_contact"
    #: 케어 기록 쓰기 (#331 후속, D-075). **이 저장소에서 유일하게 쓰는 능력이다.**
    #:
    #: `vet_contact` 와 같은 이유로 `semantic.ExecuteName` 에 없다 — 모델을 아예 안 태운다.
    #: 다만 여기서는 한 단계 더 좁다: `vet_contact` 는 어휘 게이트가 **질의 원문**으로 열지만,
    #: 이것은 **사용자가 앞 턴의 제안에 승낙했을 때만** 열린다 (`planner.resolve_care_log_write`).
    #: 라우터가 낼 수 있는 것은 같은 뜻의 HANDOFF 하나뿐이고, 그 HANDOFF 는 아무것도 안 쓴다.
    CARE_LOG = "care_log"


class CapabilityStatus(StrEnum):
    OK = "OK"
    ABSTAINED = "ABSTAINED"
    REFUSED = "REFUSED"
    PENDING = "PENDING"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class AssistantStatus(StrEnum):
    ANSWERED = "ANSWERED"
    PARTIAL = "PARTIAL"
    CLARIFY = "CLARIFY"
    HANDOFF = "HANDOFF"
    UNCERTAIN = "UNCERTAIN"
    REFUSED = "REFUSED"
    PENDING = "PENDING"
    FAILED = "FAILED"


class RouterKind(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class PrincipalContext(ContractModel):
    """Already-verified identity metadata; raw credentials have no field here."""

    subject: str = Field(min_length=1)
    kind: Literal["ADMIN", "APP_USER"]
    permissions: tuple[str, ...] = ()


class TrainingPayload(ContractModel):
    question: str = Field(min_length=1, max_length=1_000)


class DogContext(ContractModel):
    """The dog facts Life may reason with, assembled from trusted profile data only.

    Deliberately narrow (roadmap B4). ``breed`` is a Korean breed name, already translated
    from the app's avatar id by ``services.dog_context``: the stored value is an asset id
    (``dog_pug``) that matches nothing in a Korean statute or airline tariff. What that breed
    then implies — brachycephalic, restricted — needs the corpus, so that judgement stays
    with the capability rather than this layer or its adapter.

    Age arrives already reduced to whole months. The birth date itself never crosses this
    boundary — it is personal data with no routing or answering use, and ``pets`` stores a
    date that may be the day the dog joined the family rather than its birthday, which is
    not an age at all. The caller resolves that ambiguity and sends nothing when it cannot.
    """

    breed: str | None = Field(default=None, max_length=60)
    age_months: int | None = Field(default=None, ge=0, le=360)

    # ── care facts (#331) ──────────────────────────────────────────────
    # Three more facts the general-answer fallback may reason with, still from the
    # trusted profile only. Life keeps reading breed/age alone (``adapters/life.py``).
    #
    # **No drug name crosses here, ever.** ``pets.medications`` is free text and stays in
    # the profile; only *whether* the dog is on regular medication arrives, and only as
    # ``True`` — an empty medication field means "unknown", never "not medicated". The
    # fallback prompt refuses drug/dosage questions; a drug name in DOG_CONTEXT would turn
    # that refusal into a hint. Feeding *times* stay out too: their consumers are the care
    # log and reminders, not an answer.
    feeding_style: Literal["free", "scheduled"] | None = None
    health_conditions: str | None = Field(default=None, max_length=200)
    on_medication: Literal[True] | None = None


#: ``HH:MM`` on a 24-hour clock — the only shape a "last time" may take here.
_CLOCK_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class CareLogContext(ContractModel):
    """What the owner already logged for this dog today: counts per kind and the last time each.

    Assembled by ``services/care_log_context`` from the care log (#332) for the general-answer
    fallback only (#344). The point is the sentence "오늘 아침 약이 아직 체크 안 됐어요", and
    counts plus last times are all that sentence needs.

    **No note.** ``care_events.note`` is the owner's free text ("약 반만"); it would be the one
    place user-written words sit next to the instructions in a prompt, and it adds nothing
    a count and a time do not. **No event list** for the same reason — the day is already
    summarised. **No dosage, no schedule**: the log records what happened, and the prompt rule
    forbids inferring either from it. Times are ``HH:MM`` in the log's day timezone (Seoul),
    never a timestamp — the model needs "this morning", not an instant.

    A day with nothing logged never reaches here (the resolver returns None): an empty log
    means "the owner does not use the log", not "nothing was done", and the prompt must not
    say either.
    """

    day: date
    meal: int = Field(default=0, ge=0, le=200)
    medication: int = Field(default=0, ge=0, le=200)
    snack: int = Field(default=0, ge=0, le=200)
    walk: int = Field(default=0, ge=0, le=200)
    last_meal_at: str | None = Field(default=None, pattern=_CLOCK_PATTERN)
    last_medication_at: str | None = Field(default=None, pattern=_CLOCK_PATTERN)
    last_snack_at: str | None = Field(default=None, pattern=_CLOCK_PATTERN)


class CareLogKind(StrEnum):
    """기록할 수 있는 케어 종류. `care_events.kind` 의 CHECK 제약과 같은 값이다 (#332).

    **`walk` 가 없다.** 산책은 `walks` 가 진실이라 `care_events` 에도 없고, 여기에도 없다 —
    한 사실이 두 곳에 있으면 반드시 어긋난다 (`db/init/23_care_events.sql` 머리말).

    `schemas/care_event.CareEventKind`(Literal)의 **사본**이다. 두 벌인 것은 방향 때문이다:
    orchestration 계약은 HTTP 스키마를 import 하지 않는다(D-035 의 반대 방향). 사본끼리는
    `tests/test_assistant_care_log_write.py` 가 대조한다 (`aggregate._SCREENING_VERDICTS` 와
    같은 장치).
    """

    MEAL = "meal"
    MEDICATION = "medication"
    SNACK = "snack"


class CareLogProposal(ContractModel):
    """"이대로 기록할까요?" 의 **이대로** — 그리고 승낙 뒤 실제로 쓰이는 값 (#331 후속, D-075).

    한 타입이 제안과 payload 를 겸하는 것이 의도다. 확인 단계의 약속은 "보여 준 것만
    들어간다" 이고, 제안과 payload 가 다른 타입이면 그 약속을 **코드가 아니라 사람이** 지켜야
    한다 — 필드를 하나 더한 payload 는 아무 검증도 안 걸리고 통과한다.

    **모델이 만든 값이 하나도 없다** (D-051). `kind` 는 결정론 어휘가 읽고
    (`care_log.kind_of`), `occurred_at` 은 **서버 시계**이고, `pet_id` 는 신뢰된
    `context["active_dog_id"]` 이고, `proposal_id` 는 서버가 만든 UUID 다.

    `occurred_at` 이 제안 시점인 이유: 이 기능이 받는 말은 "방금 먹였어" 다. 사용자가 말한
    시점이 곧 챙긴 시점이고, 그 값을 사용자가 확인 문장에서 눈으로 보고 승낙한다. 지난 시각을
    적는 것은 기록 화면의 일이다 — 그쪽은 시각을 손으로 고른다.

    `proposal_id` 가 `care_events.client_event_id` 로 간다. 그 칸은 원래 **앱이** 만드는
    멱등키인데(`db/init/23_care_events.sql`) 이 경로에서는 서버가 만든다 — 채팅에는 그 키를
    만들 앱 코드가 없고, 같은 제안에 두 번 "네" 라고 답해도 한 줄이어야 한다. 키의 출처가
    갈리는 것은 감수한 것이고, 유일성은 어느 쪽이 만들어도 같은 UNIQUE 가 보장한다.
    """

    kind: CareLogKind
    pet_id: UUID
    occurred_at: datetime
    proposal_id: UUID

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_aware(cls, value: datetime) -> datetime:
        # `schemas/care_event.CareEventCreate` 와 같은 규칙. naive 로 받으면 어느 하루에
        # 넣을지 서버가 추측하게 되고, 이 값은 `public_response` 로 JSON 왕복을 한다.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at requires a timezone")
        return value


class WalkActivityContext(ContractModel):
    """오늘 앱이 **실제로 기록한** 산책: 건수, 그중 측정이 끝난 건수, 그 합계 거리와 이동 시간.

    `CareLogContext` 의 형제이고 규칙이 같다 — 소유권을 확인해 읽고, 좁혀서 넘기고, 없으면
    None. 다른 것은 **무엇을 빼느냐**다.

    **좌표가 한 칸도 없다.** 위경도 · 폴리라인 · 지점 목록이 여기 있으면 D-051 이 "지명은
    좌표가 아니다" 로 막아 둔 것을 뒷문으로 여는 셈이 된다 — 모델이 경로를 받으면 그것으로
    다른 경로를 추정한다. 답 문장에 필요한 것은 합계뿐이다.

    **`walk_count` 와 `measured_walk_count` 가 따로인 것이 요점이다.** 한 산책에 분석 행이
    여러 개 달릴 수 있고(`walk_analyses` 의 유니크 제약이 6칸이다), 봉인이 안 끝난 산책도
    있다. 합계는 **측정이 끝난 것만** 더한 값이고, 둘이 다르면 답이 그 사실을 말한다 —
    "3건 중 2건만 계산됐어요" 는 참이지만 "3건에 1.2km" 는 거짓이다.

    **거리를 km 로 미리 나누지 않는다.** 반올림은 답을 쓰는 자리에서 하고, 계약은 원값을
    나른다. `CareLogContext` 가 시각을 타임스탬프가 아니라 `HH:MM` 로 나르는 것과 반대
    방향처럼 보이지만 이유는 같다 — 소비자가 필요로 하는 모양으로만 준다.

    기록이 하나도 없는 날은 여기 안 온다(resolver 가 None 을 낸다): 빈 기록은 "안 걸었다"
    가 아니라 "이 기능을 안 쓴다" 일 수 있고, 프롬프트가 둘 중 어느 쪽도 말하면 안 된다.
    """

    day: date
    walk_count: int = Field(ge=0, le=200)
    measured_walk_count: int = Field(ge=0, le=200)
    #: 측정이 끝난 산책의 합계 거리(m). 하루 500km 를 넘는 값은 기록이 아니라 사고다.
    distance_m: int = Field(ge=0, le=500_000)
    #: 같은 산책들의 합계 이동 시간(s). 하루를 넘을 수 없다.
    moving_s: int = Field(ge=0, le=86_400)
    #: 마지막 산책이 시작된 시각. `CareLogContext` 와 같은 `HH:MM`(서울)이고 타임스탬프가 아니다.
    last_started_at: str | None = Field(default=None, pattern=_CLOCK_PATTERN)

    @model_validator(mode="after")
    def measured_never_exceeds_recorded(self) -> WalkActivityContext:
        if self.measured_walk_count > self.walk_count:
            raise ValueError("measured_walk_count cannot exceed walk_count")
        return self


class LastVetVisitContext(ContractModel):
    """The single most recent confirmed vet visit: when, what it was for, and how much.

    ``reason`` is the reason code's **display label** ("피부"), not the code ("skin") —
    see ``VetSpendContext`` for why the two must not disagree inside one prompt.
    ``hospital``/``phone`` are the hospital's own contact details, present only when the
    owner's confirmed record has them; the address never crosses (module docstring on
    ``VetSpendContext``).
    """

    date: date
    reason: str = Field(min_length=1, max_length=20)
    total_krw: int = Field(ge=0, le=100_000_000)
    hospital: str | None = Field(default=None, max_length=60)
    phone: str | None = Field(default=None, max_length=32)


class VetSpendContext(ContractModel):
    """What the owner has confirmed about this dog's vet visits, for the general-answer
    fallback only (#353 Task 7 — the twin of ``CareLogContext``, #344).

    Assembled by ``services/vet_spend_context`` from confirmed ``vet_visits`` rows. The
    point is sentences like "피부로 1년간 32만원 썼고, 마지막은 9/2 ○○동물병원" and "그
    병원 번호 뭐였지" — a running total, a recent-activity count, the last visit's facts,
    and a per-reason breakdown are all that needs.

    **``by_reason_12m`` keys are display labels, the same ones ``last_visit.reason``
    uses — never the underlying reason code.** Within one prompt, showing "피부" next to
    an aggregate keyed "skin" gives the model two names for the same thing and invites it
    to answer with the English one to a Korean-speaking user.

    **No ``reason_detail``.** The owner's free-text note on a confirmed visit is exactly
    the kind of user-written string that must not sit beside a prompt's instructions —
    the same reason ``CareLogContext`` excludes ``note`` (#344). **No ``raw_ocr_items``**:
    those are receipt line items, already excluded from the extraction schema for personal
    data (docs/vet-visits.md §2) and no more useful to an answer than the amounts already
    here. **No ``hospital_address``**: the prompt only ever says "이 병원" and a phone
    number, never a street.

    **No ``emergency_count_12m`` or ``oncology_total_12m``.** The approved scope for this
    card is last visit + month total + per-reason totals; those two columns are recorded as
    undecided (docs/vet-visits.md "열린 것") and stay out until scope is widened on purpose.

    A dog with no confirmed visits never reaches here (the resolver returns None): an
    empty history means "the owner does not use this feature", not "spent nothing", and
    the prompt must not say either.
    """

    month_total_krw: int = Field(ge=0, le=100_000_000_000)
    visit_count_30d: int = Field(ge=0, le=10_000)
    last_visit: LastVetVisitContext
    by_reason_12m: dict[str, int] = Field(default_factory=dict)


class ScreeningContext(ContractModel):
    """A recorded skin screening this question follows on from: what it concluded, and how long ago.

    Deliberately two fields, and the exclusions are the point (#307).

    **No lesion identity.** ``stage2.distribution`` and ``stage2.group`` name what the model
    thought it saw, and the two-stage model's lesion name is wrong 56.6% of the time on
    holdout — which is why the screening contract has no ``top1`` field at all (D-023). What
    keeps that defence standing today is that no code path speaks the name; a field here
    would demote it to a prompt instruction. It also buys nothing downstream: the ordinances
    and subsidy programmes in the Life corpus do not enumerate 구진·플라크.

    **No control copy.** ``headline``/``body``/``action``/``disclaimer`` must reach the user
    unmodified (D-023, PR #79). They belong on the screen the app already drew, not in a
    prompt where a model could paraphrase them.

    **No probability.** ``stage1`` is uncalibrated (``calibrated=false``), so "62%" orders
    correctly but is not a frequency. Rather than instruct a model not to read it as one,
    the value simply is not here. Revisit when calibration lands.

    ``days_ago`` rather than a timestamp: the caller resolves the clock, and a date is
    personal detail that no answer needs.
    """

    verdict: Literal["normal", "abnormal", "retake"]
    days_ago: int = Field(ge=0, le=3_650)


#: How many earlier screenings ``ScreeningHistory`` may carry. Three is a cap, not a target:
#: the entries answer "there are earlier records, and this is what they concluded", and a
#: fourth verdict does not make that sentence truer. An unbounded list would also grow with
#: the dog rather than with the question, and the whole of it would sit in a saved chat turn.
SCREENING_HISTORY_LIMIT = 3


class ScreeningHistory(ContractModel):
    """Earlier screenings of the same dog: the same two facts, one entry each, newest first.

    Every exclusion on ``ScreeningContext`` holds here unchanged — a list of the narrow thing
    is still narrow, and history is not a reason to widen it (#79 3번). The entries are
    ``ScreeningContext`` itself rather than a looser sibling precisely so that no second,
    laxer definition of "a screening we may talk about" can appear.

    **Nothing here says a dog got better or worse.** Two verdicts and their ages are not a
    trend: the two-stage lesion name is wrong 56.6% of the time on holdout and ``stage1`` is
    uncalibrated (D-023), so the difference between two screenings may be the model's noise
    rather than the dog's skin. What downstream may do with this is state that earlier records
    exist and what each concluded; the judgement ends at recommending a vet. The contract
    enforces the half of that it can — there is no probability, no lesion name and no
    comparison field to compute a trend from.
    """

    entries: list[ScreeningContext] = Field(max_length=SCREENING_HISTORY_LIMIT)


class LifePayload(ContractModel):
    question: str = Field(min_length=1, max_length=500)
    dog: DogContext | None = None
    #: The recorded screening this question follows on from (#283). Same rule as ``dog``:
    #: the caller resolved it, the planner only copies it, and ``None`` means Life answers
    #: exactly as it did before this field existed. The narrowing that makes it safe to put
    #: in a prompt lives on ``ScreeningContext`` itself, not here.
    screening: ScreeningContext | None = None
    #: Earlier screenings of the same dog (#79 3번). Same rule again — the caller resolved it
    #: and the planner only copies it. It arrives separately from ``screening`` rather than
    #: nested inside it because the two are independently absent: a first-ever record has no
    #: history, and a record whose own verdict failed still has one.
    screening_history: ScreeningHistory | None = None


class WalkPayload(ContractModel):
    lat: float = Field(ge=33.0, le=39.0)
    lon: float = Field(ge=124.0, le=132.0)


class PlacePayload(ContractModel):
    """Non-personalized Place input assembled from the original query and trusted location."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    query: str = Field(min_length=1, max_length=1_000)
    lat: float = Field(ge=33.0, le=39.0)
    lon: float = Field(ge=124.0, le=132.0)

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class GeneralPayload(ContractModel):
    """The fallback's input: the same trusted facts Life gets, nothing more (#279).

    The question is the user's exact words and ``dog`` comes only from the resolved
    profile (``planner._dog_context``). No coordinates — the fallback must not answer
    "where" or "is now a good time"; those are Place and Walk, and they were not selected.

    ``care_log`` (#344) is today's care summary from the trusted log, and it comes here
    **only** — Life answers from ordinances and subsidy documents, which today's meal count
    does not change, and Training never sees dog facts at all.

    ``vet_spend`` (#353 Task 7) is the same rule applied to confirmed vet visits: "피부로
    1년간 얼마 썼지" and "그 병원 번호 뭐였지" are general questions, and Life's documents
    do not carry either answer.

    ``walk_activity`` (D-073) 는 오늘 기록된 산책의 합계다. 여기 **좌표가 없는 것이 설계**이고,
    이유는 `WalkActivityContext` 독스트링에 있다.
    """

    question: str = Field(min_length=1, max_length=1_000)
    dog: DogContext | None = None
    care_log: CareLogContext | None = None
    vet_spend: VetSpendContext | None = None
    #: 오늘 기록된 산책 (D-073). `care_log`·`vet_spend` 와 같은 규칙 — 폴백에만 오고,
    #: Life 의 조례·보조금 문서는 오늘 걸은 거리로 달라지지 않는다. None 이면 프롬프트가
    #: 이 카드 전과 한 글자도 다르지 않다.
    walk_activity: WalkActivityContext | None = None
    #: 대화 맥락 (#416). **이력 원문이 아니다** — Turn Resolver(`orchestration/resolver.py`)가
    #: 만든 제한된 구조화 컨텍스트다. `relation=NEW` 이거나 확신이 낮으면 `None` 이고,
    #: 그것이 프롬프트를 오늘과 바이트 동일하게 유지하는 방법이다(#416 Task 5).
    conversation: ConversationContext | None = None


class VetContactPayload(ContractModel):
    """응급 병원 연락의 입력. 질의 원문을 싣지 않는다 — 검색어가 아니라 좌표로만 찾는다.

    좌표가 ``None`` 일 수 있는 것이 이 payload 의 요점이다. `vet_contact` 는
    `planner._NEEDS_COORDINATES` 에 들어가지 않으므로 좌표가 없어도 CLARIFY 가 걸리지
    않는다 — 응급에 "위도를 알려주세요" 로 되묻는 것이 최악이기 때문이다. 대신 adapter 가
    ABSTAINED + `vet_contact.location_required` 로 끝낸다.

    반쪽 좌표는 거부한다. 신뢰하지 않는 좌표는 좌표가 아니라는 D-051 ③ 의 처분과 같다.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float | None = Field(None, ge=33.0, le=39.0)
    lon: float | None = Field(None, ge=124.0, le=132.0)
    #: planner 가 채운다. adapter 가 시계를 읽으면 테스트가 시계에 묶인다 —
    #: 이 저장소는 `SearchMust.judge_at`·`evaluated_at` 으로 시각을 인자로 넘긴다.
    at_night: bool

    @model_validator(mode="after")
    def coordinates_come_as_a_pair(self) -> VetContactPayload:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class FacilitySessionPayload(ContractModel):
    """Continue an owner-bound facility view; coordinates belong to the saved search."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    query: str = Field(min_length=1, max_length=1_000)
    facility_session_id: UUID

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


CapabilityPayload = (
    TrainingPayload
    | LifePayload
    | WalkPayload
    | PlacePayload
    | FacilitySessionPayload
    | GeneralPayload
    | VetContactPayload
    | CareLogProposal
)
_PAYLOAD_TYPES = {
    CapabilityName.TRAINING: TrainingPayload,
    CapabilityName.LIFE: LifePayload,
    CapabilityName.WALK: WalkPayload,
    CapabilityName.PLACE: PlacePayload,
    CapabilityName.GENERAL: GeneralPayload,
    CapabilityName.VET_CONTACT: VetContactPayload,
    # 제안과 payload 가 같은 타입이다 — 확인 단계의 약속("보여 준 것만 들어간다")을
    # 사람이 아니라 타입이 지키게 하려는 것이고, 이유는 `CareLogProposal` 독스트링에 있다.
    CapabilityName.CARE_LOG: CareLogProposal,
}


class CapabilityRequest(ContractModel):
    capability: CapabilityName
    payload: CapabilityPayload
    timeout_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def parse_capability_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        capability = CapabilityName(data.get("capability"))
        payload_type = _PAYLOAD_TYPES[capability]
        payload = data.get("payload")
        if capability == CapabilityName.PLACE and (
            isinstance(payload, FacilitySessionPayload)
            or isinstance(payload, dict)
            and "facility_session_id" in payload
        ):
            payload_type = FacilitySessionPayload
        data["payload"] = payload_type.model_validate(data.get("payload"))
        return data

    @model_validator(mode="after")
    def payload_matches_capability(self) -> CapabilityRequest:
        expected = _PAYLOAD_TYPES[self.capability]
        if self.capability == CapabilityName.PLACE and isinstance(
            self.payload, FacilitySessionPayload
        ):
            return self
        if not isinstance(self.payload, expected):
            raise TypeError(f"{self.capability.value} requires {expected.__name__}")
        return self


class Handoff(ContractModel):
    target: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class ObservationAxis(StrEnum):
    """되묻기가 사용자에게 물을 수 있는 **관찰 항목**의 닫힌 목록 (#415 · D-068).

    **이것은 강아지의 상태가 아니라 "아직 물어본 항목" 입니다.** `ClarifyRequest.missing_axes`
    에만 실리고, 어디에서도 반려견의 사실이나 기록으로 저장되지 않습니다 — 값이 `APPETITE`
    라는 것은 "식욕을 물었다" 는 뜻이지 "식욕에 문제가 있다" 가 아닙니다.

    목록은 `#415` 의 수용 케이스가 실제로 요구하는 **최소**입니다. 앞의 다섯은 승인된 질문
    문구의 축(식욕 · 활력 · 배변 · 구토/설사 · 호흡)이고, `MOBILITY` 는 그 문구에는 없지만
    수용 케이스에 있어서 있습니다 — `cq_repeat_after_failure_01` 이 "발을 전다" 입니다.
    `OTHER` 로 다 밀어 넣지 않으려고 먼저 세운 것이 이 목록입니다.

    **늘리는 것은 의도된 행동이어야 합니다** — `tests/test_orchestration_ask_mode.py` 가 이
    목록을 그대로 고정합니다. `#416` 의 Turn Resolver 가 이 어휘로 후속 답변을 앞 질문에
    묶으므로, 값을 더하거나 이름을 바꾸면 그쪽 결합도 같이 봐야 합니다.
    """

    APPETITE = "APPETITE"
    ENERGY = "ENERGY"
    STOOL = "STOOL"
    VOMIT = "VOMIT"
    BREATHING = "BREATHING"
    MOBILITY = "MOBILITY"
    OTHER = "OTHER"


class ClarifyRequest(ContractModel):
    question: str = Field(min_length=1, max_length=500)
    #: **어떤 종류의 되묻기인가.** 좌표 게이트는 `location.lat` 같은 키를, 관찰 되묻기는
    #: `observation` 을 넣는다. 두 어휘를 한 목록에 섞지 않으려고 축은 아래 칸에 따로 둔다.
    missing: list[str] = Field(min_length=1)
    #: **무엇을 물었는가** — 모델이 고른 관찰 항목 1~2개 (#415). 기본값이 비어 있고,
    #: **코드가 채우는 길은 없다**: 질문 문장이 식욕을 언급했다고 해서 `APPETITE` 를
    #: 넣어 주지 않는다. 모델이 안 고르면 빈 채로 나가고, `#416` 은 그것을 "축을 모른다"
    #: 로 읽어야지 "물은 것이 없다" 로 읽으면 안 된다.
    missing_axes: list[ObservationAxis] = Field(default_factory=list, max_length=2)
    #: **이 되묻기가 승낙을 받으려는 기록** (#331 후속, D-075). 케어 기록 확인일 때만 채워지고,
    #: 다른 되묻기(좌표 게이트 · 관찰 되묻기)에서는 늘 `None` 이다.
    #:
    #: 새 칸도 새 테이블도 필요 없다 — `services/chat.public_response_of` 가
    #: `model_dump(mode="json")` 라 이 값은 `chat_turns.public_response` 에 통째로 저장되고,
    #: `pending_clarification_of` 가 다음 턴에 그대로 읽어 온다 (`missing_axes` 와 같은 길).
    #: 그것이 "확인 단계" 를 **상태 없이** 만드는 방법이다: 대기 중인 쓰기를 담아 둘 서버
    #: 메모리도, 만료 잡도 없다. 대기가 한 턴짜리인 것도 거기서 따라온다.
    care_log: CareLogProposal | None = None


class TurnRelation(StrEnum):
    """현재 발화가 앞 대화와 맺는 관계 (#416).

    **여기 사는 이유는 `ConversationContext` 가 여기 살기 때문이다.** `ConversationContext`
    는 (나중에) `GeneralPayload` 안에 실리는 계약 타입이라 `contracts.py` 를 벗어날 수
    없고, 그 필드 `relation` 의 타입인 이 열거형도 같이 따라온다. `AssistantStatus` 등
    공유 열거형을 "넓히지" 않는다는 규칙과는 다른 이야기다 — 그 규칙은 기존 열거형에
    값을 추가하지 말라는 것이지, 새 열거형이 이 모듈에 사는 것을 막지 않는다.
    `orchestration/resolver.py` 가 이 이름을 그대로 재수출해서, `from
    daengs_backend.orchestration.resolver import TurnRelation` 을 쓰는 기존 코드와 테스트는
    안 바뀐다.
    """

    NEW = "NEW"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    REPEAT = "REPEAT"
    META = "META"


class ConversationContext(ContractModel):
    """라우터와 선택된 capability 가 보는 **전부**. 이력 원문은 여기 없다 (#416).

    Turn Resolver (`orchestration/resolver.py`) 가 조립해서 내려보낸다.
    """

    relation: TurnRelation
    referenced_original_request: str | None = None
    #: 참조한 턴에서 **비서가 실제로 답한 내용** — 사용자가 뭘 물었는지만으로는 "아까 답을
    #: 다시 설명해줘" 류를 풀 수 없다는 실사용 실패에서 추가됐다 (followup-answer-text
    #: brief). `resolver.truncate_assistant` 를 거친 값만 들어온다.
    referenced_assistant_answer: str | None = None
    standalone_query: str | None = None
    pending_question: str | None = None
    pending_missing_axes: list[ObservationAxis] = Field(default_factory=list)


class RoutePlan(ContractModel):
    """The plan itself plus how it was reached.

    `router`/`model`/`prompt_version` are observation metadata, not routing semantics:
    the deterministic path calls no model, so its `model` and `prompt_version` are both
    None by construction. They live here rather than beside the router because both paths
    assemble their plan in `assemble_route_plan` (D-051 ②) — one assembly point means the
    two paths cannot disagree about what produced them.
    """

    requests: list[CapabilityRequest] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    clarify: ClarifyRequest | None = None
    router: RouterKind
    model: str | None = None
    prompt_version: str | None = None

    @model_validator(mode="after")
    def clarify_is_exclusive(self) -> RoutePlan:
        if self.clarify is not None and (self.requests or self.handoffs):
            raise ValueError("clarify is exclusive with requests and handoffs")
        return self


class OutcomeDetail(ContractModel):
    code: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=1_000)


class PendingJob(ContractModel):
    job_id: str = Field(min_length=1, max_length=200)
    poll: str = Field(min_length=1, max_length=1_000)


class ErrorDetail(ContractModel):
    kind: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=1_000)


class CapabilityResult(ContractModel):
    capability: CapabilityName
    status: CapabilityStatus
    data: dict[str, Any] | None = None
    abstention: OutcomeDetail | None = None
    refusal: OutcomeDetail | None = None
    job: PendingJob | None = None
    error: ErrorDetail | None = None
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def status_has_matching_metadata(self) -> CapabilityResult:
        required = {
            CapabilityStatus.ABSTAINED: ("abstention", self.abstention),
            CapabilityStatus.REFUSED: ("refusal", self.refusal),
            CapabilityStatus.PENDING: ("job", self.job),
            CapabilityStatus.ERROR: ("error", self.error),
            CapabilityStatus.TIMEOUT: ("error", self.error),
        }
        if self.status == CapabilityStatus.OK and self.data is None:
            raise ValueError("OK requires data")
        if self.status in required and required[self.status][1] is None:
            raise ValueError(f"{self.status.value} requires {required[self.status][0]}")
        return self


class RouteTrace(ContractModel):
    """Which way the request went, for console inspection only (#238).

    **Metadata, never content.** Router kind, model id and prompt version are exactly the
    fields D-037 already allows in observation; the question text, the prompt body and any
    provider payload stay out — putting those here would cross that line through the
    response instead of the log.

    `model` and `prompt_version` are None on the deterministic path: no model was called,
    so there is nothing to name. A reader must not render that as an empty model field.
    """

    router: RouterKind
    model: str | None = None
    prompt_version: str | None = None


class AssistantResponse(ContractModel):
    """The public `/assistant/query` contract.

    `route` is the one field that is not for everyone: it is attached only when the caller
    holds the console inspection permission (`routers/assistant.py`), so app members never
    receive it and it never reaches a stored chat turn. Everything above it is the reduced
    response the app consumes and must not change shape.
    """

    request_id: str = Field(min_length=1)
    status: AssistantStatus
    message: str
    results: list[CapabilityResult] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    clarify: ClarifyRequest | None = None
    route: RouteTrace | None = None


class OrchestratorState(TypedDict):
    request_id: str
    principal: PrincipalContext
    query: str
    locale: Literal["ko-KR"]
    context: dict[str, Any]
    route_plan: RoutePlan
    results: list[CapabilityResult]
    response: AssistantResponse | None
    #: May this caller see `AssistantResponse.route`? Decided at the HTTP boundary, which
    #: is the layer that knows permissions — this graph never reads a permission itself.
    include_route_trace: bool


__all__ = [
    "SCREENING_HISTORY_LIMIT",
    "AssistantResponse",
    "AssistantStatus",
    "CapabilityName",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilityStatus",
    "CareLogKind",
    "CareLogProposal",
    "ClarifyRequest",
    "ConversationContext",
    "ErrorDetail",
    "GeneralPayload",
    "Handoff",
    "LastVetVisitContext",
    "LifePayload",
    "ObservationAxis",
    "OrchestratorState",
    "OutcomeDetail",
    "PendingJob",
    "PlacePayload",
    "PrincipalContext",
    "RoutePlan",
    "RouteTrace",
    "RouterKind",
    "ScreeningContext",
    "ScreeningHistory",
    "TrainingPayload",
    "TurnRelation",
    "VetContactPayload",
    "VetSpendContext",
    "WalkActivityContext",
    "WalkPayload",
]
