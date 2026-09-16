"""Short-transaction rules for product chat persistence (D-048).

External orchestration/Gemini calls are intentionally absent from turn transactions. Both a
persisted ``/assistant/query`` turn and summary generation use
reserve TX -> close session -> external call -> completion TX (``run_persisted_turn``,
``create_summary``).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityStatus,
    CareLogProposal,
    ObservationAxis,
)
from daengs_backend.orchestration.resolver import (
    MAX_CANDIDATE_PAIRS,
    PendingClarification,
    PriorTurn,
)
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.services.chat_summary import (
    PROMPT_VERSION,
    SUMMARY_MODEL_ID,
    ChatSummaryDraft,
    ChatSummaryError,
    GeminiChatSummarizer,
    render_transcript,
)

MAX_SESSIONS_PER_PET = 5
MAX_QUESTION_CHARS = 2_000
MAX_ASSISTANT_CHARS = 8_000
MAX_COMPLETED_TURNS = 30
MAX_FAILED_TURNS = 30
MAX_TRANSCRIPT_CHARS = 320_000
STALE_PROCESSING_AFTER = timedelta(minutes=5)
_TITLE_MAX = 120


class ChatSessionNotFoundError(Exception):
    pass


class ChatTurnNotFoundError(Exception):
    pass


class PetNotOwnedError(Exception):
    """그 아이의 **구성원이 아닙니다** (docs/co-care.md §2).

    이름은 소유를 말하지만 판정은 대표 ∪ 돌보미입니다 — 라우터·앱이 이 이름으로 404 를
    내고 있어 그대로 둡니다. 판정의 원본은 `chat_repo.get_accessible_pet_id` 입니다.
    """


class AppUserNotActiveError(Exception):
    """The token is still valid but the member is withdrawn or otherwise not active."""


class ActiveDogMismatchError(Exception):
    """The request's ``active_dog_id`` names a different dog than the conversation's pet.

    The conversation is the durable record; a routing hint cannot re-home it.
    """

    def __init__(self, session_pet_id: uuid.UUID) -> None:
        super().__init__(f"active_dog_id differs from the session pet {session_pet_id}")
        self.session_pet_id = session_pet_id


class EmptyConversationError(Exception):
    pass


class ContentLimitError(Exception):
    def __init__(self, field: str, limit: int) -> None:
        super().__init__(f"{field} exceeds {limit} characters")
        self.field = field
        self.limit = limit


class TurnLimitError(Exception):
    pass


class TranscriptLimitError(Exception):
    pass


class CompletionConflictError(Exception):
    pass


class TurnIdempotencyConflictError(Exception):
    """The client reused a ``client_message_id`` for a *different* question.

    Never merged silently: answering the stored question would look like a correct reply to
    the new one, and overwriting the stored one would rewrite history the user already saw.
    """

    def __init__(self, turn_id: uuid.UUID) -> None:
        super().__init__(f"client_message_id reused with different content: {turn_id}")
        self.turn_id = turn_id


class TurnProcessingError(Exception):
    """The same reservation is still being answered; the client must wait, not retry."""

    def __init__(self, turn_id: uuid.UUID) -> None:
        super().__init__(f"turn is already processing: {turn_id}")
        self.turn_id = turn_id


class TurnFailedError(Exception):
    """The retained UUID belongs to a failed turn. Retry with a fresh one."""

    def __init__(self, turn_id: uuid.UUID, error_code: str) -> None:
        super().__init__(f"turn already failed ({error_code}): {turn_id}")
        self.turn_id = turn_id
        self.error_code = error_code


class TurnPersistenceError(Exception):
    """A generated non-failure response could not be committed as a completed turn."""

    def __init__(self, turn_id: uuid.UUID, persistence_error_code: str) -> None:
        super().__init__(f"turn persistence failed ({persistence_error_code}): {turn_id}")
        self.turn_id = turn_id
        self.persistence_error_code = persistence_error_code
        self.retry_with_fresh_client_message_id = True


class ChatSummaryNotFoundError(Exception):
    pass


class ExistingSummaryError(Exception):
    """The same successfully summarized source state already exists."""

    def __init__(self, summary_id: uuid.UUID) -> None:
        super().__init__(f"summary already exists: {summary_id}")
        self.summary_id = summary_id


class SummaryProcessingError(Exception):
    def __init__(self, summary_id: uuid.UUID) -> None:
        super().__init__(f"summary is already processing: {summary_id}")
        self.summary_id = summary_id


class SummaryRequestConflictError(Exception):
    pass


class SummaryPersistenceError(Exception):
    """A generated summary could not be committed as a completed row.

    The member is still active, but the reservation was no longer ``processing`` when the
    completion UPDATE ran — stale recovery already failed it, for example. Withdrawal is not
    a cause: the completion-stage active recheck raises ``AppUserNotActiveError`` (401)
    before this UPDATE is attempted. The generated draft is
    never returned as a success and nothing is re-inserted; the client retries with a fresh
    ``client_request_id``. The persisted-turn twin is ``TurnPersistenceError``.
    """

    def __init__(self, summary_id: uuid.UUID, persistence_error_code: str) -> None:
        super().__init__(
            f"summary persistence failed ({persistence_error_code}): {summary_id}"
        )
        self.summary_id = summary_id
        self.persistence_error_code = persistence_error_code
        self.retry_with_fresh_client_request_id = True


@dataclass(frozen=True)
class SummaryReservation:
    summary_id: uuid.UUID
    transcript: str


def build_title(first_message: str) -> str:
    flattened = " ".join(first_message.split())
    if not flattened:
        return "새 대화"
    if len(flattened) <= _TITLE_MAX:
        return flattened
    return flattened[: _TITLE_MAX - 1] + "…"


def categories_of(response: AssistantResponse) -> list[str]:
    categories: list[str] = []
    for result in response.results:
        if result.status is CapabilityStatus.OK and result.capability.value not in categories:
            categories.append(result.capability.value)
    return categories


def public_response_of(response: AssistantResponse) -> dict[str, object]:
    """Only the already-public response contract; no prompts, exceptions, or provider payloads."""
    return response.model_dump(mode="json")


def candidates_of(turns: list[ChatTurn]) -> list[PriorTurn]:
    """완료 turn 을 Resolver 후보로. `failed`·`processing` 은 애초에 안 들어온다.

    호출자(`run_persisted_turn`)가 `list_recent_completed_turns` 로 이미 오래된 순으로
    돌려준 것을 그대로 옮긴다 — 여기서 순서를 다시 바꾸지 않는다.
    """
    return [
        PriorTurn(turn_id=t.id, user=t.user_content, assistant=t.assistant_content or "")
        for t in turns
    ]


def pending_clarification_of(turns: list[ChatTurn]) -> PendingClarification | None:
    """**가장 최근 완료 turn 이 `CLARIFY` 일 때만** 대기다 (스펙 ⑥ 나).

    뒤에 다른 완료 turn 이 있으면 그 되묻기는 답을 받았거나 버려진 것이고, 어느 쪽이든
    대기가 아니다. 우리 대기는 한 턴짜리라 Place 의 만료·revision 기계가 필요 없다.

    읽는 자리가 `public_response` 인 이유: `public_response_of` 가 `model_dump(mode="json")`
    라 `clarify.question` · `missing` · `missing_axes` 가 통째로 저장돼 있다. 새 칸도 새
    테이블도 필요 없다.

    ⚠ **`public_response["results"]` 를 훑지 않는다.** D-068 이 진리표("CLARIFY = 아무것도
    실행되지 않았음")를 지키려고 되묻기 응답의 `results` 를 **비워서** 내보낸다 — 거기서
    "어느 능력이 돌았나" 를 알아내려 하면 조용히 빈 손이 된다. `clarify` 가 그 답이다.

    DB 에서 막 읽은 JSON 이라 방어적으로 읽는다 — `clarify` 가 없거나 dict 가 아니거나,
    `missing_axes` 에 지금은 없는 축 이름이 섞여 있어도 죽지 않는다(#416 이 경고하는 자리).
    """
    if not turns:
        return None
    last = turns[-1]
    if last.assistant_status != AssistantStatus.CLARIFY.value:
        return None
    clarify = (last.public_response or {}).get("clarify")
    if not isinstance(clarify, dict):
        return None
    question = clarify.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    axes: list[ObservationAxis] = []
    for raw in clarify.get("missing_axes") or []:
        try:
            axes.append(ObservationAxis(raw))
        except ValueError:
            continue  # 목록이 넓어진 뒤의 옛 행 — 축을 모르는 것으로 읽는다
    missing = [m for m in (clarify.get("missing") or []) if isinstance(m, str)]
    return PendingClarification(
        turn_id=last.id,
        question=question,
        missing=missing,
        missing_axes=axes,
        care_log=_pending_care_log(clarify),
    )


def _pending_care_log(clarify: dict) -> CareLogProposal | None:
    """되묻기에 실린 케어 기록 제안 (#331 후속, D-075). 없거나 깨졌으면 None.

    **여기서 조용히 None 이 되는 것이 안전한 방향이다.** 이 값이 있으면 다음 턴의 "네" 가
    DB 에 행을 남기고, None 이면 그 "네" 가 아무 일도 안 한다 — 읽다 실패했을 때 쓰는 쪽으로
    떨어지면 안 된다.

    `missing_axes` 와 같은 방어적 읽기다(그 위 루프). 다만 이유가 한 겹 더 있다: 이 값은
    `chat_turns.public_response` 의 JSON 이라 **옛 행에는 아예 없고**, 계약이 바뀌면 모양이
    다른 행도 남는다. `model_validate` 가 그 둘을 같은 None 으로 만든다.
    """
    raw = clarify.get("care_log")
    if not isinstance(raw, dict):
        return None
    try:
        return CareLogProposal.model_validate(raw)
    except ValidationError:
        return None


async def _require_accessible_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> None:
    if await chat_repo.get_accessible_pet_id(session, app_user_id, pet_id) is None:
        raise PetNotOwnedError


async def _require_owned_session(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> ChatSession:
    found = await chat_repo.get_owned_session(session, app_user_id, session_id)
    if found is None:
        raise ChatSessionNotFoundError
    return found


async def list_sessions(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSession]:
    await _require_accessible_pet(session, app_user_id, pet_id)
    return await chat_repo.list_active_sessions(
        session, app_user_id, pet_id, MAX_SESSIONS_PER_PET
    )


async def create_session(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """Return the one draft for this owner/pet, creating it without a pet row lock."""
    await _require_accessible_pet(session, app_user_id, pet_id)
    existing = await chat_repo.get_draft(session, app_user_id, pet_id)
    if existing is not None:
        return existing

    draft = chat_repo.add_session(
        session,
        ChatSession(
            app_user_id=app_user_id,
            pet_id=pet_id,
            title=build_title(title or ""),
            agent_categories=[],
            last_message_at=None,
        ),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.get_draft(session, app_user_id, pet_id)
        if winner is None:
            raise
        return winner
    return draft


async def get_session_with_turns(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[ChatSession, list[ChatTurn]]:
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    return chat_session, await chat_repo.list_turns(session, session_id)


async def delete_session(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    await chat_repo.delete_session(session, chat_session)
    await session.commit()


def _validate_question(question: str) -> None:
    if not question:
        raise ContentLimitError("question", MAX_QUESTION_CHARS)
    if len(question) > MAX_QUESTION_CHARS:
        raise ContentLimitError("question", MAX_QUESTION_CHARS)


def _resolve_existing_turn(existing: ChatTurn, question: str) -> ChatTurn:
    """Decide what a reused ``client_message_id`` means.

    Only an exact replay of a completed turn is returned, so the caller can answer from the
    stored ``public_response`` without calling the orchestrator. Everything else raises: a
    different question is a client bug, a live reservation is still being answered, and a
    retained failed or stale rows stay burned. Once bounded retention prunes a failed row,
    that UUID no longer has a reservation to conflict with.
    """
    if existing.user_content != question:
        raise TurnIdempotencyConflictError(existing.id)
    if existing.processing_status == "completed":
        return existing
    if existing.processing_status == "processing":
        raise TurnProcessingError(existing.id)
    raise TurnFailedError(existing.id, existing.error_code or "FAILED")


async def reserve_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_message_id: uuid.UUID,
    question: str,
    now: datetime | None = None,
) -> ChatTurn:
    """Reserve a turn and commit before any orchestrator call.

    Returns the new ``processing`` row, or the existing ``completed`` row for an exact replay
    (same UUID, same question). See ``_resolve_existing_turn`` for every other reuse.
    """
    _validate_question(question)
    chat_session = await chat_repo.get_owned_session_for_update(
        session, app_user_id, session_id
    )
    if chat_session is None:
        raise ChatSessionNotFoundError

    # Stale recovery and failed-row retention happen under the session lock. Commit them on
    # their own so cleanup survives whatever reservation check below raises, then relock.
    cutoff = (now or datetime.now(UTC)) - STALE_PROCESSING_AFTER
    stale_count = await chat_repo.fail_stale_turns(
        session, session_id=session_id, cutoff=cutoff
    )
    pruned_count = await chat_repo.prune_failed_turns(
        session, session_id=session_id, keep=MAX_FAILED_TURNS
    )
    if stale_count or pruned_count:
        await session.commit()
        chat_session = await chat_repo.get_owned_session_for_update(
            session, app_user_id, session_id
        )
        if chat_session is None:
            raise ChatSessionNotFoundError

    existing = await chat_repo.get_turn_by_client_id(
        session, session_id, client_message_id
    )
    if existing is not None:
        return _resolve_existing_turn(existing, question)
    capacity_turns = await chat_repo.list_capacity_turns(session, session_id)
    if len(capacity_turns) >= MAX_COMPLETED_TURNS:
        raise TurnLimitError

    projected = _reserved_transcript_char_count(capacity_turns)
    projected += len(question) + MAX_ASSISTANT_CHARS
    if projected > MAX_TRANSCRIPT_CHARS:
        raise TranscriptLimitError

    turn = chat_repo.add_turn(
        session,
        ChatTurn(
            session_id=session_id,
            client_message_id=client_message_id,
            processing_status="processing",
            user_content=question,
            agent_categories=[],
        ),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.get_turn_by_client_id(
            session, session_id, client_message_id
        )
        if winner is None:
            raise
        return _resolve_existing_turn(winner, question)
    return turn


async def complete_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    response: AssistantResponse,
) -> ChatTurn:
    """Conditionally complete a reserved turn and activate/prune in one short TX."""
    owned = await chat_repo.get_owned_turn(session, app_user_id, turn_id)
    if owned is None:
        raise ChatTurnNotFoundError
    turn, chat_session = owned

    # Every completion updates session aggregates (last_message_at and the category
    # union) and checks the shared transcript limit. Serialize those short writes per
    # session; otherwise two different turns can both pass an outdated size check and
    # overwrite each other's category update. populate_existing in the repository also
    # refreshes a row that waited behind another completion.
    locked_session = await chat_repo.get_owned_session_for_update(
        session, app_user_id, chat_session.id
    )
    if locked_session is None:
        raise ChatSessionNotFoundError
    chat_session = locked_session

    if turn.processing_status != "processing":
        raise CompletionConflictError
    if not response.message or len(response.message) > MAX_ASSISTANT_CHARS:
        failed = await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="ASSISTANT_CONTENT_TOO_LONG"
        )
        if failed is None:
            await session.rollback()
            raise CompletionConflictError
        await chat_repo.prune_failed_turns(
            session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
        )
        await session.commit()
        raise ContentLimitError("assistant", MAX_ASSISTANT_CHARS)

    completed = await chat_repo.list_turns(
        session, chat_session.id, completed_only=True
    )
    if _transcript_char_count(completed) + len(turn.user_content) + len(
        response.message
    ) > MAX_TRANSCRIPT_CHARS:
        failed = await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="TRANSCRIPT_TOO_LONG"
        )
        if failed is None:
            await session.rollback()
            raise CompletionConflictError
        await chat_repo.prune_failed_turns(
            session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
        )
        await session.commit()
        raise TranscriptLimitError

    first_activation = chat_session.last_message_at is None
    if first_activation and (
        await chat_repo.lock_accessible_pet(
            session, chat_session.app_user_id, chat_session.pet_id
        )
        is None
    ):
        raise PetNotOwnedError

    categories = categories_of(response)
    stored = await chat_repo.complete_turn_if_processing(
        session,
        turn_id,
        assistant_content=response.message,
        assistant_status=response.status.value,
        request_id=response.request_id,
        agent_categories=categories,
        public_response=public_response_of(response),
    )
    if stored is None:
        await session.rollback()
        raise CompletionConflictError

    merged = list(chat_session.agent_categories)
    for category in categories:
        if category not in merged:
            merged.append(category)
    chat_repo.touch_active_session(session, chat_session, merged)
    if chat_session.title == "새 대화":
        chat_session.title = build_title(turn.user_content)

    if first_activation:
        for stale in await chat_repo.oldest_active_sessions_beyond(
            session,
            chat_session.app_user_id,
            chat_session.pet_id,
            MAX_SESSIONS_PER_PET,
        ):
            await chat_repo.delete_session(session, stale)

    await session.commit()
    return stored


async def fail_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    error_code: str,
) -> ChatTurn:
    owned = await chat_repo.get_owned_turn(session, app_user_id, turn_id)
    if owned is None:
        raise ChatTurnNotFoundError
    turn, chat_session = owned
    if await chat_repo.get_owned_session_for_update(
        session, app_user_id, chat_session.id
    ) is None:
        raise ChatSessionNotFoundError
    if turn.processing_status != "processing":
        raise CompletionConflictError
    failed = await chat_repo.fail_turn_if_processing(
        session, turn_id, error_code=error_code
    )
    if failed is None:
        raise CompletionConflictError
    await chat_repo.prune_failed_turns(
        session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
    )
    await session.commit()
    return failed


def _transcript_char_count(turns: list[ChatTurn]) -> int:
    return sum(len(turn.user_content) + len(turn.assistant_content or "") for turn in turns)


def _reserved_transcript_char_count(turns: list[ChatTurn]) -> int:
    """Committed content plus worst-case answers for every live reservation."""
    return sum(
        len(turn.user_content)
        + (
            len(turn.assistant_content or "")
            if turn.processing_status == "completed"
            else MAX_ASSISTANT_CHARS
        )
        for turn in turns
    )


#: The external call. Receives the session's pet id (as the ``active_dog_id`` the
#: orchestrator should see), the oldest-first candidate turns for the Turn Resolver, and
#: any unresolved clarification, and returns what the user will be shown.
Orchestrate = Callable[
    [str, list[PriorTurn], PendingClarification | None], Awaitable[AssistantResponse]
]

#: Expected completion-stage failures that must become an explicit API error. A generated
#: non-FAILED response is never returned as HTTP 200 unless its identical public response
#: has been committed in a completed turn.
_PERSISTENCE_FAILURES = (
    ContentLimitError,
    TranscriptLimitError,
    CompletionConflictError,
    ChatTurnNotFoundError,
    ChatSessionNotFoundError,
    PetNotOwnedError,
)


def _persistence_error_code(error: Exception) -> str:
    if isinstance(error, ContentLimitError):
        return "ASSISTANT_CONTENT_TOO_LONG"
    if isinstance(error, TranscriptLimitError):
        return "TRANSCRIPT_TOO_LONG"
    if isinstance(error, CompletionConflictError):
        return "COMPLETION_CONFLICT"
    if isinstance(error, ChatTurnNotFoundError):
        return "TURN_NOT_FOUND"
    if isinstance(error, ChatSessionNotFoundError):
        return "SESSION_NOT_FOUND"
    if isinstance(error, PetNotOwnedError):
        return "PET_NOT_OWNED"
    raise TypeError(f"unexpected persistence error type: {type(error).__name__}")


async def _close_failed(
    session: AsyncSession, app_user_id: uuid.UUID, turn_id: uuid.UUID, error_code: str
) -> None:
    try:
        await fail_turn(session, app_user_id, turn_id, error_code=error_code)
    except (CompletionConflictError, ChatTurnNotFoundError):
        # Stale recovery or a concurrent writer already closed the row. Never overwrite.
        pass


async def run_persisted_turn(
    session_factory: async_sessionmaker[AsyncSession],
    app_user_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    client_message_id: uuid.UUID,
    question: str,
    active_dog_id: str | None,
    orchestrate: Orchestrate,
) -> AssistantResponse:
    """``reserve TX -> close AsyncSession -> orchestrate -> completion/failure TX``.

    No DB session or row lock is alive while ``orchestrate`` runs. An exact replay of a
    completed turn answers from the stored ``public_response`` and never calls it. The
    session's pet is authoritative: a conflicting ``active_dog_id`` is refused before any
    row is written, and the orchestrator always sees the session's pet.
    """
    _validate_question(question)

    async with session_factory() as reserve_session:
        # ``admin_or_app_user`` trusts the token alone. This path filters by identity, so it
        # repeats the check ``current_app_user`` makes for every other app-owned API: the
        # member must still be active, and the FOR UPDATE serializes with withdrawal.
        if await app_user_repo.get_active_for_update(reserve_session, app_user_id) is None:
            raise AppUserNotActiveError
        chat_session = await _require_owned_session(reserve_session, app_user_id, session_id)
        pet_id = str(chat_session.pet_id)
        if active_dog_id is not None and active_dog_id != pet_id:
            raise ActiveDogMismatchError(chat_session.pet_id)
        turn = await reserve_turn(
            reserve_session,
            app_user_id,
            session_id,
            client_message_id=client_message_id,
            question=question,
        )
        if turn.processing_status == "completed":
            return AssistantResponse.model_validate(turn.public_response)
        turn_id = turn.id

        # 후보와 대기 되묻기는 **여기서** 읽는다 — 아직 예약 TX 가 열려 있는 동안이다.
        # `orchestrate` 콜백 안에서 늦게 읽으면 그때는 세션이 이미 닫혀 있다(D-048:
        # 외부 모델 호출 동안 열린 세션·행 잠금 0개). `list_recent_completed_turns` 는
        # `processing_status == 'completed'` 만 보므로, 방금 예약해 `processing` 인 이
        # turn 자신은 여기 안 걸린다.
        recent_turns = await chat_repo.list_recent_completed_turns(
            reserve_session, session_id, limit=MAX_CANDIDATE_PAIRS
        )
        candidates = candidates_of(recent_turns)
        pending = pending_clarification_of(recent_turns)

    try:
        response = await orchestrate(pet_id, candidates, pending)
    except Exception:
        async with session_factory() as failure_session:
            await _close_failed(failure_session, app_user_id, turn_id, "ORCHESTRATION_FAILED")
        raise

    if response.status is AssistantStatus.FAILED:
        # The orchestrator could not answer (router/provider failure). That is a provider
        # failure with a polite message, not a delivered answer: keep the draft a draft.
        async with session_factory() as failure_session:
            await _close_failed(failure_session, app_user_id, turn_id, "ASSISTANT_FAILED")
        return response

    async with session_factory() as completion_session:
        try:
            await complete_turn(completion_session, app_user_id, turn_id, response=response)
        except _PERSISTENCE_FAILURES as exc:
            raise TurnPersistenceError(turn_id, _persistence_error_code(exc)) from exc
    return response


def _render_completed_turns(turns: list[ChatTurn]) -> str:
    return render_transcript(
        [(turn.user_content, turn.assistant_content or "") for turn in turns]
    )


def _raise_for_existing_summary(summary: ChatSummary) -> None:
    if summary.processing_status == "completed":
        raise ExistingSummaryError(summary.id)
    if summary.processing_status == "processing":
        raise SummaryProcessingError(summary.id)
    raise SummaryRequestConflictError


async def reserve_summary(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_request_id: uuid.UUID,
    now: datetime | None = None,
) -> SummaryReservation:
    """Reserve and commit. The caller must close this AsyncSession before Gemini."""
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    turns = await chat_repo.list_turns(session, session_id, completed_only=True)
    if not turns:
        raise EmptyConversationError
    if len(turns) > MAX_COMPLETED_TURNS:
        raise TurnLimitError
    # The limit counts stored characters, the same way turn reservation and completion count
    # them, so a conversation that was allowed to grow can always be summarized. The JSON
    # envelope's overhead is covered by the byte-based Gemini input cap instead.
    if _transcript_char_count(turns) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptLimitError
    transcript = _render_completed_turns(turns)

    timestamp = now or datetime.now(UTC)
    stale_count = await chat_repo.fail_stale_summaries(
        session,
        source_session_id=session_id,
        cutoff=timestamp - STALE_PROCESSING_AFTER,
    )

    by_request = await chat_repo.summary_by_request_id(
        session, app_user_id, client_request_id
    )
    if by_request is not None:
        if stale_count:
            await session.commit()
        _raise_for_existing_summary(by_request)

    source_count = len(turns)
    existing = await chat_repo.active_summary_for_source(
        session, session_id, source_count
    )
    if existing is not None:
        _raise_for_existing_summary(existing)

    summary = chat_repo.add_summary(
        session,
        ChatSummary(
            app_user_id=app_user_id,
            pet_id=chat_session.pet_id,
            source_session_id=session_id,
            source_turn_count=source_count,
            client_request_id=client_request_id,
            processing_status="processing",
            agent_categories=list(chat_session.agent_categories),
            processing_started_at=timestamp,
        ),
    )
    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.active_summary_for_source(
            session, session_id, source_count
        )
        if winner is not None:
            _raise_for_existing_summary(winner)
        by_request = await chat_repo.summary_by_request_id(
            session, app_user_id, client_request_id
        )
        if by_request is not None:
            _raise_for_existing_summary(by_request)
        raise
    return SummaryReservation(summary_id=summary.id, transcript=transcript)


async def complete_summary(
    session: AsyncSession, summary_id: uuid.UUID, draft: ChatSummaryDraft
) -> ChatSummary:
    citations = [citation.model_dump(mode="json") for citation in draft.source_citations]
    completed = await chat_repo.complete_summary_if_processing(
        session,
        summary_id,
        title=draft.title,
        question_summary=draft.question_summary,
        answer_summary=draft.answer_summary,
        key_points=draft.key_points,
        cautions=draft.cautions,
        source_citations=citations,
        model=SUMMARY_MODEL_ID,
        prompt_version=PROMPT_VERSION,
    )
    if completed is None:
        await session.rollback()
        raise CompletionConflictError
    await session.commit()
    return completed


async def fail_summary(
    session: AsyncSession, summary_id: uuid.UUID, *, error_code: str
) -> None:
    failed = await chat_repo.fail_summary_if_processing(
        session, summary_id, error_code=error_code
    )
    if failed is not None:
        await session.commit()
    else:
        await session.rollback()


async def _require_active_member(session: AsyncSession, app_user_id: uuid.UUID) -> None:
    """Recheck membership inside a service-owned TX, holding the withdrawal lock.

    The summary route authenticates with a token-only dependency (no request session), so
    the member's ``active`` state is verified here, in the same short transaction that
    writes. The ``FOR UPDATE`` serializes with withdrawal exactly like ``current_app_user``
    does for request-scoped endpoints: withdrawal holds this row for its whole transaction,
    so either it finished first and no active row is found, or our write commits first and
    withdrawal's explicit cleanup deletes it afterwards.

    Holding the lock in *this* transaction is also what removes the self-deadlock: the
    ``chat_summaries`` INSERT's FK takes ``FOR KEY SHARE`` on the same ``app_users`` row,
    which a transaction already holding ``FOR UPDATE`` on it does not wait for.
    """
    if await app_user_repo.get_active_for_update(session, app_user_id) is None:
        raise AppUserNotActiveError


async def create_summary(
    session_factory: async_sessionmaker[AsyncSession],
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_request_id: uuid.UUID,
    summarizer: GeminiChatSummarizer | None = None,
) -> ChatSummary:
    """``active check + reservation TX -> close AsyncSession -> summarizer -> completion/failure TX``.

    The caller must not hold a request-scoped session or any ``app_users`` lock: the
    reservation transaction takes that lock itself, reserves the summary under it, and
    commits and closes before the summarizer runs. No AsyncSession and no row lock is
    alive during the external call.

    If withdrawal commits between reservation and completion, its cleanup has already
    deleted the reservation. The completion TX rechecks membership first (401, like every
    other withdrawn-member write) and otherwise updates only a row that is still
    ``processing``; a vanished or already-closed row is ``SummaryPersistenceError``. Nothing
    is ever re-inserted and 201 is returned only after the completed row is committed.
    """
    async with session_factory() as reserve_session:
        await _require_active_member(reserve_session, app_user_id)
        reservation = await reserve_summary(
            reserve_session,
            app_user_id,
            session_id,
            client_request_id=client_request_id,
        )

    try:
        draft = await (summarizer or GeminiChatSummarizer()).summarize(
            transcript=reservation.transcript
        )
    except ChatSummaryError:
        async with session_factory() as failure_session:
            # Conditional UPDATE only. If withdrawal already deleted the reservation this
            # touches nothing; the provider failure is still reported as such.
            await fail_summary(
                failure_session,
                reservation.summary_id,
                error_code="SUMMARY_GENERATION_FAILED",
            )
        raise

    async with session_factory() as completion_session:
        # Withdrawal serialization again: a member who withdrew while the summarizer ran
        # gets the withdrawn-member 401, and their (already deleted) reservation is left
        # alone rather than recreated or completed.
        await _require_active_member(completion_session, app_user_id)
        try:
            return await complete_summary(
                completion_session, reservation.summary_id, draft
            )
        except CompletionConflictError as exc:
            raise SummaryPersistenceError(
                reservation.summary_id, "COMPLETION_CONFLICT"
            ) from exc


async def list_summaries(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSummary]:
    await _require_accessible_pet(session, app_user_id, pet_id)
    return await chat_repo.list_completed_summaries(session, app_user_id, pet_id)


async def delete_summary(
    session: AsyncSession, app_user_id: uuid.UUID, summary_id: uuid.UUID
) -> None:
    """Delete one completed summary. The source conversation is untouched.

    A failed reservation is invisible to the user (only completed rows are listed), so it is
    reported as not found rather than deleted. A ``processing`` one is refused: deleting it
    would leave the in-flight completion UPDATE with no row, and the caller would then see a
    502 for a summary the user "already removed".
    """
    summary = await chat_repo.get_owned_summary(session, app_user_id, summary_id)
    if summary is None or summary.processing_status == "failed":
        raise ChatSummaryNotFoundError
    if summary.processing_status == "processing":
        raise SummaryProcessingError(summary.id)
    await chat_repo.delete_summary(session, summary)
    await session.commit()


__all__ = [
    "MAX_ASSISTANT_CHARS",
    "MAX_COMPLETED_TURNS",
    "MAX_FAILED_TURNS",
    "MAX_QUESTION_CHARS",
    "MAX_SESSIONS_PER_PET",
    "MAX_TRANSCRIPT_CHARS",
    "STALE_PROCESSING_AFTER",
    "ActiveDogMismatchError",
    "AppUserNotActiveError",
    "ChatSessionNotFoundError",
    "ChatSummaryNotFoundError",
    "ChatTurnNotFoundError",
    "CompletionConflictError",
    "ContentLimitError",
    "EmptyConversationError",
    "ExistingSummaryError",
    "Orchestrate",
    "PetNotOwnedError",
    "SummaryPersistenceError",
    "SummaryProcessingError",
    "SummaryRequestConflictError",
    "SummaryReservation",
    "TranscriptLimitError",
    "TurnFailedError",
    "TurnIdempotencyConflictError",
    "TurnLimitError",
    "TurnPersistenceError",
    "TurnProcessingError",
    "build_title",
    "candidates_of",
    "categories_of",
    "complete_summary",
    "complete_turn",
    "create_session",
    "create_summary",
    "delete_session",
    "delete_summary",
    "fail_summary",
    "fail_turn",
    "get_session_with_turns",
    "list_sessions",
    "list_summaries",
    "pending_clarification_of",
    "public_response_of",
    "reserve_summary",
    "reserve_turn",
    "run_persisted_turn",
]
