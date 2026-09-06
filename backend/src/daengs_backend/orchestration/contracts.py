"""Approved orchestration contracts from D-033 and D-034 as Python types."""

from __future__ import annotations

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


class LifePayload(ContractModel):
    question: str = Field(min_length=1, max_length=500)
    dog: DogContext | None = None


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
    """

    question: str = Field(min_length=1, max_length=1_000)
    dog: DogContext | None = None


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
    "TrainingPayload",
    "WalkPayload",
]
