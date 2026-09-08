"""Approved orchestration contracts from D-033 and D-034 as Python types."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal, TypedDict

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
    """

    question: str = Field(min_length=1, max_length=1_000)
    dog: DogContext | None = None
    care_log: CareLogContext | None = None


CapabilityPayload = TrainingPayload | LifePayload | WalkPayload | PlacePayload | GeneralPayload
_PAYLOAD_TYPES = {
    CapabilityName.TRAINING: TrainingPayload,
    CapabilityName.LIFE: LifePayload,
    CapabilityName.WALK: WalkPayload,
    CapabilityName.PLACE: PlacePayload,
    CapabilityName.GENERAL: GeneralPayload,
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
        data["payload"] = payload_type.model_validate(data.get("payload"))
        return data

    @model_validator(mode="after")
    def payload_matches_capability(self) -> CapabilityRequest:
        expected = _PAYLOAD_TYPES[self.capability]
        if not isinstance(self.payload, expected):
            raise TypeError(f"{self.capability.value} requires {expected.__name__}")
        return self


class Handoff(ContractModel):
    target: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class ClarifyRequest(ContractModel):
    question: str = Field(min_length=1, max_length=500)
    missing: list[str] = Field(min_length=1)


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
    "ClarifyRequest",
    "ErrorDetail",
    "GeneralPayload",
    "Handoff",
    "LifePayload",
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
    "WalkPayload",
]
